"""The scorer's pure half: weighted-total math and strict JSON parsing.

The network call itself is not exercised here — the contract under test is
what the model's text must look like and what happens to every malformed
variant of it.
"""
from __future__ import annotations

import json

import pytest
from conftest import NOW, item

from intel.score.llm import (  # noqa: F401
    build_body,
    build_system_prompt,
    build_tag_user_prompt,
    build_user_prompt,
    clean_tags,
    llm_api_key,
    llm_endpoint,
    parse_response,
    parse_tag_response,
    score_candidates,
    tag_candidates,
    weighted_total,
)


def _dims(novelty=20, utility=20, momentum=20, commercial=20):
    return {"novelty": novelty, "utility": utility,
            "momentum": momentum, "commercial": commercial}


def test_perfect_dims_score_100():
    assert weighted_total(_dims(25, 25, 25, 25),
                          {"novelty": 0.8, "utility": 1.0,
                           "momentum": 1.2, "commercial": 1.5}) == 100


def test_commercial_weight_dominates_the_total():
    # Same mass placed on commercial vs novelty must move the total —
    # the frozen decision §11 #4 is a taste, and this is where it bites.
    weights = {"novelty": 0.8, "utility": 1.0,
               "momentum": 1.2, "commercial": 1.5}
    all_on_commercial = weighted_total(
        _dims(commercial=25, novelty=0, utility=0, momentum=0), weights)
    all_on_novelty = weighted_total(
        _dims(commercial=0, novelty=25, utility=0, momentum=0), weights)
    assert all_on_commercial > all_on_novelty
    assert all_on_novelty == round(25 * 0.8 / (25 * 4.5) * 100)


def _payload(rows):
    return json.dumps({"results": rows})


def test_parse_happy_path(rubric):
    cands = [item("github:a/b"), item("github:c/d")]
    rows = [{"id": "github:a/b", "dims": _dims(), "reason": "好东西",
             "verdict": "watch", "tags": ["mcp", "逆向"]},
            {"id": "github:c/d", "dims": _dims(0, 0, 0, 0), "reason": "wrapper",
             "verdict": "skip"}]
    scored = parse_response(_payload(rows), cands, rubric)
    assert [s.item_id for s in scored] == ["github:a/b", "github:c/d"]
    assert scored[0].score == weighted_total(_dims(), rubric.weights)
    assert scored[0].verdict == "watch"
    assert scored[0].reason == "好东西"
    # 逆向 resolves through the alias table onto the reverse-engineering
    # slug; the model's own ordering is kept among topics.
    assert scored[0].tags == ["mcp", "reverse-engineering"]
    assert scored[1].tags == []          # missing tags degrade, never void


def test_parse_tags_resolve_against_the_vocabulary(rubric):
    cands = [item("github:a/b")]
    row = {"id": "github:a/b", "dims": _dims(), "reason": "r",
           "tags": ["#MCP", "  逆向 ", "LLM", "mcp", "ai", "video", "game",
                    "ffmpeg", "x264"]}
    scored = parse_response(_payload([row]), cands, rubric)
    # '#' dropped, ASCII lowercased, 逆向 aliased, `mcp` deduped. Topics cap
    # at 5 in the model's own order, so `game` never lands; entities cap at
    # 2 and are appended after the topics.
    assert scored[0].tags == ["mcp", "reverse-engineering", "llm", "ai",
                              "video", "ffmpeg", "x264"]


def test_parse_tags_collapse_a_bilingual_pair(rubric):
    """All 17 `crack` items also carried `破解`, so the same concept owned two
    tag pages and burned two of the six slots."""
    cands = [item("github:a/b")]
    row = {"id": "github:a/b", "dims": _dims(), "reason": "r",
           "tags": ["crack", "破解", "游戏外挂", "cheat"]}
    scored = parse_response(_payload([row]), cands, rubric)
    assert scored[0].tags == ["crack", "game-cheat"]


