"""Async Postgres persistence for the intel state layer.

One function per statement family; each write commits on its own connection
so a failed run leaves only the rows it managed to finish. The DSN comes
from the TIERLIST_PG_DSN environment variable and is never echoed — status
output exposes the host component only.
"""
from __future__ import annotations

import os
from datetime import date, datetime

from psycopg import AsyncConnection, conninfo
from psycopg.rows import Row
from psycopg.types.json import Json

from intel.score.llm import ScoredItem
from intel.sources.base import Item
from intel.store.schema import SCHEMA_STATEMENTS

DSN_ENV_VAR = "TIERLIST_PG_DSN"

_schema_ready = False


def dsn_host(dsn: str) -> str:
    """Host component of a DSN, for one-line status output."""
    try:
        parsed = conninfo.conninfo_to_dict(dsn)
    except Exception:
        return "<unparsed>"
    return str(parsed.get("host") or "localhost")


async def connect() -> AsyncConnection:
    """Open the intel connection; the DSN must be set in the environment."""
    dsn = os.environ.get(DSN_ENV_VAR)
    if not dsn:
        raise RuntimeError(
            f"{DSN_ENV_VAR} is not set — no Postgres DSN for the intel store")
    return await AsyncConnection.connect(dsn)


async def ensure_schema(conn: AsyncConnection) -> None:
    """Create schema and tables; every statement is IF NOT EXISTS-idempotent,
    and the batch runs at most once per process."""
    global _schema_ready
    if _schema_ready:
        return
    for stmt in SCHEMA_STATEMENTS:
        await conn.execute(stmt)
    await conn.commit()
    _schema_ready = True


async def upsert_items(conn: AsyncConnection, items: list[Item],
                       vertical: str) -> int:
    """Insert-or-refresh one fetched batch; returns how many rows were new.

    A conflict bumps last_seen and refreshes the descriptive fields (stars
    are NOT item state — they live on the daily scores snapshots). Duplicate
    ids within one batch keep their first occurrence.
    """
    if not items:
        return 0
    unique: dict[str, Item] = {}
    for item in items:
        unique.setdefault(item.id, item)
    columns = [list(col) for col in zip(*[
        (i.id, vertical, i.source, i.url, i.name, i.description, i.lang,
         i.created_at, Json(i.raw))
        for i in unique.values()
    ], strict=True)]
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO intel.items
                (id, vertical, source, url, name, description, lang,
                 created_at, raw)
            SELECT * FROM unnest(
                %s::text[], %s::text[], %s::text[], %s::text[], %s::text[],
                %s::text[], %s::text[], %s::timestamptz[], %s::jsonb[])
            ON CONFLICT (id) DO UPDATE SET
                last_seen = now(),
                vertical = EXCLUDED.vertical,
                source = EXCLUDED.source,
                url = EXCLUDED.url,
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                lang = EXCLUDED.lang,
                created_at = COALESCE(EXCLUDED.created_at,
                                      intel.items.created_at),
                raw = COALESCE(EXCLUDED.raw, intel.items.raw)
            RETURNING (xmax = 0) AS inserted
            """,
            columns,
        )
        inserted = [row[0] for row in await cur.fetchall()]
    await conn.commit()
    return sum(1 for flag in inserted if flag)


async def insert_new_items(conn: AsyncConnection, items: list[Item],
                           vertical: str) -> set[str]:
    """Insert only the items the store has never held; returns their ids.

    For the recent cohort (`github_recent`), which re-reads thousands of
    repositories the store already knows. Refreshing them as `upsert_items`
    does would replace each row's `raw` with the cohort's `board_only` one,
    and a repository the search found two days ago would drop out of that
    week's new-item count."""
    if not items:
        return set()
    unique: dict[str, Item] = {}
    for item in items:
        unique.setdefault(item.id, item)
    columns = [list(col) for col in zip(*[
        (i.id, vertical, i.source, i.url, i.name, i.description, i.lang,
         i.created_at, Json(i.raw))
        for i in unique.values()
    ], strict=True)]
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO intel.items
                (id, vertical, source, url, name, description, lang,
                 created_at, raw)
            SELECT * FROM unnest(
                %s::text[], %s::text[], %s::text[], %s::text[], %s::text[],
                %s::text[], %s::text[], %s::timestamptz[], %s::jsonb[])
            ON CONFLICT (id) DO NOTHING
            RETURNING id
            """,
            columns,
        )
        inserted = {row[0] for row in await cur.fetchall()}
    await conn.commit()
    return inserted


async def seen_ids_with_prefix(conn: AsyncConnection,
                               prefix: str) -> set[str]:
    """Every item id already stored under `prefix` — the awesome-list diff's
    memory of what a list contained last time.

    `starts_with` rather than LIKE: a repository name may contain an
    underscore, which LIKE reads as "any character", so `awesome:a/b_c:`
    would also match another list whose name differs in exactly that
    position.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id FROM intel.items WHERE starts_with(id, %s)",
            (prefix,),
        )
        return {row[0] for row in await cur.fetchall()}


async def filter_new_candidates(conn: AsyncConnection,
                                candidates: list[Item]) -> list[Item]:
    """Keep candidates with no intel.scores row at all — never scored before.
    Input order is preserved so prefilter ranking survives."""
    if not candidates:
        return []
    ids = [c.id for c in candidates]
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT item_id FROM intel.scores WHERE item_id = ANY(%s)", (ids,))
        seen = {row[0] for row in await cur.fetchall()}
    return [c for c in candidates if c.id not in seen]


