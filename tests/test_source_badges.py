"""Where a signal came from, on every surface that shows it.

The badge is not decoration: a repository with 40 stars and a Show HN post
with 40 points are different claims, and the row has to say which one it is
making. These tests pin the three places that could disagree — the site
card, the pushed brief, and the context both read from.
"""
from __future__ import annotations

from datetime import UTC, datetime

from conftest import item

from intel.deliver import markdown
from intel.deliver.context import build_context, source_badge, source_metric
from intel.editions import daily_context
from intel.render import site
from intel.score.llm import ScoredItem, build_user_prompt
from intel.sources.base import Item

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _hn_item(item_id="hn:42"):
    return Item(id=item_id, source="hn",
                url="https://news.ycombinator.com/item?id=42",
                name="Show HN: a local-first vector store",
                description="", lang=None, stars=180,
                created_at=datetime(2026, 8, 30, 6, 0, tzinfo=UTC))


def _result(candidate):
    from intel.pipeline import RunResult

    scored = ScoredItem(item_id=candidate.id, score=76,
                        dims={"novelty": 20, "utility": 22, "momentum": 18,
                              "commercial": 16},
                        reason="实质判断", verdict="watch", tags=["storage"])
    return RunResult(
        vertical="dev_tools", since_date="2026-08-27",
        ran_at="2026-08-30T12:00:00+00:00", fetched=120, candidates=40,
        scored=1, watch_min=60, stored_new=1, run_mode="pg",
        hot_new=[], candidate_items=[candidate], top=[scored],
        usage={"prompt_tokens": 10, "completion_tokens": 5})


def _board():
    return {"label": "star 增速榜 · 日", "window_days": 1, "ready": False,
            "need": 2, "have": 1, "rows": None}


# ------------------------------------------------------------------- helpers

def test_each_source_has_a_two_character_badge():
    assert source_badge("github") == "GH"
    assert source_badge("hn") == "HN"
    assert source_badge("awesome") == "AW"


def test_an_unknown_source_does_not_invent_a_badge():
    assert source_badge(None) == "—" and source_badge("mystery") == "—"


def test_the_metric_glyph_follows_the_platform():
    # Stars and upvotes are not the same unit; a star in front of a point
    # count would make two incomparable numbers look like one column.
    assert source_metric("github", 473) == "★473"
    assert source_metric("hn", 180) == "▲180"


# --------------------------------------------------------------- site + push

def test_the_card_says_hn_and_drops_the_language_it_cannot_have():
    ctx = daily_context(_result(_hn_item()), stats={"new_count": 1},
                        gain_board=_board(), archive_recent={})
    # A Show HN post is on the Show HN board, never in 今日精选.
    assert ctx["entries"] == []
    entry = ctx["hn_entries"][0]
    assert (entry["source"], entry["badge"], entry["metric"]) == \
        ("hn", "HN", "▲180")
    assert entry["lang"] is None

    html = site.render_issue(ctx)
    assert "Show HN" in html and "▲180" in html
    assert "语言未知" not in html      # a post has no language to be unknown
    assert "仓库链接" not in html      # there is no repository to link


def test_a_repository_card_is_unchanged():
    ctx = daily_context(_result(item("github:a/b", stars=473)),
                        stats={"new_count": 1}, gain_board=_board(),
                        archive_recent={})
    entry = ctx["entries"][0]
    assert (entry["badge"], entry["metric"]) == ("GH", "★473")
    html = site.render_issue(ctx)
    # Every rated entry is a repository, so the card carries no source badge.
    assert ">GH<" not in html and "★473" in html


def test_the_pushed_brief_carries_the_badge_and_the_right_glyph():
    # The Show HN board is its own message, so that is where the post's
    # badge and glyph have to survive.
    line = markdown.render_hn_brief(build_context(_result(_hn_item())))
    assert "[HN]" in line and "▲180" in line


def test_an_older_context_without_a_badge_still_renders():
    # The brief renderer is shared with rebuilt back issues, whose contexts
    # predate these keys.
    legacy = {"rank": "01", "name": "a/b", "score": 76, "stars": 473,
              "reason": "r", "url": "u",
              "dims": {"novelty": 19, "utility": 19, "momentum": 19,
                       "commercial": 19}}
    assert markdown._entry_block(legacy)[0] == "01. a/b [A] ★473"


# ------------------------------------------------------------------- scoring

def test_the_scorer_is_told_points_are_points():
    prompt = build_user_prompt([_hn_item()], NOW)
    assert "points=180" in prompt and "stars=" not in prompt
    assert "Hacker News" in prompt


def test_a_repository_line_still_reads_in_stars():
    prompt = build_user_prompt([item("github:a/b", stars=473)], NOW)
    assert "stars=473" in prompt and "points=" not in prompt
