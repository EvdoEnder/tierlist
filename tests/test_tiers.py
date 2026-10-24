"""Score → tier: the lines from the rubric, and that no raw score is published."""
from __future__ import annotations

import pytest

from intel import tiers
from intel.rubrics import load_rubric

OLD_DIMS = {"novelty": 20, "utility": 20, "momentum": 20, "commercial": 20}


@pytest.mark.parametrize("score,expected", [
    (100, "S"), (69, "S"), (68, "A"), (58, "A"), (57, "B"), (44, "B"),
    (43, "C"), (29, "C"), (28, "D"), (0, "D"), (None, None),
])
def test_lines_match_the_rubric(score, expected):
    assert tiers.tier(score) == expected


@pytest.mark.parametrize("score,expected", [
    (77, "S"), (76, "A"), (65, "A"), (64, "B"), (53, "B"),
    (52, "C"), (39, "C"), (38, "D"),
])
def test_scores_from_the_old_rubric_keep_the_old_lines(score, expected):
    assert tiers.tier(score, dims=OLD_DIMS) == expected


def test_current_dims_use_the_current_lines():
    dims = dict.fromkeys(load_rubric("dev_tools").dimensions, 15)
    assert tiers.lines(dims=dims) == tiers.lines()
    assert tiers.tier(69, dims=dims) == "S"


def test_lines_descend():
    assert [n for n, _ in tiers.lines()] == [69, 58, 44, 29]


def test_rank_orders_tiers_and_puts_unrated_last():
    assert [tiers.rank(t) for t in ("S", "A", "B", "C", "D", None)] == \
        [5, 4, 3, 2, 1, 0]


def test_feed_carries_the_tier_and_not_the_score():
    from intel.render import site

    xml = site.render_feed(
        [{"title": "a/b", "url": "https://github.com/a/b",
          "updated": "2026-10-22T00:00:00Z", "summary": "s", "tier": "A"}],
        updated="2026-10-22T00:00:00Z", site_url="https://tierlist.tech",
        lang="en")
    assert "<tl:tier" in xml and ">A</tl:tier>" in xml
    assert "score" not in xml
