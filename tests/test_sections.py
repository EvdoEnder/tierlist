"""Two boards, not one list: which entries go where, and what it costs.

The rule these pin is a delivery decision, not a taste one. On 2026-08-30 a
star-farm took 37 of the 38 GitHub candidates and scored 9, so seven of the
ten picks were Hacker News posts and the brief read as an HN digest with one
repository in it. Ranking each board on its own is what makes the reader's
share of each independent of the other's bad day.
"""
from __future__ import annotations

from datetime import UTC, datetime

from conftest import item

from intel import sections
from intel.deliver import markdown
from intel.deliver.context import build_context
from intel.editions import best_per_item, daily_context
from intel.pipeline import PipelineConfig, _top
from intel.score.llm import ScoredItem
from intel.sources.base import Item


def _hn(item_id="hn:1", stars=180):
    return Item(id=item_id, source="hn",
                url=f"https://news.ycombinator.com/item?id={item_id[3:]}",
                name=f"Show HN: {item_id}", description="一个新工具",
                lang=None, stars=stars,
                created_at=datetime(2026, 8, 30, 6, 0, tzinfo=UTC))


def _scored(item_id, score):
    return ScoredItem(item_id=item_id, score=score,
                      dims={"novelty": 10, "utility": 10, "momentum": 10,
                            "commercial": 10},
                      reason="判断", verdict="watch" if score >= 60 else "skip")


def _row(item_id, score, source="github"):
    return {"item_id": item_id, "score": score, "source": source,
            "name": item_id, "url": f"https://x/{item_id}", "stars": 10,
            "lang": "Rust", "dims": {}, "description": "d", "reason": "r",
            "tags": [], "verdict": "watch"}


# --------------------------------------------------------------- the mapping

def test_a_repository_source_is_on_the_picks_board():
    assert sections.section_of("github") == sections.PICKS
    # An awesome-list entry IS a repository — endorsed by a curator instead
    # of by a star count, but the same kind of thing.
    assert sections.section_of("awesome") == sections.PICKS
    assert sections.section_of("hn") == sections.HN


def test_an_unknown_source_lands_on_the_picks_board():
    assert sections.section_of(None) == sections.PICKS
    assert sections.section_of("mystery") == sections.PICKS


def test_the_floor_applies_to_show_hn_only():
    rows = [{"source": "hn", "score": 54}, {"source": "github", "score": 9}]
    out = sections.split(rows)
    assert out[sections.HN] == []          # 54 is under the 55 line
    assert len(out[sections.PICKS]) == 1   # 9 is not: 精选 has no floor


def test_the_cap_is_per_board_not_shared():
    rows = [{"source": "github", "score": 50} for _ in range(12)] + \
           [{"source": "hn", "score": 80} for _ in range(12)]
    out = sections.split(rows, limit=10)
    assert (len(out[sections.PICKS]), len(out[sections.HN])) == (10, 10)


def test_input_order_is_preserved_within_a_board():
    rows = [{"source": "github", "score": s} for s in (90, 70, 40)]
    assert [r["score"] for r in sections.split(rows)[sections.PICKS]] == \
        [90, 70, 40]


# ------------------------------------------------------------- the selection

def _cfg(top_n=10):
    return PipelineConfig(lookback_hours=72, max_candidates=50,
                          temperature=0.3, max_tokens=2000, top_n=top_n)


def test_a_flooded_hn_day_cannot_take_the_repository_slots():
    """The 2026-08-30 shape, inverted: twelve strong HN posts and three
    repositories. Before the split the repositories got whatever was left of
    ten; each board now fills its own."""
    items = {f"hn:{i}": _hn(f"hn:{i}") for i in range(12)}
    items.update({f"github:r/{i}": item(f"github:r/{i}") for i in range(3)})
    scores = [_scored(f"hn:{i}", 90 - i) for i in range(12)] + \
             [_scored(f"github:r/{i}", 50 - i) for i in range(3)]

    top = _top(scores, None, _cfg(), items)
    picks = [s for s in top if s.item_id.startswith("github:")]
    hn = [s for s in top if s.item_id.startswith("hn:")]
    assert len(picks) == 3        # every repository, not the three left over
    assert len(hn) == 10          # its own cap, not the whole batch
    assert top[:3] == picks       # repositories first — sections.ORDER


def test_a_post_below_the_line_never_reaches_the_top():
    items = {"hn:1": _hn("hn:1"), "github:a/b": item("github:a/b")}
    top = _top([_scored("hn:1", 54), _scored("github:a/b", 9)],
               None, _cfg(), items)
    assert [s.item_id for s in top] == ["github:a/b"]


def test_best_per_item_caps_each_board_separately():
    rows = [_row(f"hn:{i}", 90, "hn") for i in range(12)] + \
           [_row(f"github:r/{i}", 50) for i in range(12)]
    out = best_per_item(rows, limit=10)
    assert sum(1 for r in out if r["source"] == "github") == 10
    assert sum(1 for r in out if r["source"] == "hn") == 10


# ----------------------------------------------------------- the two outputs

def _result(top, candidates):
    from intel.pipeline import RunResult

    return RunResult(
        vertical="dev_tools", since_date="2026-08-27",
        ran_at="2026-08-30T12:00:00+00:00", fetched=600, candidates=50,
        scored=len(top), watch_min=60, stored_new=len(top), run_mode="pg",
        hot_new=[], candidate_items=candidates, top=top,
        usage={"prompt_tokens": 10, "completion_tokens": 5})


def _mixed_result():
    candidates = [item("github:a/b", stars=203), _hn("hn:1", stars=215)]
    return _result([_scored("github:a/b", 68), _scored("hn:1", 84)],
                   candidates)


def test_each_board_is_numbered_from_one():
    ctx = build_context(_mixed_result())
    assert [e["rank"] for e in ctx["entries"]] == ["01"]
    assert [e["rank"] for e in ctx["hn_entries"]] == ["01"]


def test_the_brief_holds_the_repositories_and_nothing_else():
    ctx = build_context(_mixed_result())
    brief = markdown.render_markdown(ctx)
    assert "a/b" in brief
    assert "hn:1" not in brief and "[HN]" not in brief


def test_the_show_hn_message_stands_alone():
    ctx = build_context(_mixed_result())
    hn_brief = markdown.render_hn_brief(ctx)
    assert "Show HN" in hn_brief
    assert "a/b" not in hn_brief          # the repository is the other message


def test_no_second_message_when_nothing_cleared_the_line():
    ctx = build_context(_result([_scored("github:a/b", 68)],
                                [item("github:a/b")]))
    # None, not "" — nothing to push is not the same as an empty push.
    assert markdown.render_hn_brief(ctx) is None


def test_the_page_gets_both_boards_and_the_line_it_applied():
    ctx = daily_context(_mixed_result(), stats={"new_count": 2},
                        gain_board={"label": "l", "window_days": 1,
                                    "ready": False, "need": 2, "have": 1,
                                    "rows": None},
                        archive_recent={})
    assert [e["name"] for e in ctx["entries"]] == ["a/b"]
    assert [e["source"] for e in ctx["hn_entries"]] == ["hn"]
    assert ctx["hn_min_score"] == 55