async def save_scores(conn: AsyncConnection, scored: list[ScoredItem],
                      items_by_id: dict[str, Item], snap_date: date) -> None:
    """One snapshot row per scored item; same-day reruns overwrite in place.
    Parsed tags ride along into intel.items.tags (the item's current label
    set — a later scoring pass simply overwrites it)."""
    rows = []
    for s in scored:
        item = items_by_id.get(s.item_id)
        rows.append((s.item_id, snap_date, s.score, Json(s.dims), s.reason,
                     s.verdict, item.stars if item else None, s.model_version,
                     s.reason_en or None))
    if not rows:
        return
    async with conn.cursor() as cur:
        await cur.executemany(
            """
            INSERT INTO intel.scores
                (item_id, snap_date, score, dims, reason, verdict, stars,
                 model_version, reason_en)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (item_id, snap_date) DO UPDATE SET
                score = EXCLUDED.score,
                dims = EXCLUDED.dims,
                reason = EXCLUDED.reason,
                verdict = EXCLUDED.verdict,
                stars = EXCLUDED.stars,
                model_version = EXCLUDED.model_version,
                reason_en = EXCLUDED.reason_en
            """,
            rows,
        )
        tag_rows = [(Json(s.tags), s.item_id) for s in scored if s.tags]
        if tag_rows:
            await cur.executemany(
                "UPDATE intel.items SET tags = %s WHERE id = %s",
                tag_rows,
            )
    await conn.commit()


async def save_tags(conn: AsyncConnection, item_id: str,
                    tags: list[str]) -> None:
    """Overwrite one item's tag array; the raw SQL half of the tag writers."""
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE intel.items SET tags = %s WHERE id = %s",
            (Json(tags), item_id),
        )
    await conn.commit()


async def set_tags_for_item(conn: AsyncConnection, item_id: str,
                            tags: list[str]) -> None:
    """Normalized single-item setter — the backfill's write entry point.
    An empty/invalid list is stored as an empty array so the backfill never
    re-picks the same item (`tags IS NULL` is the work queue)."""
    clean = [t for t in (tags or []) if isinstance(t, str) and t.strip()]
    await save_tags(conn, item_id, clean)


async def tagged_items(conn: AsyncConnection) -> list[tuple[str, list]]:
    """Every item that has a tag array, as (id, tags), in id order."""
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, tags FROM intel.items WHERE tags IS NOT NULL "
            "ORDER BY id COLLATE \"C\"")
        return [(row[0], row[1]) for row in await cur.fetchall()]


async def save_tags_many(conn: AsyncConnection,
                         rows: list[tuple[str, list[str]]]) -> None:
    """Overwrite the tag arrays of many items in one transaction."""
    if not rows:
        return
    async with conn.cursor() as cur:
        await cur.executemany(
            "UPDATE intel.items SET tags = %s WHERE id = %s",
            [(Json(tags), item_id) for item_id, tags in rows])
    await conn.commit()


async def untagged_ids(conn: AsyncConnection, ids: list[str]) -> set[str]:
    """Which of `ids` have never been tagged (`tags IS NULL`)."""
    if not ids:
        return set()
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id FROM intel.items WHERE id = ANY(%s) AND tags IS NULL",
            (ids,))
        return {row[0] for row in await cur.fetchall()}


async def tags_for_ids(conn: AsyncConnection,
                       ids: list[str]) -> dict[str, list[str]]:
    """{item_id: tags} for `ids`; untagged items map to []."""
    if not ids:
        return {}
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, tags FROM intel.items WHERE id = ANY(%s)", (ids,))
        return {row[0]: list(row[1] or []) for row in await cur.fetchall()}


async def upsert_star_counts(conn: AsyncConnection, counts: dict[str, int],
                             snap_date: date) -> int:
    """Write {item_id: stars} as one day's snapshots; returns rows written.

    The single writer for intel.star_snapshots: the sweep passes the fetch's
    own star counts through it, and the re-snapshot pass passes the counts it
    re-read for items too old for the fetch to see. Same-day reruns overwrite
    in place, so the two callers can run in either order on the same date.
    """
    if not counts:
        return 0
    async with conn.cursor() as cur:
        await cur.executemany(
            """
            INSERT INTO intel.star_snapshots (item_id, snap_date, stars)
            VALUES (%s, %s, %s)
            ON CONFLICT (item_id, snap_date) DO UPDATE SET
                stars = EXCLUDED.stars
            """,
            [(item_id, snap_date, stars) for item_id, stars in counts.items()],
        )
    await conn.commit()
    return len(counts)


