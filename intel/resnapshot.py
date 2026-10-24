"""Keep following the projects we already know — the daily star refresh.

The sweep only ever sees repositories created inside the lookback window, so
every project's star series used to stop about three days after it appeared:
1005 of the 1585 items in the store on 2026-08-29 held exactly ONE day of
history. Two things depend on a longer series and neither could work:

* the 7-day and 30-day gain boards, which need snapshots that actually span
  their window;
* the score calibration, which asks whether a 9 turns into more real growth
  than a 6 — a question that can only be answered 30 days after the score.

This pass closes that gap. It re-reads the current star count for every item
in the tracked cohort (`db.snapshot_cohort`) and writes it as today's
snapshot, through the same writer the sweep uses.

    python -m intel.resnapshot                 # refresh, write today's rows
    python -m intel.resnapshot --dry-run       # fetch, report, write nothing
    python -m intel.resnapshot --date 2026-08-29

It also runs at the end of every sweep, before the pages are rendered, so
the boards can see the refreshed population the same day rather than the
next one. A failure there is reported and swallowed: the day's brief is
already paid for and must not be lost to a GitHub hiccup.

GitHub's GraphQL API has no anonymous tier, so without GITHUB_TOKEN this
pass reports the skip and does nothing.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from intel import aio
from intel.sources import github_stars
from intel.store import db

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TRACK_DAYS = 45      # monthly window (30) plus slack for a late run
DEFAULT_MAX_ITEMS = 20000    # ~200 GraphQL documents; a guard, not a target


@dataclass(frozen=True, slots=True)
class RefreshResult:
    cohort: int          # items asked about
    refreshed: int       # star counts GitHub answered with
    written: int         # snapshot rows written (0 on a dry run)
    missing: int         # gone, renamed or now private

    def line(self) -> str:
        return (f"resnapshot: cohort {self.cohort} · refreshed "
                f"{self.refreshed} · written {self.written} · missing "
                f"{self.missing}")


def full_names(item_ids: list[str]) -> list[str]:
    """`github:owner/repo` ids → `owner/repo`. Ids of any other shape are
    dropped: the GraphQL query addresses repositories by owner and name, and
    only the github source has those."""
    names = []
    for item_id in item_ids:
        source, _, rest = item_id.partition(":")
        if source == "github" and "/" in rest:
            names.append(rest)
    return names


async def refresh(conn, snap_date: date, *,
                  track_days: int = DEFAULT_TRACK_DAYS,
                  limit: int = DEFAULT_MAX_ITEMS,
                  batch_size: int = github_stars.MAX_BATCH,
                  dry_run: bool = False) -> RefreshResult:
    """Re-read the cohort's stars and write them as `snap_date`'s snapshots."""
    cohort = await db.snapshot_cohort(conn, track_days=track_days, limit=limit)
    # The cohort read opened a transaction. Close it before the GitHub fetch:
    # the database role has idle_in_transaction_session_timeout=5min, and a
    # cohort of ~13,000 repositories takes longer than that to re-read, so
    # Postgres would drop the connection and the rest of the run with it.
    await conn.commit()
    names = full_names(cohort)
    if not names:
        return RefreshResult(0, 0, 0, 0)

    async with httpx.AsyncClient(timeout=github_stars._TIMEOUT_S) as client:
        counts = await github_stars.fetch_star_counts(client, names,
                                                      batch_size=batch_size)

    written = 0
    if not dry_run:
        written = await db.upsert_star_counts(
            conn, {f"github:{name}": stars for name, stars in counts.items()},
            snap_date)
    return RefreshResult(cohort=len(names), refreshed=len(counts),
                         written=written, missing=len(names) - len(counts))


async def refresh_quietly(conn, snap_date: date, *, track_days: int,
                          limit: int, batch_size: int) -> RefreshResult | None:
    """The sweep's entry point: never raises.

    The star refresh is an addition to the run, not a precondition of it —
    an expired token or a GitHub outage must cost the day its snapshots, not
    its brief. Returns None when the pass could not run; the reason is
    printed to stderr either way.
    """
    try:
        result = await refresh(conn, snap_date, track_days=track_days,
                               limit=limit, batch_size=batch_size)
    except github_stars.MissingToken as exc:
        print(f"resnapshot: skipped ({exc})", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"resnapshot: failed ({exc})", file=sys.stderr)
        return None
    print(result.line(), file=sys.stderr)
    return result


async def _main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="intel.resnapshot",
        description="re-read the tracked cohort's GitHub stars and write "
                    "today's star snapshots")
    parser.add_argument("--track-days", type=int, default=DEFAULT_TRACK_DAYS,
                        help="also follow unscored items first seen inside "
                             f"this many days (default {DEFAULT_TRACK_DAYS}); "
                             "scored items are followed regardless")
    parser.add_argument("--limit", type=int, default=DEFAULT_MAX_ITEMS,
                        help=f"cohort ceiling (default {DEFAULT_MAX_ITEMS})")
    parser.add_argument("--batch", type=int, default=github_stars.MAX_BATCH,
                        help="repositories per GraphQL document "
                             f"(default {github_stars.MAX_BATCH})")
    parser.add_argument("--date", default=None,
                        help="snapshot date to write (default today, UTC)")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and report, write nothing")
    args = parser.parse_args(argv)

    snap_date = (date.fromisoformat(args.date) if args.date
                 else datetime.now(UTC).date())
    conn = await db.connect()
    try:
        await db.ensure_schema(conn)
        result = await refresh(conn, snap_date, track_days=args.track_days,
                               limit=args.limit, batch_size=args.batch,
                               dry_run=args.dry_run)
        print(result.line() + (" (DRY RUN, nothing written)"
                               if args.dry_run else f" · {snap_date}"))
        coverage = await db.snapshot_coverage(conn)
        print(f"coverage: {coverage['items']} items, "
              f"{coverage['first']}..{coverage['last']}, longest series "
              f"{coverage['longest_span']}d, "
              f"{coverage['spanning_items']} spanning 30d")
    finally:
        await conn.close()
    return 0


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
        print(f"resnapshot failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
