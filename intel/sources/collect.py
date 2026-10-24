"""Fetch every enabled source for one sweep, concurrently.

The sweep used to name GitHub in a single hard-coded line. Adding a source
then meant editing the orchestration, which is exactly what the engine
promised never to require: a new source is an adapter plus a rubric line,
and `pipeline` does not learn its name.

Failure policy is deliberately asymmetric. One adapter failing while others
answer degrades the day's brief; every adapter failing means there is no
brief to write, and that must be loud. So a partial failure is reported and
skipped, and a total failure raises the first error.

Adapters receive a `FetchContext` rather than a widening list of keyword
arguments. Two of them need more than a window: the awesome-list diff needs
the store to know what it has already seen, and it needs the vertical to
record a first sighting under.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import httpx

from intel.sources import awesome, github, github_recent, github_trending, hn
from intel.sources.base import Item


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """One entry of config.yaml's `sources` block."""

    name: str
    enabled: bool
    options: dict


@dataclass(frozen=True, slots=True)
class FetchContext:
    """What every adapter may ask of the run it belongs to."""

    since_date: str
    now: datetime
    conn: object | None = None       # open store, when the run has one
    vertical: str = "dev_tools"
    # `intel.shadow` reads a source without leaving anything behind. Only
    # the awesome diff can write during a fetch (recording a list it has
    # never seen), so only it reads this flag.
    dry_run: bool = False


def enabled_sources(sources: tuple[SourceConfig, ...]
                    ) -> tuple[SourceConfig, ...]:
    return tuple(s for s in sources if s.enabled and s.name in FETCHERS)


async def _fetch_github(client: httpx.AsyncClient, options: dict,
                        ctx: FetchContext) -> list[Item]:
    return await github.fetch_new_repos(client, ctx.since_date,
                                        int(options.get("max_pages", 3)))


async def _fetch_github_trending(client: httpx.AsyncClient, options: dict,
                                 ctx: FetchContext) -> list[Item]:
    """Established repositories on github.com/trending. Board-only rows: see
    the adapter's docstring for why they are not scored."""
    periods = [str(p) for p in (options.get("periods") or ["daily"])]
    return await github_trending.fetch_trending(client, periods)


async def _fetch_github_recent(client: httpx.AsyncClient, options: dict,
                               ctx: FetchContext) -> list[Item]:
    """Every repository created within `days` holding `min_stars`, however
    long it was private. Board-only rows: see the adapter's docstring."""
    since = (ctx.now - timedelta(days=int(options.get("days", 60)))).date()
    return await github_recent.fetch_recent(
        client, since.isoformat(), int(options.get("min_stars", 100)))


async def _fetch_hn(client: httpx.AsyncClient, options: dict,
                    ctx: FetchContext) -> list[Item]:
    """Show HN over the sweep's own window, with the repository behind the
    link filled in where there is one.

    This is the same adapter the 15-minute news cron calls, asked a
    different question: the news page wants the last few hours of anything,
    the sweep wants the window the GitHub search covers. Enrichment is the
    sweep's alone — the news cron runs 96 times a day and would pay for it
    every quarter of an hour to render a title.
    """
    items = await hn.fetch_hn(
        client,
        since_hours=int(options.get("since_hours", 72)),
        hits_per_page=int(options.get("hits_per_page", 100)),
        tags=str(options.get("tags", "show_hn")),
        max_pages=int(options.get("max_pages", 3)),
    )
    if not bool(options.get("enrich_github", True)):
        return items
    return await hn.enrich_from_github(
        client, items, limit=int(options.get("enrich_limit", 40)))


async def _fetch_awesome(client: httpx.AsyncClient, options: dict,
                         ctx: FetchContext) -> list[Item]:
    """Every watchlist's additions since the last sweep, best-first.

    A list seen for the first time is recorded and contributes nothing: with
    no past to diff against, its several hundred standing entries would all
    look new and would take the whole day's paid batch for a discovery that
    happened years ago.
    """
    from intel.store import db

    watchlists = [str(w).strip() for w in (options.get("watchlists") or [])
                  if str(w).strip()]
    if not watchlists:
        return []
    if ctx.conn is None:
        print("source awesome: skipped (no store to diff against — every "
              "entry would read as new)", file=sys.stderr)
        return []

    max_new = int(options.get("max_new_per_list", 25))
    fresh: list[Item] = []
    for list_name in watchlists:
        seen = await db.seen_ids_with_prefix(ctx.conn, f"awesome:{list_name}:")
        # Close the read before the HTTP fetch: the rest of the sweep's
        # fetch runs with this connection idle, and the database role drops a
        # transaction left idle for 5 minutes.
        await ctx.conn.commit()
        entries = await awesome.new_entries(
            client, list_name, seen_ids=seen, baseline=not seen,
            max_new=max_new)
        if not seen:
            if ctx.dry_run:
                print(f"source awesome: {list_name} has no baseline yet — "
                      f"{len(entries)} entries would be recorded and none "
                      f"emitted", file=sys.stderr)
                continue
            # Marked as what they are: a catalogue import, not a day's
            # discoveries. Without the mark the issue's "new this period"
            # count reads 4172 on the day five lists are first read, of
            # which 3891 are entries that have existed for years.
            await db.upsert_items(
                ctx.conn,
                [replace(e, raw={**e.raw, "baseline": True}) for e in entries],
                ctx.vertical)
            print(f"source awesome: {list_name} baseline recorded "
                  f"({len(entries)} entries), none emitted", file=sys.stderr)
            continue
        fresh.extend(entries)
        await asyncio.sleep(awesome._PAUSE_S)

    enriched = await awesome.enrich_from_github(
        client, fresh, limit=int(options.get("enrich_limit", 40)))
    # Best-first, so the round-robin merge takes the strongest endorsement
    # each turn rather than whichever list happened to be read first.
    return sorted(enriched, key=lambda i: (-i.stars, i.id))


FETCHERS = {
    "github": _fetch_github,
    "github_trending": _fetch_github_trending,
    "github_recent": _fetch_github_recent,
    "hn": _fetch_hn,
    "awesome": _fetch_awesome,
}


async def fetch_enabled(client: httpx.AsyncClient,
                        sources: tuple[SourceConfig, ...],
                        ctx: FetchContext
                        ) -> tuple[list[Item], dict[str, int]]:
    """Every enabled source, in parallel → (all items, per-source counts).

    Items keep source order (config order), and each source's own ranking
    inside its block; the prefilter merges them fairly afterwards. The
    per-source counts are reported in the run's stats line, so a source that
    quietly returns nothing is visible rather than merely absent.
    """
    active = enabled_sources(sources)
    if not active:
        return [], {}

    results = await asyncio.gather(*[
        FETCHERS[s.name](client, s.options, ctx) for s in active
    ], return_exceptions=True)

    items: list[Item] = []
    counts: dict[str, int] = {}
    errors: list[BaseException] = []
    for source, result in zip(active, results, strict=True):
        if isinstance(result, BaseException):
            errors.append(result)
            counts[source.name] = 0
            print(f"source {source.name}: failed ({result})", file=sys.stderr)
            continue
        counts[source.name] = len(result)
        items.extend(result)
    if errors and not items:
        raise errors[0]
    return items, counts
