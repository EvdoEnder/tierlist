"""Tag items in bulk — the initial backfill and the re-tag after a
vocabulary change.

    python -m intel.backfill_tags                       # only untagged items
    python -m intel.backfill_tags --all                 # re-tag everything
    python -m intel.backfill_tags --all --limit 30 --dry-run
    python -m intel.backfill_tags --short               # thin descriptions, from README
    python -m intel.backfill_tags --empty               # ask again where the model named none

ONE LLM call per batch asking only for tags. Progress and token totals print
to stdout.

Two queues, because they have different stopping conditions:

  * **`tags IS NULL`** (default). Items the model skipped are stored as an
    empty array, and that is what keeps them out of the queue on the next
    pass — an empty array is not NULL.
  * **`--all`** walks every item by `(first_seen, id)` keyset instead. The
    NULL queue cannot express "re-tag", since a re-tagged row still matches
    "everything" and the loop would never end. This is also the only way to
    reach the 239 items that were empty-arrayed by an earlier pass: they are
    not in the NULL queue and never will be again.

`--dry-run` prints the before/after for each item and writes nothing, so the
cost and the quality of a vocabulary change can both be judged on a sample
before paying for the whole corpus.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from intel import aio
from intel.score import llm
from intel.sources import github_readme
from intel.sources.base import Item
from intel.store import db

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _as_items(rows) -> list[Item]:
    return [
        Item(id=r[0], source="github", url="", name=r[1] or r[0],
             description=r[2] or "", lang=None, stars=0, created_at=None)
        for r in rows
    ]


async def _null_queue(conn, batch: int, _cursor):
    """Items that have never been tagged, oldest first. The cursor is unused:
    writing tags removes a row from this queue, so re-reading from the top
    always advances."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, name, description, tags FROM intel.items
            WHERE tags IS NULL
            ORDER BY first_seen, id
            LIMIT %s
            """,
            (batch,),
        )
        return list(await cur.fetchall()), None


async def _keyset_queue(conn, batch: int, cursor, where: str = "TRUE"):
    """Rows matching `where`, paged by the (first_seen, id) keyset. Keyset
    rather than OFFSET because the rows are being written while they are
    walked."""
    async with conn.cursor() as cur:
        if cursor is None:
            await cur.execute(
                f"""
                SELECT id, name, description, tags FROM intel.items
                WHERE {where}
                ORDER BY first_seen, id
                LIMIT %s
                """,
                (batch,),
            )
        else:
            await cur.execute(
                f"""
                SELECT id, name, description, tags FROM intel.items
                WHERE {where} AND (first_seen, id) > (%s, %s)
                ORDER BY first_seen, id
                LIMIT %s
                """,
                (cursor[0], cursor[1], batch),
            )
        rows = list(await cur.fetchall())
    if not rows:
        return [], None
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT first_seen, id FROM intel.items WHERE id = %s",
            (rows[-1][0],),
        )
        last = await cur.fetchone()
    return rows, (last[0], last[1])


async def _all_queue(conn, batch: int, cursor):
    """Every item."""
    return await _keyset_queue(conn, batch, cursor)


async def _empty_queue(conn, batch: int, cursor):
    """Items a pass stored as an empty array: the model named no tag."""
    return await _keyset_queue(conn, batch, cursor, "tags = '[]'::jsonb")


async def _short_queue(conn, batch: int, cursor):
    """Tagged github items whose description is too thin to tag from: the
    rows tagged before README excerpts reached the tag prompt."""
    return await _keyset_queue(
        conn, batch, cursor,
        "source = 'github' AND tags IS NOT NULL"
        f" AND length(coalesce(description, '')) < "
        f"{github_readme.SHORT_DESCRIPTION}")


async def backfill(conn, batch: int, *, retag_all: bool = False,
                   short_only: bool = False, empty_only: bool = False,
                   limit: int | None = None,
                   dry_run: bool = False) -> int:
    """Loop the chosen queue until it is empty; returns items processed."""
    total_usage: dict = {}
    done = 0
    cursor = None
    if short_only:
        next_batch = _short_queue
    elif empty_only:
        next_batch = _empty_queue
    elif retag_all:
        next_batch = _all_queue
    else:
        next_batch = _null_queue
    changed = 0
    skipped = 0

    while True:
        size = batch if limit is None else min(batch, limit - done)
        if size <= 0:
            break
        rows, cursor = await next_batch(conn, size, cursor)
        if not rows:
            break

        # The queue read opened a transaction; close it before the README
        # fetch and the LLM call, or the database role's 5-minute
        # idle_in_transaction_session_timeout drops the connection.
        await conn.commit()
        before = {r[0]: list(r[3] or []) for r in rows}
        candidates = _as_items(rows)
        readmes = await github_readme.fetch_excerpts(candidates)
        if short_only:
            # A row with no readable README (most are deleted repositories)
            # would be re-asked on the same input it was tagged from: the
            # answer could only churn. Its tags stay as they are.
            skipped += len(candidates) - len(
                [c for c in candidates if c.id in readmes])
            candidates = [c for c in candidates if c.id in readmes]
            if not candidates:
                done += len(rows)
                continue
        tagged, usage = await llm.tag_candidates(candidates, readmes)
        for key in ("prompt_tokens", "completion_tokens"):
            total_usage[key] = total_usage.get(key, 0) + (usage.get(key) or 0)

        for candidate in candidates:
            answered = candidate.id in tagged
            old = before[candidate.id]

            # A miss means different things to the two queues, and getting
            # this backwards destroys data. On the NULL queue an empty array
            # must be written: it is what takes the item out of the queue,
            # so the same batch would otherwise repeat forever. On --all the
            # item already HAS tags, and the model failing to mention it is
            # not evidence that it has none — leave what is there.
            if not answered and (retag_all or short_only or empty_only):
                skipped += 1
                continue

            new = tagged.get(candidate.id) or []
            if new != old:
                changed += 1
            if dry_run:
                if new != old:
                    print(f"  {candidate.name}\n    - {old}\n    + {new}")
                continue
            await db.set_tags_for_item(conn, candidate.id, new)

        done += len(rows)
        print(f"batch: {done} processed, {changed} changed, "
              f"{skipped} left alone"
              f"{' (DRY RUN, nothing written)' if dry_run else ''}")

    print(f"tokens: prompt={total_usage.get('prompt_tokens', 0)} "
          f"completion={total_usage.get('completion_tokens', 0)}")
    return done


async def _main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="intel.backfill_tags",
                                     description="tag intel.items in bulk")
    parser.add_argument("--batch", type=int, default=50,
                        help="items per LLM call (default 50)")
    parser.add_argument("--all", action="store_true", dest="retag_all",
                        help="re-tag EVERY item, not just the untagged ones "
                             "— the only way to reach items an earlier pass "
                             "stored as an empty array")
    parser.add_argument("--short", action="store_true", dest="short_only",
                        help="re-tag the tagged github items whose "
                             "description is under "
                             f"{github_readme.SHORT_DESCRIPTION} chars, from "
                             "their README; rows with no readable README "
                             "keep their tags")
    parser.add_argument("--empty", action="store_true", dest="empty_only",
                        help="ask again about the items stored with no tag")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after N items (sample a vocabulary change "
                             "before paying for the whole corpus)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print before/after per item, write nothing")
    args = parser.parse_args(argv)
    conn = await db.connect()
    try:
        await db.ensure_schema(conn)
        await backfill(conn, args.batch, retag_all=args.retag_all,
                       short_only=args.short_only,
                       empty_only=args.empty_only, limit=args.limit,
                       dry_run=args.dry_run)
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
        print(f"backfill failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    load_dotenv(_PROJECT_ROOT / ".env")
    sys.exit(main())