def test_parse_tags_drop_a_programming_language(rubric):
    """Every entry already prints the repo's language next to its stars, so a
    `rust` tag is a second copy of a field the reader is looking at."""
    cands = [item("github:a/b")]
    row = {"id": "github:a/b", "dims": _dims(), "reason": "r",
           "tags": ["cli", "rust"]}
    scored = parse_response(_payload([row]), cands, rubric)
    assert scored[0].tags[0] == "cli"


def test_clean_tags_rejects_malformed_payloads():
    for raw in [None, 7, "tags", [1, 2], ["", "   "], [["nested"]],
                ["#"]]:  # '#' alone strips to nothing
        assert clean_tags(raw) == []
    assert clean_tags(["a", "b"]) == ["a", "b"]


def test_system_prompt_hands_over_the_whole_vocabulary(rubric):
    """The model can only pick from a list it was shown. If the prompt and
    the normalizer read different lists, every tag the model chooses is
    dropped downstream and the site simply stops having tags."""
    from intel.tags import vocabulary

    system = build_system_prompt(rubric)
    assert '"tags"' in system
    for slug in vocabulary().topics:
        assert f"{slug} = " in system
    assert "不要自己造词" in system
    assert "不要输出编程语言" in system


def test_parse_rejects_each_malformation(rubric):
    cands = [item("github:a/b")]
    good = _good_row()
    bad_variants = [
        {**good, "id": "github:unknown/x"},          # id we never sent
        {**good, "dims": {**_dims(), "utility": "high"}},   # non-int dim
        {**good, "dims": {**_dims(), "momentum": 26}},      # out of range
        {**good, "dims": {k: v for k, v in _dims().items()
                          if k != "commercial"}},           # missing dim
        {k: v for k, v in good.items() if k != "dims"},     # dims absent
    ]
    for variant in bad_variants:
        assert parse_response(_payload([variant]), cands, rubric) == [], variant


def test_parse_records_a_reason_for_every_drop(rubric):
    cands = [item("github:a/b")]
    problems: list[str] = []
    for variant in [
        {**_good_row(), "id": "github:unknown/x"},          # id we never sent
        {**_good_row(), "dims": {**_dims(), "utility": "high"}},  # non-int
        {**_good_row(), "dims": {k: v for k, v in _dims().items()
                                 if k != "commercial"}},    # missing dim
    ]:
        assert parse_response(_payload([variant]), cands, rubric,
                              problems) == []
    assert len(problems) == 3


def test_parse_resolves_bare_repo_name_to_source_prefix(rubric):
    """Regression (2026-08-25): the model returned 'a/b' instead of
    'github:a/b' for all 26 rows and strict parsing dropped every one —
    a paid batch voided by a formatting mutation. Bare names must resolve."""
    cands = [item("github:a/b"), item("github:c/d")]
    row = {**_good_row(), "id": "a/b"}
    scored = parse_response(_payload([row]), cands, rubric)
    assert len(scored) == 1 and scored[0].item_id == "github:a/b"
    # unrelated bare name still rejected
    row2 = {**_good_row(), "id": "zzz/unknown"}
    assert parse_response(_payload([row2]), cands, rubric) == []


def test_tag_response_also_resolves_bare_names():
    from intel.score.llm import parse_tag_response

    known = {"github:a/b"}
    assert parse_tag_response('{"tags": [{"id": "a/b", "tags": ["mcp"]}]}',
                              known) == {"github:a/b": ["mcp"]}
    assert parse_tag_response('{"tags": [{"id": "other/x", "tags": ["m"]}]}',
                              known) == {}


def _good_row() -> dict:
    return {"id": "github:a/b", "dims": _dims(), "reason": "r",
            "verdict": "watch"}


def test_missing_or_garbled_verdict_degrades_to_skip(rubric):
    cands = [item("github:a/b")]
    for row in [{"id": "github:a/b", "dims": _dims(), "reason": "r"},
                {"id": "github:a/b", "dims": _dims(), "reason": "r",
                 "verdict": "maybe"}]:
        scored = parse_response(_payload([row]), cands, rubric)
        assert len(scored) == 1 and scored[0].verdict == "skip", row


