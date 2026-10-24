"""The shadow preview's one piece of arithmetic.

Everything else in `intel.shadow` is I/O and printing; this is the number a
reader would otherwise take on trust — how much of the requested window the
fetch actually reached.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from intel.shadow import coverage_hours
from intel.sources.base import Item

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _post(hours_old: float | None):
    return Item(id=f"hn:{hours_old}", source="hn", url="u", name="n",
                description="", lang=None, stars=1,
                created_at=None if hours_old is None
                else NOW - timedelta(hours=hours_old))


def test_coverage_is_the_oldest_item_fetched():
    assert coverage_hours([_post(2), _post(54), _post(9)], NOW) == 54


def test_items_without_a_timestamp_do_not_count_as_coverage():
    assert coverage_hours([_post(None), _post(3)], NOW) == 3


def test_an_empty_fetch_covers_nothing():
    # Not an error: a quiet window and a truncated one both return rows the
    # caller has to read, and they must not look identical by accident.
    assert coverage_hours([], NOW) == 0.0
