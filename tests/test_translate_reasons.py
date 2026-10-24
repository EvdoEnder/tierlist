"""English reasons: written by the scorer, translated when it left one out,
and shown on the English pages, with the Chinese line as the fallback.

No network: the translation call runs against httpx.MockTransport, and the
store is two monkeypatched functions.
"""
from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
from conftest import item
from test_editions import ARCHIVE, SUNDAY, _board, _row

from intel import translate_reasons
from intel.editions import build_aggregate_context
from intel.i18n import reason_in
from intel.score import llm
from intel.score.llm import ScoredItem, parse_response, parse_translations
from intel.store import db


def _scored(item_id, reason="中文理由", reason_en=""):
    return ScoredItem(item_id=item_id, score=70, dims={}, reason=reason,
                      verdict="watch", reason_en=reason_en)


def _reply(results):
    content = json.dumps({"results": results})
    return {"choices": [{"message": {"content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


# ------------------------------------------------------------- the scorer


def test_the_scorer_keeps_the_english_reason(rubric):
    dims = {"novelty": 20, "utility": 20, "momentum": 20, "commercial": 20}
    rows = [{"id": "github:a/b", "dims": dims, "reason": "好东西",
             "reason_en": "  A good thing.  ", "verdict": "watch"},
            {"id": "github:c/d", "dims": dims, "reason": "也行",
             "verdict": "skip"}]
    got = parse_response(json.dumps({"results": rows}),
                         [item("github:a/b"), item("github:c/d")], rubric)
    assert [(s.reason, s.reason_en) for s in got] == [
        ("好东西", "A good thing."), ("也行", "")]


def test_the_scoring_prompt_asks_for_english(rubric):
    assert '"reason_en"' in llm.build_system_prompt(rubric)


# ------------------------------------------------------- the translation


def test_translations_attach_only_to_their_own_row():
    content = json.dumps({"results": [
        {"k": "0", "en": "first"}, {"k": 1, "en": "second"},
        {"k": "1", "en": "a repeat"}, {"k": "7", "en": "out of range"},
        {"k": "x", "en": "no key"}, {"k": "2", "en": "  "}]})
    assert parse_translations(content, 3) == {0: "first", 1: "second"}
    assert parse_translations("not json", 3) == {}


@pytest.mark.real_translate
@pytest.mark.anyio
async def test_one_request_per_batch_with_thinking_off(monkeypatch,
                                                        anyio_backend):
    monkeypatch.setenv("TIERLIST_LLM_API_KEY", "k")
    monkeypatch.delenv("TIERLIST_LLM_BASE_URL", raising=False)
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=_reply([{"k": "1", "en": "Two."}]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        got, usage = await llm.translate_to_english(["一\n行", "二"], client=c)

    assert got == {1: "Two."}
    assert usage["completion_tokens"] == 5
    (body,) = sent
    assert "reasoning_effort" not in body
    assert body["thinking"] == {"type": "disabled"}
    assert body["messages"][1]["content"] == "0\t一 行\n1\t二"


@pytest.mark.anyio
async def test_fill_missing_translates_only_the_gaps(monkeypatch,
                                                      anyio_backend):
    asked = []

    async def fake(texts, *, client=None):
        asked.append(list(texts))
        return {0: "Filled."}, {"completion_tokens": 3}

    monkeypatch.setattr(llm, "translate_to_english", fake)
    scores = [_scored("a", reason_en="Already."), _scored("b"),
              _scored("c", reason="")]

    out, usage = await translate_reasons.fill_missing(scores)

    assert asked == [["中文理由"]]
    assert [s.reason_en for s in out] == ["Already.", "Filled.", ""]
    assert usage == {"completion_tokens": 3}


@pytest.mark.anyio
async def test_a_failed_translation_does_not_stop_the_run(monkeypatch,
                                                          anyio_backend):
    async def boom(texts, *, client=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(llm, "translate_to_english", boom)
    scores = [_scored("a")]

    out, usage = await translate_reasons.fill_missing(scores)

    assert out == scores and usage == {}


class _Conn:
    async def commit(self):
        pass


@pytest.mark.anyio
@pytest.mark.parametrize("dry_run", [False, True])
async def test_the_store_pass_writes_what_came_back(monkeypatch, dry_run,
                                                     anyio_backend):
    day = date(2026, 10, 1)
    queue = [{"item_id": f"github:x/{i}", "snap_date": day,
              "reason": f"理由{i}"} for i in range(3)]
    written = []

    async def rows(conn, *, limit=None):
        return queue[:limit]

    async def write(conn, triples):
        written.extend(triples)

    async def fake(texts, *, client=None):
        return {0: "Zero.", 2: "Two."}, {}

    monkeypatch.setattr(db, "reasons_without_english", rows)
    monkeypatch.setattr(db, "set_reasons_en", write)
    monkeypatch.setattr(llm, "translate_to_english", fake)

    n = await translate_reasons.backfill(_Conn(), batch=3, dry_run=dry_run)

    if dry_run:
        assert n == 0 and written == []
    else:
        assert n == 2
        assert written == [("Zero.", "github:x/0", day),
                           ("Two.", "github:x/2", day)]


# ------------------------------------------------------------- the pages


def test_english_pages_prefer_english_and_never_go_blank():
    assert reason_in("en", "中文", "English.") == "English."
    assert reason_in("en", "中文", None) == "中文"
    assert reason_in("en", "中文", "") == "中文"
    assert reason_in("zh", "中文", "English.") == "中文"
    assert reason_in("en", None, None) == ""


@pytest.mark.parametrize("lang, want", [("zh", "why a/b"), ("en", "Why.")])
def test_issue_entries_carry_the_page_language(lang, want):
    row = {**_row("a/b", 80), "reason_en": "Why."}
    ctx = build_aggregate_context(
        "weekly", "dev_tools", SUNDAY, rows=[row],
        stats={"new_count": 1, "scored_count": 1}, gain_board=_board(),
        archive_recent=ARCHIVE, lang=lang)
    assert [e["reason"] for e in ctx["entries"]] == [want]


@pytest.mark.real_translate
@pytest.mark.anyio
async def test_descriptions_use_their_own_prompt(monkeypatch, anyio_backend):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": '{"results": [{"k": "0", "en": "A tool"}]}'}}],
            "usage": {}})

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    got, _ = await llm.translate_to_english(
        ["一个工具"], client=client, system=llm.DESCRIBE_SYSTEM)
    assert got == {0: "A tool"}
    assert seen["body"]["messages"][0]["content"] == llm.DESCRIBE_SYSTEM
    assert seen["body"]["thinking"] == {"type": "disabled"}