async def seed_star_counts(conn: AsyncConnection, counts: dict[str, int],
                           snap_date: date) -> int:
    """Write {item_id: stars} for `snap_date` only where no snapshot exists.

    For a reconstructed count (GitHub Trending's "stars today" subtracted
    from the current total), which must never overwrite a measured one.
    Returns how many rows were inserted."""
    if not counts:
        return 0
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO intel.star_snapshots (item_id, snap_date, stars)
            SELECT * FROM unnest(%s::text[], %s::date[], %s::int[])
            ON CONFLICT (item_id, snap_date) DO NOTHING
            """,
            (list(counts), [snap_date] * len(counts), list(counts.values())),
        )
        inserted = cur.rowcount
    await conn.commit()
    return inserted


async def snapshot_cohort(conn: AsyncConnection, *, track_days: int,
                          limit: int) -> list[str]:
    """The item ids whose stars are re-read every day, best claim first.

    Two populations, for two consumers:

    * **anything ever scored** — the calibration cohort. A score is a
      prediction about a project, and a prediction nobody measures is worth
      nothing, so these are followed for as long as GitHub still answers.
    * **anything first seen inside `track_days`** — the gain boards. They
      read the latest snapshot date, so an item missing from today's refresh
      cannot reach any board today however fast it is growing.

    Scored items sort first, so a `limit` small enough to bite sheds the
    board population rather than the measurement one.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT i.id
            FROM intel.items i
            LEFT JOIN LATERAL (
                SELECT 1 AS scored FROM intel.scores s
                WHERE s.item_id = i.id LIMIT 1
            ) sc ON true
            WHERE i.source = 'github'
              AND (sc.scored IS NOT NULL
                   OR i.first_seen >= now() - make_interval(days => %s))
            ORDER BY (sc.scored IS NULL), i.first_seen DESC, i.id COLLATE "C"
            LIMIT %s
            """,
            (track_days, limit),
        )
        return [row[0] for row in await cur.fetchall()]


async def snapshot_coverage(conn: AsyncConnection) -> dict:
    """How far the star series actually reaches, per item and overall.

    `spanning_items` counts items whose OWN snapshots span 30 days, which is
    the only number that says whether a 30-day board or a 30-day calibration
    horizon can exist. The table's own span says nothing about it: until the
    re-snapshot pass, every item held about three days of history inside a
    table that had been growing for weeks.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT COUNT(*), MIN(first_snap), MAX(last_snap),
                   COUNT(*) FILTER (WHERE span >= 30), MAX(span)
            FROM (
                SELECT item_id,
                       MIN(snap_date) AS first_snap,
                       MAX(snap_date) AS last_snap,
                       MAX(snap_date) - MIN(snap_date) AS span
                FROM intel.star_snapshots
                GROUP BY item_id
            ) t
            """
        )
        row = await cur.fetchone()
    return {"items": int(row[0] or 0), "first": row[1], "last": row[2],
            "spanning_items": int(row[3] or 0),
            "longest_span": int(row[4] or 0)}


async def save_snapshots(conn: AsyncConnection, items: list[Item],
                         snap_date: date) -> None:
    """Upsert every fetched item's star count for the day — the gain board's
    time series. Same-day reruns overwrite; duplicate ids keep first.

    It covers the day's fetch only, which reaches three days back at most
    (the lookback window). Every older item is refreshed by
    `intel.resnapshot`, writing through the same `upsert_star_counts`.
    """
    unique: dict[str, int] = {}
    for item in items:
        unique.setdefault(item.id, item.stars)
    await upsert_star_counts(conn, unique, snap_date)


async def save_rollup(conn: AsyncConnection, vertical: str, snap_date: date,
                      new_count: int, top_ids: list[str],
                      lang_dist: dict) -> None:
    """Upsert the vertical's daily summary row the report reads back."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO intel.daily_rollup
                (vertical, snap_date, new_count, top_ids, lang_dist)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (vertical, snap_date) DO UPDATE SET
                new_count = EXCLUDED.new_count,
                top_ids = EXCLUDED.top_ids,
                lang_dist = EXCLUDED.lang_dist
            """,
            (vertical, snap_date, new_count, top_ids, Json(lang_dist)),
        )
    await conn.commit()


async def items_seen_on(conn: AsyncConnection, vertical: str,
                        day: date) -> list[Item]:
    """Every item the run on `day` discovered, rebuilt as `Item`s.

    The cohort of a past day, for rescoring it without a refetch: `first_seen`
    is the row's own record of which sweep first saw it, and a search window
    that has since moved on cannot return those repositories again. Stars come
    from that day's snapshot, so a rescored item carries the count it had then
    rather than the one it has now.

    `raw.baseline` and `raw.board_only` rows are left out, as in the new-item
    count: an awesome-list's standing catalogue and the star-only cohorts
    (github_trending, github_recent) are recorded but never scored by the
    live sweep, so rescoring them would bill for thousands of rows the day
    never rated (2,000 or more on 2026-10-16) and add them to its issue.
    """
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT i.id, i.source, i.url, i.name, i.description, i.lang,
                   i.created_at, i.raw, s.stars
            FROM intel.items i
            LEFT JOIN intel.star_snapshots s
                   ON s.item_id = i.id AND s.snap_date = %s
            WHERE i.vertical = %s AND i.first_seen::date = %s
              AND COALESCE(i.raw->>'baseline', '') <> 'true'
              AND COALESCE(i.raw->>'board_only', '') <> 'true'
            ORDER BY i.id COLLATE "C"
            """,
            (day, vertical, day),
        )
        rows = list(await cur.fetchall())

    items = []
    for row in rows:
        raw = row["raw"] or {}
        stars = row["stars"]
        if stars is None:
            stars = int(raw.get("stargazers_count") or raw.get("points") or 0)
        items.append(Item(
            id=row["id"], source=row["source"], url=row["url"],
            name=row["name"], description=row["description"] or "",
            lang=row["lang"], stars=int(stars),
            created_at=row["created_at"], raw=raw,
        ))
    return items


async def run_started_on(conn: AsyncConnection, vertical: str,
                         day: date) -> datetime | None:
    """When the day's first sweep opened, whatever became of it.

    `run_stats_for_date` answers only for a run that finished 'ok', which is
    the wrong question for a day that failed: a rescore needs the clock the
    prefilter was applied against, and a failed run had one.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT MIN(started_at) FROM intel.runs
            WHERE vertical = %s AND started_at >= %s::date
              AND started_at < %s::date + 1
            """,
            (vertical, day, day),
        )
        row = await cur.fetchone()
    return row[0] if row else None


