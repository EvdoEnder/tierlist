"""Which board a scored signal belongs on, and what it must score to appear.

A repository and a Show HN post are not the same claim. One has stars, code
and a history; the other has upvotes and a discussion that may be about a
hosted product, a blog post or a game. They are judged by the same rubric
because the question ("is this worth watching") is the same, but they are
delivered as two boards because one ranked list's ORDER silently decides how
much of each the reader gets — and the reader did not choose that ratio.

2026-08-30 is the day that stopped being theoretical: seven of the ten picks
were HN posts, not because the repositories were weaker but because a
star-farm had taken 37 of the 38 GitHub candidates (all at exactly 150 stars,
all created inside the same 36 minutes) and the scorer correctly gave them 9.
The brief read as a Hacker News digest with one repository in it.

Two rules follow from being two boards:

- Each board takes its own `top_n`. A board is ranked against its own kind,
  so a quiet day on one side cannot consume the other side's slots.
- Each board carries its own floor. 精选 has none — a day whose best
  repository scores 40 still shows it, and the page says it is below the
  rubric's line (see `pipeline._top`). Show HN carries one, because it is a
  SECOND push message: an extra notification has to earn itself, and a post
  that only just cleared the prefilter is not worth a phone buzzing twice.
  55 is a little under the rubric's 60 watch line, so a near-miss still
  arrives while the 42-point curiosity does not.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

PICKS = "picks"
HN = "hn"

# Render/delivery order: the repositories are the brief, Show HN follows it.
ORDER = (PICKS, HN)

# A source that is not named here falls on the 精选 board. That is the right
# default for the sources this engine has: `awesome` entries ARE repositories,
# endorsed by a curator instead of by a star count.
SECTION_OF_SOURCE = {"github": PICKS, "awesome": PICKS, "hn": HN}

MIN_SCORE = {PICKS: 0, HN: 55}


def section_of(source: str | None) -> str:
    return SECTION_OF_SOURCE.get(source or "", PICKS)


def min_score(section: str) -> int:
    return MIN_SCORE.get(section, 0)


def _dict_source(row: Any) -> str | None:
    return row.get("source")


def _dict_score(row: Any) -> int:
    return row.get("score") or 0


def split(rows: Iterable[Any], *,
          source: Callable[[Any], str | None] = _dict_source,
          score: Callable[[Any], int] = _dict_score,
          limit: int | None = None) -> dict[str, list]:
    """Rows → {section: rows of that section}, every section present.

    Input order is preserved and never re-sorted: callers hand over rows in
    the order their board ranks them (score-desc everywhere today), and a
    second sort here would be a second opinion about ranking held in the
    wrong module. `limit` is per section, which is the whole point — it is
    the cap each board fills on its own.
    """
    out: dict[str, list] = {name: [] for name in ORDER}
    for row in rows:
        name = section_of(source(row))
        bucket = out.setdefault(name, [])
        if score(row) < min_score(name):
            continue
        if limit is not None and len(bucket) >= limit:
            continue
        bucket.append(row)
    return out
