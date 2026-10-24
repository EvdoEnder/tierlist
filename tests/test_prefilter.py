"""The free prefilter: which candidates reach the paid stage, per source."""
from __future__ import annotations

from datetime import timedelta

from conftest import NOW, item

from intel.score.prefilter import (
    PrefilterRules,
    apply,
    apply_by_source,
    interleave,
    points_per_hour,
    star_velocity,
)

RULES = PrefilterRules(
    min_description_chars=20,
    min_star_velocity_per_day=5.0,
    max_candidates=3,
    drop_forks=True,
)

HN_RULES = PrefilterRules(min_points_per_hour=1.0, max_candidates=3)

_UNSET = object()


def _hn(item_id="hn:1", points=10, created=_UNSET, title="Show HN: a thing"):
    from intel.sources.base import Item

    return Item(id=item_id, source="hn",
                url="https://news.ycombinator.com/item?id=1",
                name=title, description="", lang=None, stars=points,
                created_at=NOW - timedelta(hours=2) if created is _UNSET
                else created)


# ------------------------------------------------------------------- github

def test_velocity_is_stars_per_day_since_creation():
    created = NOW - timedelta(days=2)
    assert star_velocity(10, created, NOW) == 5.0


def test_fresh_repos_get_a_half_day_age_floor():
    # 6 hours old, 3 stars → floored age 0.5 d → 6/day, not 12/day.
    created = NOW - timedelta(hours=6)
    assert star_velocity(3, created, NOW) == 6.0


def test_unknown_age_scores_zero_and_is_filtered():
    survivors = apply([item(created=None)], RULES, NOW)
    assert survivors == []


def _passing(stars=50, name="owner/repo"):
    return item(item_id=f"github:{name}", stars=stars,
                description="x" * RULES.min_description_chars)


def _many(count, first_stars=100):
    """`count` distinct passing repos, star-descending — an adapter page."""
    return [_passing(stars=first_stars - n, name=f"owner/repo{n}")
            for n in range(count)]


def test_each_rule_filters():
    short_desc = item(description="too short")
    slow = item(description="x" * 20, stars=1)          # 1/1d < 5
    forked = item(description="x" * 20, stars=50, fork=True)
    assert apply([short_desc, slow, forked], RULES, NOW) == []
    assert apply([_passing()], RULES, NOW) != []


def test_truncation_keeps_the_strongest_in_input_order():
    # Input arrives star-descending from the adapter; the cap must keep the
    # head of that order, not an arbitrary subset.
    survivors = apply(_many(5), RULES, NOW)
    assert len(survivors) == RULES.max_candidates
    assert survivors[0].stars == 100


def test_boundary_velocity_exactly_at_threshold_passes():
    created = NOW - timedelta(days=1)
    edge = item(stars=5, description="x" * 20, created=created)
    assert apply([edge], RULES, NOW) == [edge]


# ----------------------------------------------------------------------- hn

def test_points_per_hour_is_the_hn_measure_of_momentum():
    assert points_per_hour(20, NOW - timedelta(hours=4), NOW) == 5.0


def test_a_minutes_old_post_gets_an_hour_age_floor():
    # Without the floor, 3 points in 6 minutes reads as 30 points/hour and
    # tops every board on the strength of one upvote.
    assert points_per_hour(3, NOW - timedelta(minutes=6), NOW) == 3.0


def test_an_hn_post_is_not_judged_by_stars_it_cannot_have():
    # The github rules would drop every HN post twice over: no description
    # and no star velocity. Its own rules let a climbing post through.
    assert apply([_hn(points=10)], RULES, NOW) == []
    assert apply([_hn(points=10)], HN_RULES, NOW) != []


def test_a_stale_post_with_a_few_points_is_dropped():
    old = _hn(points=3, created=NOW - timedelta(hours=20))
    assert apply([old], HN_RULES, NOW) == []


def test_a_post_without_a_timestamp_is_dropped_not_guessed_at():
    assert apply([_hn(created=None)], HN_RULES, NOW) == []


# -------------------------------------------------------------------- merge

def test_interleave_gives_every_source_its_turn():
    gh = _many(2)
    hn = [_hn(item_id="hn:1"), _hn(item_id="hn:2")]
    merged = interleave([gh, hn], cap=10)
    assert [i.source for i in merged] == ["github", "hn", "github", "hn"]


def test_a_loud_source_cannot_take_every_slot():
    # 300 GitHub rows against 2 HN rows: the HN pair must still be scored.
    hn = [_hn(item_id="hn:1"), _hn(item_id="hn:2")]
    merged = interleave([_many(300), hn], cap=50)
    assert len(merged) == 50
    assert sum(1 for i in merged if i.source == "hn") == 2


def test_a_source_that_runs_out_does_not_waste_the_cap():
    merged = interleave([_many(5), []], cap=4)
    assert len(merged) == 4


def test_the_same_item_from_two_sources_is_carried_once():
    dup = _passing(stars=100)
    assert len(interleave([[dup], [dup]], cap=10)) == 1


def test_apply_by_source_uses_each_sources_own_rules():
    items = [_passing(stars=100), _hn(item_id="hn:1", points=10)]
    merged = apply_by_source(items, {"github": RULES, "hn": HN_RULES},
                             cap=10, now=NOW)
    assert {i.source for i in merged} == {"github", "hn"}


def test_a_source_with_no_rules_never_reaches_the_paid_stage():
    # Nobody has written down what makes one of its entries worth paying
    # for, so it is dropped rather than admitted on someone else's lines.
    merged = apply_by_source([_hn(item_id="hn:1")], {"github": RULES},
                             cap=10, now=NOW)
    assert merged == []


def test_disabling_a_source_reproduces_the_single_source_behaviour():
    rows = _many(5)
    assert apply_by_source(rows, {"github": RULES}, cap=3, now=NOW) \
        == apply(rows, RULES, NOW)
