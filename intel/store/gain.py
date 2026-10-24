"""Star-gain math for the 增速榜: pure core + the SQL that feeds it.

The board is honest by construction: a row exists only when that
project's own snapshots actually span the window, and when none do the board
stays dark with the 积累中 note instead of showing a shorter span under a
longer title.

That distinction is the whole design. Readiness used to be read off the
TABLE's span — earliest snapshot to latest — which says nothing about any
single project: the sweep gave each item about three days of history, so a
table three weeks wide still held nothing that could answer "how much did
this grow in 30 days". Coverage is therefore measured per item, and the
reference snapshot must genuinely sit a window back (`intel.resnapshot` is
what keeps those series alive).
"""
from __future__ import annotations

from datetime import date

from psycopg import AsyncConnection


def reference_slack(window_days: int) -> int:
    """How much older than the exact reference date a past snapshot may be.

    The refresh writes every tracked item every day, so the exact date is
    normally there; the slack exists for the day a run is missed. It is
    one-sided on purpose — a slightly OLDER reference reports a slightly
    generous gain, while a NEWER one would report a shorter span under the
    window's name, which is the failure the board exists to avoid.
    """
    return max(1, window_days // 7)


def compute_gains(pairs_now: list[tuple[str, int]],
                  pairs_past: list[tuple[str, int]],
                  window: int) -> list[tuple[str, int, int]]:
    """(item_id, stars_now) vs (item_id, stars_at_reference) →
    (item_id, stars_now, gain), best-first. Items with no past pair are
    excluded — a gain we cannot span the window for is not a gain. `window`
    documents the contract (the caller picked the reference pair to span it);
    the subtraction itself needs only the two pairs."""
    past = dict(pairs_past)
    gains = [(item_id, stars_now, stars_now - past[item_id])
             for item_id, stars_now in pairs_now if item_id in past]
    gains.sort(key=lambda g: (-g[2], g[0]))
    return gains


async def _coverage(conn: AsyncConnection, window_days: int,
                    as_of: date | None = None) -> tuple[int, int]:
    """(longest per-item series in days, how many items span the window).

    Both are measured per item and both are pinned at `as_of`, so a rebuilt
    back issue reports the coverage that existed then.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT COALESCE(MAX(span), -1),
                   COUNT(*) FILTER (WHERE span >= %s::int)
            FROM (
                SELECT MAX(snap_date) - MIN(snap_date) AS span
                FROM intel.star_snapshots
                WHERE snap_date <= COALESCE(%s::date, CURRENT_DATE)
                GROUP BY item_id
            ) t
            """,
            (window_days, as_of),
        )
        row = await cur.fetchone()
    longest, spanning = row
    return int(longest), int(spanning or 0)


async def _gain_query_rows(conn: AsyncConnection, window_days: int,
                           limit: int,
                           as_of: date | None = None) -> list[dict]:
    """stars at the latest snap minus stars at that item's own snapshot a
    window earlier, joined to intel.items for display fields.

    "Latest" means latest at `as_of`, not latest overall: a rebuilt issue
    from 2026-08-23 must show the board that day's readers saw, not today's.

    The reference snapshot is the newest one at or before (latest −
    window_days), and no older than `reference_slack` beyond that. An item
    without such a snapshot is dropped rather than paired with whatever it
    happens to have: pairing by nearest date is how a three-day gain ends up
    printed under a 30-day heading.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            WITH bounds AS (
                SELECT MAX(snap_date) AS latest FROM intel.star_snapshots
                WHERE snap_date <= COALESCE(%s::date, CURRENT_DATE)
            ),
            ref AS (
                SELECT latest - %s::int AS ref_date FROM bounds
            ),
            now_stars AS (
                SELECT item_id, stars FROM intel.star_snapshots
                WHERE snap_date = (SELECT latest FROM bounds)
            ),
            past_stars AS (
                SELECT DISTINCT ON (ss.item_id) ss.item_id, ss.stars
                FROM intel.star_snapshots ss CROSS JOIN ref r
                WHERE ss.snap_date <= r.ref_date
                  AND ss.snap_date >= r.ref_date - %s::int
                ORDER BY ss.item_id, ss.snap_date DESC
            )
            SELECT n.item_id,
                   n.stars - p.stars AS gain,
                   n.stars AS stars_now,
                   i.name, i.url, i.lang
            FROM now_stars n
            JOIN past_stars p USING (item_id)
            JOIN intel.items i ON i.id = n.item_id
            ORDER BY gain DESC, n.item_id COLLATE "C" ASC
            LIMIT %s
            """,
            (as_of, window_days, reference_slack(window_days), limit),
        )
        rows = await cur.fetchall()
    return [{"name": r[3], "url": r[4], "lang": r[5],
             "stars_now": r[2], "gain": r[1]} for r in rows]


async def gain_rows(conn: AsyncConnection, window_days: int,
                    limit: int = 10, as_of: date | None = None) -> dict:
    """The gain board's data half: readiness + rows.

    `have` is the longest star series any single item has, in days, and
    `need` the window's own length in days — the two numbers the 积累中 note
    prints, now comparable because both are per item. `ready` requires at
    least one item to span the window AND that pairing to survive into rows;
    a board that is ready but empty would print no rows and no explanation.

    `as_of` (default today) pins every bound to a point in time so a rebuilt
    back issue reports the readiness it had then.
    """
    longest, spanning = await _coverage(conn, window_days, as_of)
    have = longest + 1 if longest >= 0 else 0   # a span of 0 days is 1 day of data
    need = window_days + 1
    rows = await _gain_query_rows(conn, window_days, limit,
                                  as_of) if spanning else None
    ready = bool(rows)
    return {
        "ready": ready,
        "have": have,
        "need": need,
        "rows": rows if ready else None,
    }