def test_parse_drops_duplicates_and_garbage_but_keeps_the_rest(rubric):
    cands = [item("github:a/b"), item("github:c/d")]
    rows = ["junk", {"no": "shape"},
            {"id": "github:a/b", "dims": _dims(), "reason": "first",
             "verdict": "watch"},
            {"id": "github:a/b", "dims": _dims(10), "reason": "dupe",
             "verdict": "skip"},
            {"id": "github:c/d", "dims": _dims(5), "reason": "second",
             "verdict": "watch"}]
    scored = parse_response(_payload(rows), cands, rubric)
    assert [(s.item_id, s.reason) for s in scored] == [
        ("github:a/b", "first"), ("github:c/d", "second")]


def test_parse_survives_non_json_and_wrong_shapes(rubric):
    for content in ["not json at all", '"just a string"',
                    json.dumps({"results": 7}), json.dumps([1, 2])]:
        assert parse_response(content, [], rubric) == []


def test_prompts_name_every_candidate_and_dimension(rubric):
    cands = [item("github:a/b"), item("github:c/d")]
    user = build_user_prompt(cands, NOW)
    system = build_system_prompt(rubric)
    for needle in ("github:a/b", "github:c/d", "age_days="):
        assert needle in user
    for name in rubric.dimensions:
        assert name in system
    assert "不要输出总分" in system


def test_endpoint_defaults_to_deepseek_and_follows_the_base_url(monkeypatch):
    monkeypatch.delenv("TIERLIST_LLM_BASE_URL", raising=False)
    assert llm_endpoint() == "https://api.deepseek.com/chat/completions"
    monkeypatch.setenv("TIERLIST_LLM_BASE_URL",
                       "https://gw.example.com/v1/")  # trailing slash eaten
    assert llm_endpoint() == "https://gw.example.com/v1/chat/completions"