async def scored_ids_on(conn: AsyncConnection, day: date) -> set[str]:
    """Item ids that already carry a score for `day` — what a rescore skips
    so a second pass cannot pay for the same judgment twice."""
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT item_id FROM intel.scores WHERE snap_date = %s", (day,))
        return {row[0] for row in await cur.fetchall()}


async def recent_scores(conn: AsyncConnection, days: int = 7) -> list[Row]:
    """Scores joined items for the past `days` days — trend annotation fuel.
    Namedtuple rows: trend.annotate_repeat reads columns as attributes."""
    from psycopg.rows import namedtuple_row

    async with conn.cursor(row_factory=namedtuple_row) as cur:
        await cur.execute(
            """
            SELECT s.item_id, s.snap_date, s.score, s.stars,
                   i.name, i.url, i.lang
            FROM intel.scores s
            JOIN intel.items i ON i.id = s.item_id
            WHERE s.snap_date >= CURRENT_DATE - %s::int
            ORDER BY s.snap_date DESC, s.score DESC, i.id COLLATE "C"
            """,
            (days,),
        )
        return list(await cur.fetchall())


async def start_run(conn: AsyncConnection, vertical: str, model: str) -> int:
    """Open an audit row for this sweep and return its id."""
    async with conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO intel.runs (vertical, model) VALUES (%s, %s)"
            " RETURNING id",
            (vertical, model),
        )
        row = await cur.fetchone()
    await conn.commit()
    return int(row[0])


async def finish_run(conn: AsyncConnection, run_id: int, *, status: str,
                     fetched: int, candidates: int, scored: int, model: str,
                     usage: dict, error: str | None,
                     hot_new: list[dict] | None = None,
                     trending: dict | None = None) -> None:
    """Close the audit row with outcome counts, the provider token report and
    the run's 新仓库热榜.

    `hot_new` is the board in display shape (see editions.hot_new_rows). It
    is stored here rather than derived later because the board is a slice of
    the raw fetch taken BEFORE the prefilter: those repositories may never
    reach intel.items, so once the process exits the only remaining copy was
    the HTML of the page that happened to be written that minute. Every
    reader that rebuilds a page from the store — the news cron's landing
    page, `--rebuild-site`, a backfilled permalink — silently produced a
    page without the board."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE intel.runs SET
                status = %s,
                finished_at = now(),
                fetched = %s,
                candidates = %s,
                scored = %s,
                model = %s,
                usage = %s,
                error = %s,
                hot_new = COALESCE(%s::jsonb, intel.runs.hot_new),
                trending = COALESCE(%s::jsonb, intel.runs.trending)
            WHERE id = %s
            """,
            (status, fetched, candidates, scored, model, Json(usage), error,
             Json(hot_new) if hot_new else None,
             Json(trending) if trending else None, run_id),
        )
    await conn.commit()


async def edition_stats(conn: AsyncConnection, since_date: str,
                        until_date: str | None = None) -> dict:
    """Period summary for the weekly/monthly lede: items first seen in the
    window, distinct items scored in it, and the window's token spend (same
    $/token rates as the brief's cost line).

    `until_date` closes the window at the top (default today). A live run
    never has anything past today to exclude; a rebuild of a past issue
    does, and an open-ended window would print a later run's token spend on
    an older page. `intel.runs.started_at` is a timestamptz, so its bound is
    the exclusive start of the following day rather than the date itself.

    Rows marked `raw.baseline` are excluded from the new-item count: they
    are an awesome-list's standing catalogue, recorded the first time that
    list was read so its later additions can be told apart. Counting them
    would report 3891 discoveries on a day that discovered none of them.
    `raw.board_only` rows (github_trending) are excluded for the same reason:
    an established repository on the trending page is not a discovery."""
    from intel.deliver.context import COMPLETION_USD_PER_TOKEN, PROMPT_USD_PER_TOKEN

    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM intel.items
               WHERE first_seen >= %s::date
                 AND first_seen < COALESCE(%s::date, CURRENT_DATE) + 1
                 AND COALESCE(raw->>'baseline', '') <> 'true'
                 AND COALESCE(raw->>'board_only', '') <> 'true')
                AS new_count,
              (SELECT COUNT(DISTINCT item_id) FROM intel.scores
               WHERE snap_date >= %s::date AND score IS NOT NULL
                 AND snap_date <= COALESCE(%s::date, CURRENT_DATE))
                AS scored_count,
              (SELECT COALESCE(SUM((usage->>'prompt_tokens')::bigint), 0)
               FROM intel.runs
               WHERE started_at >= %s::date AND status = 'ok'
                 AND started_at < COALESCE(%s::date, CURRENT_DATE) + 1)
                AS prompt_tokens,
              (SELECT COALESCE(SUM((usage->>'completion_tokens')::bigint), 0)
               FROM intel.runs
               WHERE started_at >= %s::date AND status = 'ok'
                 AND started_at < COALESCE(%s::date, CURRENT_DATE) + 1)
                AS completion_tokens
            """,
            (since_date, until_date, since_date, until_date,
             since_date, until_date, since_date, until_date),
        )
        row = await cur.fetchone()
    # SUM(bigint) comes back as numeric → Decimal; the rate math below is
    # float, so coerce here or the multiply raises TypeError in production.
    new_count, scored_count, prompt_tokens, completion_tokens = (
        int(v or 0) for v in row)
    cost = prompt_tokens * PROMPT_USD_PER_TOKEN \
        + completion_tokens * COMPLETION_USD_PER_TOKEN
    return {
        "new_count": int(new_count or 0),
        "scored_count": int(scored_count or 0),
        "cost_sum": round(cost, 4),
    }


async def run_stats_for_date(conn: AsyncConnection, vertical: str,
                             day: date) -> dict | None:
    """The sweep counters recorded for `vertical` on `day` — fetched,
    candidates, scored, model and token usage — or None if no successful run
    that day. The last successful run of the day wins (a manual re-run
    supersedes the cron's).

    Exists so a rebuilt back issue prints the lede the sweep actually
    reported instead of a row of blanks: a page that silently drops its
    provenance is worse than one that never had it. The run's 新仓库热榜
    rides along in the same row for the same reason — see `finish_run`.
    It is NULL for every run that finished before the column existed;
    `hot_new_for_date` reconstructs those from the star snapshots."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT fetched, candidates, scored, model, usage, hot_new,
                   trending, started_at
            FROM intel.runs
            WHERE vertical = %s AND status = 'ok'
              AND started_at >= %s::date
              AND started_at < %s::date + 1
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (vertical, day, day),
        )
        return await cur.fetchone()


