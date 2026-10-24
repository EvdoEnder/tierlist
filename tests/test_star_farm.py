"""The star-farm guard: a bought batch is visible in the company it keeps.

These pin a rule that was written against measured data, so the numbers in
them are the shape of the real thing rather than a convenient fixture. The
2026-08-30 batch: 40 repositories, exactly 150 stars each, created inside 36
minutes, named `gta-6-download-cyberleek` and the like. Each one cleared the
velocity line by 25x.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import item

from intel.score.prefilter import PrefilterRules, bursts, drop_bursts
from intel.sources.base import Item

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def repo(name, stars, minutes_before, source="github"):
    return Item(id=f"{source}:{name}", source=source,
                url=f"https://github.com/{name}", name=name,
                description="A tool", lang="C#", stars=stars,
                created_at=NOW - timedelta(minutes=minutes_before))


def farm(n=40, stars=150, spread_minutes=36):
    """One purchase: identical star counts, one sitting."""
    return [repo(f"seller{i}/gta-6-download", stars,
                 100 - (i * spread_minutes / n)) for i in range(n)]


def test_a_bought_batch_is_dropped_whole():
    ids = bursts(farm(), min_cluster=5, window_minutes=60)
    assert len(ids) == 40


def test_a_shared_star_count_alone_is_not_evidence():
    """The store holds 97 repositories with 8 stars fetched on one day,
    spread over 35 hours. They are genuine, and the rule must not touch
    them."""
    spread = [repo(f"real{i}/tool", 8, i * 60) for i in range(40)]
    assert bursts(spread, min_cluster=5, window_minutes=60) == set()


def test_a_tight_window_alone_is_not_evidence():
    # GitHub creates thousands of repositories an hour; what none of them
    # share is an exact star count.
    minute_apart = [repo(f"real{i}/tool", 3 + i, i) for i in range(20)]
    assert bursts(minute_apart, min_cluster=5, window_minutes=60) == set()


def test_a_burst_across_an_hour_boundary_is_one_burst():
    """The 08-30 batch straddled 20:40 → 21:16. Bucketing by clock hour
    would have split it into two runs and let a smaller one through."""
    straddling = [repo(f"seller{i}/x", 150, 100 - i * 6) for i in range(6)]
    assert len(bursts(straddling, min_cluster=5, window_minutes=60)) == 6


def test_four_siblings_are_left_alone_at_a_cluster_of_five():
    four = [repo(f"maybe{i}/x", 12, i) for i in range(4)]
    assert bursts(four, min_cluster=5, window_minutes=60) == set()


def test_an_item_without_a_creation_time_is_never_dropped():
    """The rule is a statement about when things were made; it cannot be
    made about a row that does not say."""
    timeless = [Item(id=f"github:x/{i}", source="github", url="u", name="n",
                     description="d", lang=None, stars=150, created_at=None)
                for i in range(10)]
    assert bursts(timeless, min_cluster=5, window_minutes=60) == set()


def test_the_rule_is_off_at_zero():
    assert bursts(farm(), min_cluster=0, window_minutes=60) == set()


# ------------------------------------------------------------- per source

def _rules(cluster=5):
    return {"github": PrefilterRules(max_candidates=50,
                                     burst_min_cluster=cluster),
            "hn": PrefilterRules(max_candidates=50)}


def test_the_real_repository_survives_its_days_farm():
    """2026-08-30 in miniature: one genuine repository, one bought batch."""
    real = item("github:cbrock84/headcount", stars=203)
    items = [real, *farm()]
    kept, counts = drop_bursts(items, _rules())
    assert [i.id for i in kept] == [real.id]
    assert counts == {"github": 40}


def test_a_source_without_the_rule_is_untouched():
    # HN points move on their own clock; the rule is about purchased stars.
    posts = [Item(id=f"hn:{i}", source="hn", url="u", name="Show HN: x",
                  description="", lang=None, stars=5,
                  created_at=NOW - timedelta(minutes=i))
             for i in range(20)]
    kept, counts = drop_bursts(posts, _rules())
    assert len(kept) == 20 and counts == {}


def test_nothing_moves_when_every_source_has_the_rule_off():
    items = farm()
    kept, counts = drop_bursts(items, _rules(cluster=0))
    assert kept is items and counts == {}
