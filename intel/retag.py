"""Run the stored tags through the current vocabulary again.

Tags are normalized once, when the model returns them. A later change to
`intel/rubrics/tags.yaml` (a new alias, a dropped word) reaches new ratings
only, so the tag pages keep the old spellings beside the new ones until the
stored arrays are normalized again. This command does that, with no model
call:

    python -m intel.retag            # report what would change, write nothing
    python -m intel.retag --apply    # write the changes

It also folds entity spellings the alias table has not met yet. The model
writes a proper noun as it sees fit, so `marvelrivals` turns up beside
`marvel-rivals`; spellings that differ only in punctuation and case are
merged onto the one most items already use, or onto the topic of that name.

An item whose every tag is now dropped keeps an empty array, which is what
the tagger writes for "nothing to say", so it is not queued for tagging again.
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

from dotenv import load_dotenv

from intel import aio
from intel import tags as T
from intel.store import db

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _key(slug: str) -> str:
    return "".join(ch for ch in slug.lower() if ch.isalnum())


def spellings(tag_lists: list[list[str]], vocab: T.Vocabulary
              ) -> dict[str, str]:
    """{variant: spelling to keep} for entity slugs that differ only in
    punctuation and case. A topic of that name wins; otherwise the spelling
    most items use, then the one with a separator, then the first."""
    counts = collections.Counter(t for tags in tag_lists for t in tags)
    groups: dict[str, list[str]] = collections.defaultdict(list)
    for slug in counts:
        groups[_key(slug)].append(slug)
    topics = {_key(t): t for t in vocab.topics}
    out = {}
    for key, group in groups.items():
        keep = topics.get(key) or sorted(
            group, key=lambda s: (-counts[s], s.isalnum(), s))[0]
        out.update({s: keep for s in group if s != keep})
    return out


def plan(rows: list[tuple[str, list]], *, vocab: T.Vocabulary | None = None
         ) -> list[tuple[str, list[str], list[str]]]:
    """(id, old, new) for every item whose tags would change."""
    vocab = vocab or T.vocabulary()
    olds = [(item_id, list(old or [])) for item_id, old in rows]
    first = [T.normalize(old, vocab=vocab) for _id, old in olds]
    fold = spellings(first, vocab)
    out = []
    for (item_id, old), tags in zip(olds, first, strict=True):
        new = (T.normalize([fold.get(t, t) for t in tags], vocab=vocab)
               if any(t in fold for t in tags) else tags)
        if new != old:
            out.append((item_id, old, new))
    return out


def report(changes: list[tuple[str, list[str], list[str]]], total: int) -> str:
    moves: collections.Counter = collections.Counter()
    for _id, old, new in changes:
        added = ",".join(t for t in new if t not in old) or "-"
        for gone in (t for t in old if t not in new):
            moves[(gone, added)] += 1
    emptied = sum(1 for _id, old, new in changes if old and not new)
    lines = [f"retag: {len(changes)} of {total} tagged items would change, "
             f"{emptied} left with no tag"]
    lines += [f"  {n:5d}  {gone} -> {added}"
              for (gone, added), n in moves.most_common()]
    return "\n".join(lines)


async def run(conn, *, apply: bool = False) -> int:
    rows = await db.tagged_items(conn)
    changes = plan(rows)
    print(report(changes, len(rows)))
    if apply:
        await db.save_tags_many(conn, [(i, new) for i, _old, new in changes])
        print(f"retag: wrote {len(changes)} items")
    else:
        print("retag: dry run, nothing written (add --apply to write)")
    return len(changes)


async def _main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="intel.retag",
        description="normalize the stored tags with the current vocabulary")
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (default: report only)")
    args = parser.parse_args(argv)
    conn = await db.connect()
    try:
        await run(conn, apply=args.apply)
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
        print(f"retag failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    load_dotenv(_PROJECT_ROOT / ".env")
    sys.exit(main())