async def latest_run_date(conn: AsyncConnection, vertical: str,
                          on_or_before: date) -> date | None:
    """The date of the newest successful sweep for `vertical` on or before
    `on_or_before`, or None if there is none.

    The landing page is rebuilt every 15 minutes by the news cron. Between
    00:00 UTC and the end of that day's sweep, today has no scores yet, so
    anchoring the landing to the clock rendered an empty 今日精选 (observed
    2026-10-18: 进榜 0 for twenty minutes while the sweep was still
    scoring). Anchoring to the last finished run keeps yesterday's issue up
    until today's exists."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT max(started_at)::date FROM intel.runs
            WHERE vertical = %s AND status = 'ok'
              AND started_at < %s::date + 1
            """,
            (vertical, on_or_before),
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def hot_new_for_date(conn: AsyncConnection, day: date) -> list[dict]:
    """Reconstruct a day's 新仓库热榜 from the star snapshots, in display
    shape. Empty list when that day has no snapshots.

    The board is the top ten of one day's fetch by stars, and
    `save_snapshots` writes a row for EVERY item of that fetch — so the two
    are the same population and this query reproduces the published board
    rather than approximating it. Verified against a page still holding the
    original: 2026-08-27, ten of ten identical in name, order and star count.

    This is the fallback, not the record. `intel.runs.hot_new` holds what was
    actually rendered and wins when present; this reaches back to the runs
    that finished before that column existed, which is every run before
    2026-08-27. It is a derivation, so it drifts if the fetch ever stops
    being star-ordered or the snapshot population stops matching the fetch —
    which is exactly why it does not replace storing the board.
    """
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT i.name, i.url, s.stars, i.description
            FROM intel.star_snapshots s
            JOIN intel.items i ON i.id = s.item_id
            WHERE s.snap_date = %s
            ORDER BY s.stars DESC, i.id COLLATE "C"
            LIMIT 10
            """,
            (day,),
        )
        rows = await cur.fetchall()
    return [{"rank": f"{n:02d}", "name": r["name"], "url": r["url"],
             "stars": r["stars"], "description": r["description"] or ""}
            for n, r in enumerate(rows, 1)]


async def search_rows(conn: AsyncConnection) -> list[Row]:
    """The client-side search index: every item that is reachable from
    somewhere on the site, with its best score (NULL if never scored), tags
    as a plain list, and the display fields the /search/ page filters on.
    Dict rows; the index writer serializes them as JSON.

    Membership is `scored OR tagged`, not `scored` alone. The tag pages are
    built from `items.tags` (site.tag_cloud) while this index used to
    require a score row, so the two disagreed on 83 items: 40 owned a tag
    page that search could not find, 43 were searchable but absent from the
    tag cloud. A tag page linking to a project that /search/ swears does not
    exist is the visible half of that split."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT DISTINCT ON (i.id COLLATE "C")
                   i.id, i.name, i.url, i.lang,
                   COALESCE(i.tags, '[]'::jsonb) AS tags,
                   i.description AS description,
                   x.score, x.dims, x.model_version, y.snap_date
            FROM intel.items i
            LEFT JOIN LATERAL (
                SELECT score, dims, model_version
                FROM intel.scores s WHERE s.item_id = i.id AND score IS NOT NULL
                ORDER BY score DESC LIMIT 1
            ) x ON true
            LEFT JOIN LATERAL (
                SELECT MAX(snap_date) AS snap_date
                FROM intel.scores s WHERE s.item_id = i.id AND score IS NOT NULL
            ) y ON true
            WHERE x.score IS NOT NULL
               OR jsonb_array_length(COALESCE(i.tags, '[]'::jsonb)) > 0
            ORDER BY i.id COLLATE "C", x.score DESC NULLS LAST
            """
        )
        return list(await cur.fetchall())


async def page_input_digests(conn: AsyncConnection) -> dict[str, dict]:
    """`{kind: {date: md5}}` for every row that can reach an issue page.

    Four grouped digests in ONE round trip — `scores` (with the `items`
    display columns each score is rendered beside), `items` (the lede's
    新增 count), `runs` (the lede's provenance and cost) and
    `star_snapshots` (the 新仓库热榜 and the whole gain board). A page is
    re-rendered when any digest its window covers differs from the one
    recorded when it was last written; see `deliver.manifest`.

    Every column a page can display is inside a digest, not a curated
    subset: the point of this query is to be wrong in the direction of
    rendering too much. `COALESCE(..., '')` is what makes that true —
    `concat_ws` DROPS a NULL argument, so an un-coalesced field would let a
    description moving into an empty reason hash the same as before.

    `first_seen::date` and `started_at::date` are timestamptz casts, which
    resolve in the session timezone — the same one `edition_stats` compares
    its own `>= %s::date` bounds in, so the two agree on which day a row
    belongs to whatever the server is set to.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT 'scores' AS kind, s.snap_date AS day,
                   md5(string_agg(concat_ws(chr(31),
                       s.item_id,
                       COALESCE(s.score::text, ''),
                       COALESCE(s.dims::text, ''),
                       COALESCE(s.model_version, ''),
                       COALESCE(s.reason, ''),
                       COALESCE(s.reason_en, ''),
                       COALESCE(s.verdict, ''),
                       COALESCE(s.stars::text, ''),
                       COALESCE(i.name, ''),
                       COALESCE(i.url, ''),
                       COALESCE(i.lang, ''),
                       COALESCE(i.description, ''),
                       COALESCE(i.description_en, ''),
                       COALESCE(i.tags::text, ''),
                       COALESCE(i.source, '')),
                       chr(10) ORDER BY s.item_id COLLATE "C")) AS d
            FROM intel.scores s
            JOIN intel.items i ON i.id = s.item_id
            GROUP BY s.snap_date

            UNION ALL
            SELECT 'items', i.first_seen::date,
                   md5(string_agg(concat_ws(chr(31),
                       i.id, COALESCE(i.raw->>'baseline', '')),
                       chr(10) ORDER BY i.id COLLATE "C"))
            FROM intel.items i
            GROUP BY i.first_seen::date

            UNION ALL
            SELECT 'runs', r.started_at::date,
                   md5(string_agg(concat_ws(chr(31),
                       r.id::text,
                       COALESCE(r.vertical, ''),
                       COALESCE(r.status, ''),
                       COALESCE(r.fetched::text, ''),
                       COALESCE(r.candidates::text, ''),
                       COALESCE(r.scored::text, ''),
                       COALESCE(r.model, ''),
                       COALESCE(r.usage::text, ''),
                       COALESCE(r.hot_new::text, ''),
                       COALESCE(r.trending::text, '')),
                       chr(10) ORDER BY r.id))
            FROM intel.runs r
            GROUP BY r.started_at::date

            UNION ALL
            SELECT 'snaps', ss.snap_date,
                   md5(string_agg(concat_ws(chr(31),
                       ss.item_id, ss.stars::text),
                       chr(10) ORDER BY ss.item_id COLLATE "C"))
            FROM intel.star_snapshots ss
            GROUP BY ss.snap_date
            """
        )
        rows = await cur.fetchall()
    out: dict[str, dict] = {"scores": {}, "items": {}, "runs": {},
                            "snaps": {}}
    for kind, day, d in rows:
        if day is not None:
            out.setdefault(kind, {})[day] = d
    return out


async def issue_dates(conn: AsyncConnection) -> list[date]:
    """Every date that has at least one real score — the archive's ground
    truth for which issues exist."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT DISTINCT snap_date FROM intel.scores
            WHERE score IS NOT NULL
            ORDER BY snap_date
            """
        )
        return [row[0] for row in await cur.fetchall()]


async def window_score_rows(conn: AsyncConnection, days: int,
                            as_of: date | None = None) -> list[Row]:
    """Best score per item in the `days`-wide window ending at `as_of`
    (default today). `days=0` means that one day only — used by the daily
    landing, so yesterday's picks can never leak into a board titled
    今日精选. Weekly/monthly pass 7/30. Ordering and limiting happen in
    editions.best_per_item (pure, tested).
    Dict rows: that consumer reads columns as r["col"].

    The window is closed at BOTH ends. The upper bound is a no-op for a
    live run (no snapshot carries a future date) and load-bearing for a
    rebuild of a past issue: without it, regenerating 2026-08-23 would
    stamp it with entries first scored on 08-25."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT DISTINCT ON (s.item_id)
                   s.item_id, s.score, s.dims, s.reason, s.reason_en,
                   s.verdict, s.snap_date, s.stars, s.model_version,
                   i.name, i.url, i.lang, i.description, i.tags, i.source
            FROM intel.scores s
            JOIN intel.items i ON i.id = s.item_id
            WHERE s.snap_date >= COALESCE(%s::date, CURRENT_DATE) - %s::int
              AND s.snap_date <= COALESCE(%s::date, CURRENT_DATE)
              AND s.score IS NOT NULL
            ORDER BY s.item_id, s.score DESC, s.snap_date DESC
            """,
            (as_of, days, as_of),
        )
        return list(await cur.fetchall())


async def feed_rows(conn: AsyncConnection, *, days: int = 14,
                    limit: int = 30) -> list[Row]:
    """The Atom feed's items: best score per item across the trailing window,
    score-desc — same selection as the report's own Top, so RSS readers see
    exactly what the site shows. Dict rows: r['col'].

    DISTINCT ON needs the rows ordered by item_id, so the best-per-item pick
    happens in the inner query and the score order and LIMIT in the outer
    one; a LIMIT on the inner query would take the first ids, not the best."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT * FROM (
                SELECT DISTINCT ON (s.item_id)
                       s.item_id, s.score, s.dims, s.reason, s.reason_en,
                       s.snap_date, s.model_version, i.name, i.url
                FROM intel.scores s
                JOIN intel.items i ON i.id = s.item_id
                WHERE s.snap_date >= CURRENT_DATE - %s::int
                  AND s.score IS NOT NULL
                ORDER BY s.item_id, s.score DESC, s.snap_date DESC
            ) best
            ORDER BY score DESC, snap_date DESC, item_id COLLATE "C"
            LIMIT %s
            """,
            (days, limit),
        )
        return list(await cur.fetchall())


async def upsert_news(conn: AsyncConnection, items: list[Item]) -> int:
    """Insert-or-keep one news batch; returns how many rows were new. An
    existing id is left untouched (news edits happen on the source side;
    we record each item once, first-seen wins)."""
    if not items:
        return 0
    unique: dict[str, Item] = {}
    for item in items:
        unique.setdefault(item.id, item)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO intel.news
                (id, source, feed, url, title, summary, points, published_at)
            SELECT * FROM unnest(
                %s::text[], %s::text[], %s::text[], %s::text[], %s::text[],
                %s::text[], %s::int[], %s::timestamptz[])
            ON CONFLICT (id) DO NOTHING
            RETURNING (xmax = 0) AS inserted
            """,
            [list(col) for col in zip(*[
                (i.id, i.source, i.feed, i.url, i.name, i.description,
                 i.stars, i.created_at)
                for i in unique.values()
            ], strict=True)],
        )
        inserted = [row[0] for row in await cur.fetchall()]
    await conn.commit()
    return sum(1 for flag in inserted if flag)


