"""What a source would contribute, without paying for it.

`python -m intel.shadow --source hn`

Fetches one source over the sweep's own window, runs its prefilter rules,
and prints what would have reached the scorer. No LLM call, no database
write, no page — the point is to watch a source for a few days before its
`enabled` flag is turned on in config.yaml (M2 of docs/plan-multi-source.md),
and a shadow that costs money or leaves rows behind is not a shadow.

The numbers it prints are the ones the decision needs: how much of the
window the fetch actually covered, how many items cleared the rules, and
what those items are.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

from intel import aio
from intel.pipeline import load_config, source_rules
from intel.score import prefilter
from intel.sources import collect
from intel.store import db

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def coverage_hours(items: list, now: datetime) -> float:
    """How far back the oldest fetched item actually reaches.

    A page-size ceiling can silently shorten a window — one page of Show HN
    covers about a day whatever `since_hours` asks for — and a source that
    quietly returns a third of its window looks the same from the outside as
    a quiet week.
    """
    ages = [(now - i.created_at).total_seconds() / 3600.0
            for i in items if i.created_at]
    return max(ages) if ages else 0.0


async def preview(source_name: str, limit: int) -> int:
    cfg = load_config(_PROJECT_ROOT / "config.yaml")
    now = datetime.now(UTC)
    since_date = (now - timedelta(hours=cfg.lookback_hours)).date().isoformat()

    options = next((s.options for s in cfg.sources if s.name == source_name),
                   None)
    if options is None:
        print(f"shadow: config.yaml has no source named {source_name!r}",
              file=sys.stderr)
        return 1
    fetcher = collect.FETCHERS.get(source_name)
    if fetcher is None:
        print(f"shadow: this build has no adapter for {source_name!r}",
              file=sys.stderr)
        return 1

    # A source may need the store to fetch at all (the awesome diff is
    # defined against what has been seen before). The context is marked
    # dry_run, so a list without a baseline reports what it WOULD record
    # instead of recording it — this tool leaves nothing behind.
    conn = await db.connect() if os.environ.get(db.DSN_ENV_VAR) else None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            items = await fetcher(client, options, collect.FetchContext(
                since_date=since_date, now=now, conn=conn, dry_run=True))
    finally:
        if conn is not None:
            await conn.close()

    # The rules the sweep would apply, whether or not the source is enabled
    # — the flag decides if it runs, not how it is judged.
    rules = source_rules(cfg).get(source_name) or prefilter.PrefilterRules(
        max_candidates=cfg.max_candidates,
        min_description_chars=int(options.get("min_description_chars", 0)),
        min_star_velocity_per_day=float(
            options.get("min_star_velocity_per_day", 0.0)),
        min_points_per_hour=float(options.get("min_points_per_hour", 0.0)),
        min_points=int(options.get("min_points", 0)),
        drop_forks=bool(options.get("drop_forks", source_name == "github")),
        burst_min_cluster=int(options.get("star_farm_cluster", 0)),
        burst_window_minutes=int(options.get("star_farm_window_minutes", 60)),
    )
    # The same two-stage shape the sweep uses: bought batches leave first,
    # then the per-item lines are applied to what is left. A shadow that
    # skipped this would report a farm as admitted candidates.
    kept, farmed = prefilter.drop_bursts(items, {source_name: rules})
    admitted = prefilter.apply(kept, rules, now)

    enabled = any(s.name == source_name and s.enabled for s in cfg.sources)
    print(f"shadow: {source_name} · enabled={enabled} · window asked "
          f"{cfg.lookback_hours}h, covered {coverage_hours(items, now):.0f}h")
    if farmed:
        print(f"bought batches dropped: {farmed[source_name]} "
              f"(≥{rules.burst_min_cluster} rows sharing a star count within "
              f"{rules.burst_window_minutes} min)")
    print(f"fetched {len(items)} · admitted {len(admitted)} "
          f"(of a {rules.max_candidates}-candidate daily cap shared with "
          f"every other source)")
    print()
    for item in admitted[:limit]:
        age = (f"{(now - item.created_at).total_seconds() / 3600:5.1f}h"
               if item.created_at else "    ?")
        print(f"  {item.stars:5d} | {age} | {item.name[:66]}")
        print(f"        {item.url}")
    return 0


async def _main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="intel.shadow",
        description="what a source would contribute — no LLM, no writes")
    parser.add_argument("--source", default="hn",
                        help="source name as it appears in config.yaml")
    parser.add_argument("--limit", type=int, default=20,
                        help="how many admitted items to print")
    args = parser.parse_args(argv)
    return await preview(args.source, args.limit)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(_PROJECT_ROOT / ".env")
    try:
        return aio.run(_main_async(sys.argv[1:]))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # CLI boundary — report, don't traceback-spam
        print(f"shadow failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
