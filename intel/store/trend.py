"""Pure trend math over Items and prior score rows — no I/O, no network.

These feed the brief's trend lines: the day's language
mix, and which items were already strong in an earlier snapshot. Rows are
read structurally (attribute access), so psycopg Row objects and test
doubles both fit.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol

from intel.score.llm import ScoredItem
from intel.sources.base import Item

UNKNOWN_LANG = "unknown"


def lang_dist(items: list[Item]) -> dict[str, int]:
    """Language counts across items; missing values bucket under 'unknown'."""
    dist: dict[str, int] = {}
    for item in items:
        key = item.lang or UNKNOWN_LANG
        dist[key] = dist.get(key, 0) + 1
    return dist


class PriorScore(Protocol):
    """One earlier snapshot row: `recent_scores()` output qualifies."""

    item_id: str
    score: int
    snap_date: date


def annotate_repeat(scored: list[ScoredItem],
                    recent_rows: list[PriorScore], *,
                    today: date) -> dict[str, int]:
    """item_id → best score from a snapshot strictly before `today`.

    Rows dated today or later are ignored — today's own snapshot must not
    count as history. Ids outside `scored` are dropped: the brief annotates
    only what it already shows. A later deliver step marks survivors as
    持续走强.
    """
    keep = {s.item_id for s in scored}
    best: dict[str, int] = {}
    for row in recent_rows:
        if row.item_id not in keep or row.snap_date >= today:
            continue
        prev = best.get(row.item_id)
        if prev is None or row.score > prev:
            best[row.item_id] = row.score
    return best
