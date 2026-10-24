"""The renderers: markdown push budget + the mailer's plain-text brief."""
from __future__ import annotations

from conftest import item

from intel.deliver.context import build_context
from intel.deliver.markdown import render_markdown
from intel.pipeline import RunResult
from intel.score.llm import ScoredItem


def make_result(hot_count=1, hot_desc_len=0) -> RunResult:
    top = [ScoredItem(
        item_id="github:a/b", score=76,
        dims={"novelty": 18, "utility": 20, "momentum": 21, "commercial": 17},
        reason="实质判断文字", verdict="watch")]
    hot = [item(f"github:hot/{i}", stars=900 - i,
                description="热榜描述" * (hot_desc_len or 1))
           for i in range(hot_count)]
    return RunResult(
        vertical="dev_tools", since_date="2026-08-22",
        ran_at="2026-08-23T12:00:00+00:00",
        fetched=120, candidates=40, scored=38, watch_min=60,
        stored_new=40, run_mode="off",
        hot_new=hot, candidate_items=[item("github:a/b")], top=top,
        usage={"prompt_tokens": 1000, "completion_tokens": 500},
    )


def test_markdown_carries_entry_names_and_urls():
    text = render_markdown(build_context(make_result()))
    assert "a/b" in text                 # the scored entry's name
    assert "https://github.com/a/b" in text
    assert "[A]" in text


def test_markdown_drops_hot_rows_until_the_budget_holds():
    ctx = build_context(make_result(hot_count=60, hot_desc_len=200))
    text = render_markdown(ctx)
    assert len(text) <= 3800
    # the payload survives the trim; only garnish rows were dropped
    assert "https://github.com/a/b" in text


def test_markdown_keeps_everything_when_already_under_budget():
    text = render_markdown(build_context(make_result()))
    assert "新仓库热榜" in text and len(text) < 3800


def test_hot_board_rows_are_links():
    """The hot board listed names with no way to reach them; the name is now
    an inline link. An edition context supplies no url and must not break."""
    text = render_markdown(build_context(make_result()))
    assert "[hot/0](https://github.com/hot/0)" in text
    from intel.deliver.markdown import _hot_line
    assert _hot_line({"rank": "01", "name": "a/b", "stars": 5,
                      "desc": None}) == "01. a/b ★5 — (无描述)"
