"""Give every rating its English reason.

The scorer writes a reason in Chinese and, since 2026-10-24, its English
version beside it. Two cases are left without English, and this module
closes both:

  * **the daily run**, when the model leaves `reason_en` out of a row.
    `fill_missing` translates those rows in memory before they are saved,
    so the English page of that same run already has them.
  * **ratings made before English existed**. The command walks the store:

    python -m intel.translate_reasons                  # every missing row
    python -m intel.translate_reasons --limit 10 --dry-run
    python -m intel.translate_reasons --workers 8
    python -m intel.translate_reasons --descriptions   # repository descriptions

A repository's own description is the author's, but one written in Chinese,
Japanese, Korean or Russian says nothing to an English reader. With
`--descriptions` the command gives those an English line, which the English
pages show in its place; the Chinese pages keep the original.

Translating needs no judgment, so the call runs with thinking switched off and
costs a fraction of a scoring call. A row the model skips keeps a NULL and
shows its Chinese reason on the English page until a later pass reaches it.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

import httpx
from dotenv import load_dotenv

from intel import aio
from intel.score import llm
from intel.score.llm import ScoredItem
from intel.store import db

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def fill_missing(scores: list[ScoredItem]
                       ) -> tuple[list[ScoredItem], dict]:
    """`scores` with `reason_en` filled wherever the model left it out.

    Best-effort: a failed batch is reported and its rows stay without
    English, because a missing translation must not cost the day its
    issue. The English page falls back to the Chinese reason for them."""
    todo = [i for i, s in enumerate(scores) if s.reason and not s.reason_en]
    if not todo:
        return scores, {}
    out = list(scores)
    usage: dict = {}
    filled = 0
    async with httpx.AsyncClient(timeout=llm._TIMEOUT_S) as client:
        for start in range(0, len(todo), llm.TRANSLATE_BATCH):
            chunk = todo[start:start + llm.TRANSLATE_BATCH]
            try:
                got, used = await llm.translate_to_english(
                    [scores[i].reason for i in chunk], client=client)
            except Exception as exc:  # noqa: BLE001 — reported; the run goes on
                print(f"translate: batch of {len(chunk)} failed: {exc}",
                      file=sys.stderr)
                continue
            usage = llm.merge_usage(usage, used)
            for k, en in got.items():
                out[chunk[k]] = replace(out[chunk[k]], reason_en=en)
            filled += len(got)
    print(f"translate: {filled} of {len(todo)} missing English reasons filled",
          file=sys.stderr)
    return out, usage


async def backfill(conn, *, limit: int | None = None, workers: int = 4,
                   batch: int = llm.TRANSLATE_BATCH,
                   dry_run: bool = False) -> int:
    """Translate the stored ratings that have no English; returns rows
    written. The queue is read once up front, newest first, so a row the
    model skips is not asked again in the same pass."""
    rows = await db.reasons_without_english(conn, limit=limit)
    await conn.commit()
    chunks = [rows[i:i + batch] for i in range(0, len(rows), batch)]
    print(f"translate: {len(rows)} ratings without English, "
          f"{len(chunks)} batches, {workers} at a time")
    sem = asyncio.Semaphore(max(workers, 1))
    write = asyncio.Lock()
    totals = {"written": 0, "skipped": 0, "failed": 0}
    usage: dict = {}

    async def one(chunk, client):
        nonlocal usage
        async with sem:
            try:
                got, used = await llm.translate_to_english(
                    [r["reason"] for r in chunk], client=client)
            except Exception as exc:  # noqa: BLE001 — reported; others go on
                totals["failed"] += len(chunk)
                print(f"translate: batch of {len(chunk)} failed: {exc}",
                      file=sys.stderr)
                return
        async with write:
            usage = llm.merge_usage(usage, used)
            totals["skipped"] += len(chunk) - len(got)
            if dry_run:
                for k, en in sorted(got.items()):
                    print(f"  {chunk[k]['item_id']}\n    {chunk[k]['reason']}"
                          f"\n    {en}")
                return
            await db.set_reasons_en(conn, [
                (en, chunk[k]["item_id"], chunk[k]["snap_date"])
                for k, en in got.items()])
            totals["written"] += len(got)
            print(f"translate: {totals['written']} written", flush=True)

    async with httpx.AsyncClient(timeout=llm._TIMEOUT_S) as client:
        await asyncio.gather(*(one(c, client) for c in chunks))
    print(f"translate: done, {totals['written']} written, "
          f"{totals['skipped']} skipped by the model, {totals['failed']} in "
          f"failed batches{' (DRY RUN, nothing written)' if dry_run else ''}; "
          f"tokens prompt={usage.get('prompt_tokens', 0)} "
          f"completion={usage.get('completion_tokens', 0)}")
    return totals["written"]


async def backfill_descriptions(conn, *, limit: int | None = None,
                                workers: int = 4,
                                batch: int = llm.TRANSLATE_BATCH,
                                dry_run: bool = False) -> int:
    """Translate the foreign-script descriptions that have no English line;
    returns rows written. Same shape as `backfill`, on intel.items."""
    rows = await db.descriptions_without_english(conn, limit=limit)
    await conn.commit()
    chunks = [rows[i:i + batch] for i in range(0, len(rows), batch)]
    print(f"describe: {len(rows)} descriptions without English, "
          f"{len(chunks)} batches, {workers} at a time")
    sem = asyncio.Semaphore(max(workers, 1))
    write = asyncio.Lock()
    totals = {"written": 0, "skipped": 0, "failed": 0}
    usage: dict = {}

    async def one(chunk, client):
        nonlocal usage
        async with sem:
            try:
                got, used = await llm.translate_to_english(
                    [r["description"] for r in chunk], client=client,
                    system=llm.DESCRIBE_SYSTEM)
            except Exception as exc:  # noqa: BLE001 — reported; others go on
                totals["failed"] += len(chunk)
                print(f"describe: batch of {len(chunk)} failed: {exc}",
                      file=sys.stderr)
                return
        async with write:
            usage = llm.merge_usage(usage, used)
            totals["skipped"] += len(chunk) - len(got)
            if dry_run:
                for k, en in sorted(got.items()):
                    print(f"  {chunk[k]['id']}\n    {chunk[k]['description']}"
                          f"\n    {en}")
                return
            await db.set_descriptions_en(
                conn, [(en, chunk[k]["id"]) for k, en in got.items()])
            totals["written"] += len(got)
            print(f"describe: {totals['written']} written", flush=True)

    async with httpx.AsyncClient(timeout=llm._TIMEOUT_S) as client:
        await asyncio.gather(*(one(c, client) for c in chunks))
    print(f"describe: done, {totals['written']} written, "
          f"{totals['skipped']} skipped by the model, {totals['failed']} in "
          f"failed batches{' (DRY RUN, nothing written)' if dry_run else ''}; "
          f"tokens prompt={usage.get('prompt_tokens', 0)} "
          f"completion={usage.get('completion_tokens', 0)}")
    return totals["written"]


async def _main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="intel.translate_reasons",
        description="write the English reason of every rating that has none")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after N ratings (sample the wording first)")
    parser.add_argument("--workers", type=int, default=4,
                        help="requests in flight at once (default 4)")
    parser.add_argument("--batch", type=int, default=llm.TRANSLATE_BATCH,
                        help=f"reasons per request (default "
                             f"{llm.TRANSLATE_BATCH})")
    parser.add_argument("--dry-run", action="store_true",
                        help="print each translation, write nothing")
    parser.add_argument("--descriptions", action="store_true",
                        help="translate repository descriptions written in "
                             "a foreign script instead of reasons")
    args = parser.parse_args(argv)
    conn = await db.connect()
    try:
        await db.ensure_schema(conn)
        run = backfill_descriptions if args.descriptions else backfill
        await run(conn, limit=args.limit, workers=args.workers,
                  batch=args.batch, dry_run=args.dry_run)
    finally:
        await conn.close()
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        return aio.run(_main_async(sys.argv[1:]))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # CLI boundary — report, don't traceback-spam
        print(f"translate failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    load_dotenv(_PROJECT_ROOT / ".env")
    sys.exit(main())
