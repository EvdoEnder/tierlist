"""build_context: the copy decisions both renderers inherit."""
from __future__ import annotations

import re
from dataclasses import replace

import pytest
from conftest import item

from intel.deliver.context import build_context, cost_str
from intel.pipeline import RunResult
from intel.score.llm import ScoredItem


def make_result(top: list[ScoredItem], usage: dict | None = None) -> RunResult:
    return RunResult(
        vertical="dev_tools",
        since_date="2026-08-22",
        ran_at="2026-08-23T12:00:00+00:00",
        fetched=120,
        candidates=40,
        scored=38,
        watch_min=60,
        stored_new=40,
        run_mode="off",
        hot_new=[item("github:hot/one", stars=473)],
        candidate_items=[item("github:a/b"), item("github:c/d")],
        top=top,
        usage=usage if usage is not None else {"prompt_tokens": 1000,
                                               "completion_tokens": 500},
    )


def scored(item_id="github:a/b", score=76, verdict="watch"):
    return ScoredItem(
        item_id=item_id,
        score=score,
        dims={"novelty": 18, "utility": 20, "momentum": 21, "commercial": 17},
        reason="判断文字",
        verdict=verdict,
    )


def test_ranks_are_zero_padded_in_order():
    c = build_context(make_result([scored("github:a/b"),
                                   scored("github:c/d", score=61)]))
    assert [e["rank"] for e in c["entries"]] == ["01", "02"]


def test_cost_format_is_fixed_four_decimals():
    usages = [{}, None, {"prompt_tokens": 123456, "completion_tokens": 7890}]
    for usage in usages:
        cost = cost_str(usage)
        assert re.fullmatch(r"\$0\.\d{4}", cost), cost


def test_high_score_flag_flips_at_70():
    below = build_context(make_result([scored(score=69)]))["entries"][0]
    at = build_context(make_result([scored(score=70)]))["entries"][0]
    assert below["high_score"] is False
    assert at["high_score"] is True


def test_long_description_truncated_with_ellipsis():
    long_item = item("github:a/b",
                     description="很长的描述" * 200)
    res = make_result([scored()])
    res.candidate_items = [long_item]
    desc = build_context(res)["entries"][0]["description"]
    assert len(desc) <= 201 and desc.endswith("…")


def test_missing_lang_falls_back_to_chinese_label():
    no_lang = replace(item("github:a/b"), lang=None)
    res = make_result([scored()])
    res.candidate_items = [no_lang]
    assert build_context(res)["entries"][0]["lang"] == "语言未知"


def test_masthead_and_lede_fields():
    c = build_context(make_result([]))
    assert c["date"] == "2026-08-23"
    assert c["vertical_label"] == "开发者工具晨报"
    lede = c["lede"]
    assert (lede["fetched"], lede["candidates"], lede["sent"],
            lede["scored"]) == (120, 40, 40, 38)
    assert lede["cost"].startswith("$")
    assert c["generated_at"]


@pytest.mark.parametrize("verdict,is_watch", [("watch", True), ("skip", False)])
def test_watch_flag_follows_verdict(verdict, is_watch):
    entry = build_context(make_result([scored(verdict=verdict)]))["entries"][0]
    assert entry["is_watch"] is is_watch


def test_hot_board_rows_carry_rank_stars_and_short_desc():
    rows = build_context(make_result([]))["hot_new"]
    assert rows[0]["rank"] == "01"
    assert rows[0]["stars"] == 473
    assert rows[0]["desc"] is not None and len(rows[0]["desc"]) <= 71


def test_the_page_names_the_model_the_scorer_calls(monkeypatch):
    """Both sides resolved TIERLIST_LLM_MODEL themselves, with different
    defaults — an unset variable had the site name a model that was never
    called. One reading, so they cannot drift again."""
    from intel.deliver.context import llm_model_label
    from intel.score import llm as llm_mod
    from intel.score.llm import llm_model

    monkeypatch.setenv("TIERLIST_LLM_MODEL", "some-other-model")
    assert llm_model_label() == llm_model() == "some-other-model"

    # Moving the scorer's default has to move the label with it. A label
    # that carries its own copy of the literal passes every check but this
    # one — which is exactly the state the two functions were found in.
    monkeypatch.delenv("TIERLIST_LLM_MODEL", raising=False)
    monkeypatch.setattr(llm_mod, "_DEFAULT_MODEL", "moved-default")
    assert llm_model_label() == "moved-default"