def test_gateway_key_wins_over_the_deepseek_name(monkeypatch):
    monkeypatch.delenv("TIERLIST_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert llm_api_key() == ""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "native")
    assert llm_api_key() == "native"
    monkeypatch.setenv("TIERLIST_LLM_API_KEY", "gateway")
    assert llm_api_key() == "gateway"


def test_opencode_gateway_gets_a_session_header_and_others_get_none():
    from intel.score.llm import gateway_headers
    assert gateway_headers("https://api.deepseek.com/chat/completions") == {}
    assert gateway_headers(
        "https://opencode.ai/zen/go/v1/chat/completions",
    ) == {"x-opencode-session": "tierlist"}


def test_every_endpoint_gets_high_effort_thinking_and_no_off_switch():
    """One body for DeepSeek direct and for a gateway. `thinking=disabled`
    used to ride along to the native endpoint and would silently buy a worse
    judgment there — the scorer is paid for its reasoning, not its speed."""
    body = build_body("m", "s", "u", temperature=0.3, max_tokens=10)
    assert body["reasoning_effort"] == "high"
    assert "thinking" not in body
    assert body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


@pytest.mark.anyio
async def test_score_candidates_refuses_without_a_key(rubric, monkeypatch):
    monkeypatch.delenv("TIERLIST_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    try:
        await score_candidates([], rubric, now=NOW,
                               temperature=0.3, max_tokens=10)
    except RuntimeError as exc:
        assert "TIERLIST_LLM_API_KEY" in str(exc)
    else:
        raise AssertionError("missing key must refuse to run")


# ------------------------------------------------- backfill-only tag contract

def _tag_payload(rows):
    return json.dumps({"tags": rows})


def test_tag_response_keeps_known_ids_only():
    parsed = parse_tag_response(_tag_payload([
        {"id": "github:a/b", "tags": ["#Rust", "CLI"]},
        {"id": "github:unknown/x", "tags": ["nope"]},
        {"id": "github:c/d", "tags": "garbled"},
        {"id": "github:a/b", "tags": ["dupe"]},       # first answer wins
        "junk",
    ]), {"github:a/b", "github:c/d"})
    # `cli` is a topic and sorts ahead of the `rust` entity; "nope" and
    # "dupe" never reach the vocabulary because their rows are dropped first.
    assert parsed == {"github:a/b": ["cli", "rust"], "github:c/d": []}


def test_tag_response_survives_non_json():
    for content in ["not json", '"str"', json.dumps([1]), json.dumps({})]:
        assert parse_tag_response(content, {"github:a/b"}) == {}


def test_tag_user_prompt_names_every_candidate_with_description():
    cands = [item("github:a/b"), item("github:c/d")]
    prompt = build_tag_user_prompt(cands)
    for needle in ("github:a/b", "github:c/d", "A real tool"):
        assert needle in prompt


@pytest.mark.anyio
async def test_tag_candidates_refuses_without_a_key(monkeypatch):
    monkeypatch.delenv("TIERLIST_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    try:
        await tag_candidates([item()])
    except RuntimeError as exc:
        assert "TIERLIST_LLM_API_KEY" in str(exc)
    else:
        raise AssertionError("missing key must refuse to run")


# ------------------------------------------------ a tag batch that came back empty

def test_tag_max_tokens_scales_with_the_batch():
    """It was a flat 2000 and a 30-item batch truncated against it."""
    from intel.score.llm import tag_max_tokens

    assert tag_max_tokens(30) > 2000
    assert tag_max_tokens(50) > tag_max_tokens(30)
    assert tag_max_tokens(0) > 0


def _fake_tag_client(replies, sent):
    import httpx

    class _FakeClient:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, url, json, headers):  # noqa: A002
            sent.append(json)
            reason, content = replies[len(sent) - 1]
            return httpx.Response(
                200,
                json={"choices": [{"finish_reason": reason,
                                   "message": {"content": content}}],
                      "usage": {"completion_tokens": 7}},
                request=httpx.Request("POST", url))

    return lambda **kw: _FakeClient(**kw)


_EMPTY_BATCH = '{"tags":[],"note":"候选列表为空，无法标注。"}'


@pytest.mark.anyio
async def test_empty_tag_batch_is_asked_once_more(monkeypatch):
    """2026-10-18: a 50-row batch came back as an empty list with a note
    that the list was missing. The same request is sent once more."""
    from intel.score import llm as llm_mod

    sent: list[dict] = []
    ok = json.dumps({"tags": [{"id": "github:a/b", "tags": ["cli"]}]})
    monkeypatch.setenv("TIERLIST_LLM_API_KEY", "k")
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", _fake_tag_client(
        [("stop", _EMPTY_BATCH), ("stop", ok)], sent))

    tagged, usage = await tag_candidates([item("github:a/b")])

    assert len(sent) == 2
    assert sent[0] == sent[1]
    assert tagged == {"github:a/b": ["cli"]}
    assert usage == {"completion_tokens": 14}


@pytest.mark.anyio
async def test_empty_tag_batch_twice_still_raises(monkeypatch):
    """Two empty replies must not come back as "no tags for anyone": the
    caller would store [] for every row and never queue them again."""
    from intel.score import llm as llm_mod

    sent: list[dict] = []
    monkeypatch.setenv("TIERLIST_LLM_API_KEY", "k")
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", _fake_tag_client(
        [("stop", _EMPTY_BATCH), ("stop", _EMPTY_BATCH)], sent))

    with pytest.raises(RuntimeError, match="parsed to 0 items"):
        await tag_candidates([item("github:a/b")])
    assert len(sent) == 2


@pytest.mark.anyio
async def test_truncated_tag_batch_is_not_retried(monkeypatch):
    """The same ceiling would cut the same reply again."""
    from intel.score import llm as llm_mod

    sent: list[dict] = []
    monkeypatch.setenv("TIERLIST_LLM_API_KEY", "k")
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", _fake_tag_client(
        [("length", '{"tags": [{"id": "github:a/b", "ta')], sent))

    with pytest.raises(RuntimeError, match="token ceiling"):
        await tag_candidates([item("github:a/b")])
    assert len(sent) == 1


# ------------------------------------------------------------ id resolution