async def news_recent(conn: AsyncConnection, *, limit: int = 60,
                      ticker_limit: int = 6) -> list[Row]:
    """The news feed rows, newest published first (dates-NULL last — RSS
    items without a parseable date still surface). Dict rows: r['col']."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT title, url, source, points, published_at, summary
            FROM intel.news
            ORDER BY published_at DESC NULLS LAST, first_seen DESC
            LIMIT %s
            """,
            (limit,),
        )
        return list(await cur.fetchall())


async def news_count(conn: AsyncConnection) -> int:
    """Total rows in the news table (milestone diagnostics for the cron log)."""
    async with conn.cursor() as cur:
        await cur.execute("SELECT COUNT(*) FROM intel.news")
        row = await cur.fetchone()
    return int(row[0])


async def run_cost_rows(conn: AsyncConnection) -> list[Row]:
    """Scoring spend per UTC day from the runs' own usage reports
    (`usage.cost`, for an endpoint that returns one with each call). Dict
    rows."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT started_at::date AS day, COUNT(*) AS runs,
                   SUM((usage->>'cost')::float8) AS cost_usd,
                   string_agg(DISTINCT model, ', ') AS models
            FROM intel.runs
            WHERE usage ? 'cost'
            GROUP BY 1 ORDER BY 1
            """
        )
        return list(await cur.fetchall())