@pytest.mark.parametrize("raw,expect", [
    ("github:a/b", "github:a/b"),                    # exact
    ("a/b", "github:a/b"),                           # prefix dropped
    ("github:a/b | a/b", "github:a/b"),              # whole prompt line echoed
    ("a/b | a/b", "github:a/b"),                     # both slips at once
    ("github:nope/x", None),
    ("", None),
    (None, None),
    (7, None),
])
def test_resolve_item_id_tolerates_the_models_slips(raw, expect):
    """A strict rejection voids a whole paid batch over a formatting
    mutation. Both parsers share this, so both tolerate the same set."""
    from intel.score.llm import resolve_item_id

    assert resolve_item_id(raw, {"github:a/b", "github:c/d"}) == expect


def test_tag_prompt_does_not_repeat_the_name_beside_the_id():
    """`- {id} | {name}` said the same thing twice — an id is
    github:owner/repo and the name is owner/repo — and the model echoed the
    whole line back as the id, losing a 25-item batch."""
    from intel.score.llm import build_tag_user_prompt

    prompt = build_tag_user_prompt([item("github:a/b")])
    assert "github:a/b" in prompt
    assert "github:a/b | " not in prompt


def test_a_pipe_mangled_id_still_lands_in_the_tag_response():
    parsed = parse_tag_response(_tag_payload([
        {"id": "github:a/b | a/b", "tags": ["mcp"]},
    ]), {"github:a/b"})
    assert parsed == {"github:a/b": ["mcp"]}


# --------------------------------------- the scoring batch that thought itself out

def test_score_max_tokens_leaves_room_for_the_rows():
    """The ceiling was a flat scoring.max_tokens, so the reasoning and the
    JSON shared one budget. On 2026-08-29 a 37-candidate batch spent all
    16003 of it on reasoning_tokens and returned an empty string."""
    from intel.score.llm import score_max_tokens

    floor = 16000
    assert score_max_tokens(37, floor) > 16003        # the run that failed
    assert score_max_tokens(50, floor) > score_max_tokens(37, floor)
    assert score_max_tokens(0, floor) > floor         # empty batch still thinks


def test_score_max_tokens_never_dips_under_the_reasoning_floor():
    """A config that lowers max_tokens must not reintroduce the truncation."""
    from intel.score.llm import _REASONING_FLOOR, score_max_tokens

    assert score_max_tokens(10, 1000) > _REASONING_FLOOR


@pytest.mark.anyio
async def test_the_scoring_request_carries_the_scaled_ceiling(rubric,
                                                              monkeypatch):
    """The budget function is only worth having if the request uses it: the
    flat ceiling reached the wire through `score_candidates`, not through a
    constant, so that call site is what the regression guards."""
    import httpx

    from intel.score import llm as llm_mod

    sent: list[dict] = []

    class _FakeClient:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, url, json, headers):  # noqa: A002
            sent.append(json)
            return httpx.Response(
                200,
                json={"choices": [{"finish_reason": "stop",
                                   "message": {"content": '{"results": []}'}}],
                      "usage": {}},
                request=httpx.Request("POST", url))

    monkeypatch.setenv("TIERLIST_LLM_API_KEY", "k")
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(**kw))

    candidates = [item(item_id=f"github:a/b{i}") for i in range(37)]
    await score_candidates(candidates, rubric, now=NOW,
                           temperature=0.3, max_tokens=16000)

    assert sent[0]["max_tokens"] == llm_mod.score_max_tokens(37, 16000)
    assert sent[0]["max_tokens"] > 16003


def _row(item_id):
    return {"id": item_id, "dims": _dims(), "reason": "r", "verdict": "watch",
            "tags": []}