async def rated_item_rows(conn: AsyncConnection) -> list[Row]:
    """Every item with at least one rating, and all of its ratings newest
    first, in one scan: the JSON API's item documents. Dict rows."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT i.id, i.name, i.url, i.source, i.description, i.lang,
                   i.tags, i.first_seen::date AS first_seen,
                   jsonb_agg(jsonb_build_object(
                       'date', s.snap_date, 'score', s.score, 'dims', s.dims,
                       'model_version', s.model_version,
                       'reason', s.reason, 'reason_en', s.reason_en,
                       'stars', s.stars)
                       ORDER BY s.snap_date DESC) AS ratings
            FROM intel.items i
            JOIN intel.scores s ON s.item_id = i.id AND s.score IS NOT NULL
            GROUP BY i.id
            ORDER BY i.id COLLATE "C"
            """
        )
        return list(await cur.fetchall())


# ---------------------------------------------------------- tier calibration

async def tier_line_rows(conn: AsyncConnection) -> list[Row]:
    """Every measured set of tier lines, oldest first (tiers.use_store_lines)."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT vertical, model_version, dims, s_line, a_line, b_line,
                   c_line, sample, source, window_from, window_to, created_at
            FROM intel.tier_lines ORDER BY created_at, id
            """)
        return list(await cur.fetchall())