@pytest.mark.anyio
async def test_absent_rows_are_asked_for_once_more(rubric, monkeypatch):
    """A batch that returns fewer rows than it was sent gets one follow-up
    for the absent candidates only, and the usage of both calls is summed."""
    import httpx

    from intel.score import llm as llm_mod

    sent: list[dict] = []
    replies = [
        {"results": [_row("github:a/b0"), _row("github:a/b1")]},
        {"results": [_row("github:a/b2")]},
    ]

    class _FakeClient:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, url, json, headers):  # noqa: A002
            sent.append(json)
            body = replies[len(sent) - 1]
            return httpx.Response(
                200,
                json={"choices": [{"finish_reason": "stop",
                                   "message": {"content": _json.dumps(body)}}],
                      "usage": {"prompt_tokens": 10, "cost": 0.5,
                                "completion_tokens_details":
                                    {"reasoning_tokens": 3}}},
                request=httpx.Request("POST", url))

    monkeypatch.setenv("TIERLIST_LLM_API_KEY", "k")
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(**kw))

    candidates = [item(item_id=f"github:a/b{i}") for i in range(3)]
    scores, usage = await score_candidates(candidates, rubric, now=NOW,
                                           temperature=0.3, max_tokens=100)

    assert len(sent) == 2
    assert "github:a/b2" in sent[1]["messages"][1]["content"]
    assert "github:a/b0" not in sent[1]["messages"][1]["content"]
    assert sorted(s.item_id for s in scores) == [
        "github:a/b0", "github:a/b1", "github:a/b2"]
    assert usage == {"prompt_tokens": 20, "cost": 1.0,
                     "completion_tokens_details": {"reasoning_tokens": 6}}


_json = json


def _batch_client(sent: list, fail_on: frozenset[str] = frozenset()):
    """A fake gateway that answers every candidate it is sent, and keeps
    failing any request that carries one of the ids in `fail_on`."""
    import httpx

    class _FakeClient:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, url, json, headers):  # noqa: A002
            sent.append(json)
            ids = [line.split(" | ")[0][2:]
                   for line in json["messages"][1]["content"].splitlines()
                   if line.startswith("- ")]
            if fail_on & set(ids):
                raise httpx.ConnectError("down")
            body = {"results": [_row(i) for i in ids]}
            return httpx.Response(
                200,
                json={"choices": [{"finish_reason": "stop",
                                   "message": {"content": _json.dumps(body)}}],
                      "usage": {"cost": 0.1}},
                request=httpx.Request("POST", url))

    return lambda **kw: _FakeClient(**kw)


async def _no_sleep(_seconds):
    return None


@pytest.mark.anyio
async def test_a_day_is_scored_in_batches(rubric, monkeypatch):
    """Every new repository is rated, so a day of 120 goes out as requests
    of SCORE_BATCH and every row comes back."""
    from intel.score import llm as llm_mod

    sent: list[dict] = []
    monkeypatch.setenv("TIERLIST_LLM_API_KEY", "k")
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", _batch_client(sent))

    candidates = [item(item_id=f"github:a/b{i}") for i in range(120)]
    scores, usage = await score_candidates(candidates, rubric, now=NOW,
                                           temperature=0.3, max_tokens=100)

    assert len(sent) == -(-120 // llm_mod.SCORE_BATCH)
    assert len(scores) == 120
    assert usage["cost"] == pytest.approx(0.1 * len(sent))


@pytest.mark.anyio
async def test_one_failed_batch_costs_only_its_own_rows(rubric, monkeypatch):
    from intel.score import llm as llm_mod

    sent: list[dict] = []
    monkeypatch.setenv("TIERLIST_LLM_API_KEY", "k")
    monkeypatch.setattr(llm_mod.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient",
                        _batch_client(sent, fail_on=frozenset({"github:a/b0"})))

    candidates = [item(item_id=f"github:a/b{i}") for i in range(80)]
    scores, _ = await score_candidates(candidates, rubric, now=NOW,
                                       temperature=0.3, max_tokens=100)
    assert len(scores) == 80 - llm_mod.SCORE_BATCH


@pytest.mark.anyio
async def test_a_day_where_every_batch_fails_raises(rubric, monkeypatch):
    import httpx

    from intel.score import llm as llm_mod

    sent: list[dict] = []
    monkeypatch.setenv("TIERLIST_LLM_API_KEY", "k")
    monkeypatch.setattr(llm_mod.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient",
                        _batch_client(sent, fail_on=frozenset(
                            {"github:a/b0", "github:a/b59"})))

    candidates = [item(item_id=f"github:a/b{i}") for i in range(60)]
    with pytest.raises(httpx.ConnectError):
        await score_candidates(candidates, rubric, now=NOW,
                               temperature=0.3, max_tokens=100)