async def save_tier_lines(conn: AsyncConnection, *, vertical: str,
                          model_version: str, dims: list[str],
                          lines: dict[str, int], sample: int, source: str,
                          window_from: date | None = None,
                          window_to: date | None = None) -> None:
    """Record one version's lines; the newest row for a version is in force."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO intel.tier_lines
                (vertical, model_version, dims, s_line, a_line, b_line,
                 c_line, sample, source, window_from, window_to)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (vertical, model_version, sorted(dims), lines["S"], lines["A"],
             lines["B"], lines["C"], sample, source, window_from, window_to))
    await conn.commit()


async def calibration_cohort(conn: AsyncConnection, *, dims: list[str],
                             since: date, until: date, limit: int) -> list[Row]:
    """The items a calibration rescores: each item's latest score under the
    current rubric (`dims` is its dimension set) inside [since, until],
    newest first, up to `limit`. Dict rows carrying everything needed to
    rebuild the `Item` as it looked that day, stars included."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT * FROM (
                SELECT DISTINCT ON (s.item_id)
                       s.item_id, s.snap_date, s.stars, i.source, i.url,
                       i.name, i.description, i.lang, i.created_at, i.raw
                FROM intel.scores s
                JOIN intel.items i ON i.id = s.item_id
                WHERE s.snap_date BETWEEN %s AND %s
                  AND s.score IS NOT NULL
                  AND (SELECT array_agg(k ORDER BY k)
                       FROM jsonb_object_keys(s.dims) AS k) = %s::text[]
                ORDER BY s.item_id, s.snap_date DESC
            ) latest
            ORDER BY snap_date DESC, item_id COLLATE "C"
            LIMIT %s
            """,
            (since, until, sorted(dims), limit))
        return list(await cur.fetchall())


async def calibration_scores(conn: AsyncConnection,
                             model_version: str) -> dict[str, int]:
    """{item_id: score} a calibration of `model_version` already holds."""
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT item_id, score FROM intel.calibration_scores "
            "WHERE model_version = %s", (model_version,))
        return {r[0]: int(r[1]) for r in await cur.fetchall()}


async def save_calibration_scores(conn: AsyncConnection, model_version: str,
                                  scored: list, days: dict[str, date]) -> None:
    """Store a calibration batch. Never touches intel.scores."""
    rows = [(model_version, s.item_id, days[s.item_id], s.score, Json(s.dims))
            for s in scored if s.item_id in days]
    if not rows:
        return
    async with conn.cursor() as cur:
        await cur.executemany(
            """
            INSERT INTO intel.calibration_scores
                (model_version, item_id, snap_date, score, dims)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (model_version, item_id) DO UPDATE SET
                score = EXCLUDED.score, dims = EXCLUDED.dims,
                scored_at = now()
            """, rows)
    await conn.commit()


async def recent_version_scores(conn: AsyncConnection, *, model_version: str,
                                since: date, until: date) -> list[int]:
    """Scores `model_version` gave inside [since, until], for the drift check."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT score FROM intel.scores
            WHERE model_version = %s AND score IS NOT NULL
              AND snap_date BETWEEN %s AND %s
            """, (model_version, since, until))
        return [int(r[0]) for r in await cur.fetchall()]


async def clear_calibration_scores(conn: AsyncConnection,
                                   model_version: str) -> None:
    """Drop what a calibration of `model_version` holds, to measure it again."""
    async with conn.cursor() as cur:
        await cur.execute("DELETE FROM intel.calibration_scores "
                          "WHERE model_version = %s", (model_version,))
    await conn.commit()


async def reasons_without_english(conn: AsyncConnection, *,
                                  limit: int | None = None) -> list[Row]:
    """Ratings with a Chinese reason and no English one, newest first: the
    queue `intel.translate_reasons` works through. Dict rows."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT item_id, snap_date, reason FROM intel.scores
            WHERE reason_en IS NULL AND COALESCE(reason, '') <> ''
            ORDER BY snap_date DESC, item_id COLLATE "C"
            LIMIT %s
            """,
            (limit,),
        )
        return list(await cur.fetchall())


async def set_reasons_en(conn: AsyncConnection,
                         rows: list[tuple[str, str, date]]) -> None:
    """Write (reason_en, item_id, snap_date) triples and commit."""
    if not rows:
        return
    async with conn.cursor() as cur:
        await cur.executemany(
            "UPDATE intel.scores SET reason_en = %s"
            " WHERE item_id = %s AND snap_date = %s",
            rows,
        )
    await conn.commit()


# A description needs English when it has letters of a script an English
# reader cannot read: Chinese, Japanese, Korean or Cyrillic.
_FOREIGN_SCRIPT = r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\u0400-\u04ff]"


async def descriptions_without_english(conn: AsyncConnection, *,
                                       limit: int | None = None) -> list[Row]:
    """Items whose description is in a foreign script and has no English
    line yet, newest first. Dict rows of (id, description)."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, description FROM intel.items
            WHERE description_en IS NULL AND description ~ %s
            ORDER BY first_seen DESC, id COLLATE "C"
            LIMIT %s
            """,
            (_FOREIGN_SCRIPT, limit),
        )
        return list(await cur.fetchall())


async def set_descriptions_en(conn: AsyncConnection,
                              rows: list[tuple[str, str]]) -> None:
    """Write (description_en, item_id) pairs and commit."""
    if not rows:
        return
    async with conn.cursor() as cur:
        await cur.executemany(
            "UPDATE intel.items SET description_en = %s WHERE id = %s", rows)
    await conn.commit()


async def english_descriptions(conn: AsyncConnection) -> dict[str, str]:
    """{url: English description} for every item that has one; the English
    pages show it in place of the author's original."""
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT url, description_en FROM intel.items"
            " WHERE description_en IS NOT NULL AND url IS NOT NULL")
        return {row[0]: row[1] for row in await cur.fetchall()}


async def tier_history_rows(conn: AsyncConnection) -> list[Row]:
    """Every rated item once, with its latest rating: the rows the tier
    history pages are built from. Newest rating first, then score, then id,
    so two machines build the same pages from the same rows. Dict rows."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT * FROM (
                SELECT DISTINCT ON (s.item_id)
                       s.item_id, s.snap_date, s.score, s.dims,
                       s.model_version, s.reason, s.reason_en, s.verdict,
                       s.stars, i.name, i.url, i.source, i.lang,
                       i.description, i.description_en, i.tags
                FROM intel.scores s
                JOIN intel.items i ON i.id = s.item_id
                WHERE s.score IS NOT NULL
                ORDER BY s.item_id, s.snap_date DESC
            ) latest
            ORDER BY snap_date DESC, score DESC, item_id COLLATE "C"
            """
        )
        return list(await cur.fetchall())
