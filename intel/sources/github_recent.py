"""GitHub recent-cohort adapter — every young repository past a star floor.

The search adapter asks for repositories created inside the 72-hour lookback
window, and GitHub's `created_at` is the day the repository was first made,
not the day it became public. A project developed in private and published
weeks later is therefore already "old" on the day anyone can see it.
laogou717/dogsc was created 2026-08-23, published about 2026-10-05, gained
roughly 70 stars a day after that, and never entered the store: too old for
the search, too slow for GitHub Trending, never posted to Show HN.

GitHub offers no "recently made public" filter, so this adapter reads the
whole cohort instead: every repository created within `days` that holds at
least `min_stars`. A repository that was not in yesterday's cohort and is in
today's is the new arrival. On 2026-10-15 the 60-day, 100-star cohort held
3,824 repositories, read in 42 search requests.

The search API returns at most 1000 results per query, so the cohort is read
in star bands, and a band reporting more than 1000 results is split in two
until each half fits.

Rows are marked `board_only` and `recent`: recorded, snapshotted and tagged,
never scored, and kept off every board and count about new repositories.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import replace

import httpx

from intel.sources.base import Item
from intel.sources.github import (
    _PAGE_PAUSE_S,
    _PER_PAGE,
    _SEARCH_URL,
    _retryable_get,
    map_repo,
)

_RESULT_CAP = 1000           # GitHub search serves no row past the 1000th
# Initial band edges above the floor, chosen so that most bands fit under the
# cap on the first request; a band that does not fit is bisected.
_BAND_EDGES = (120, 150, 200, 300, 500, 1000)


def initial_bands(min_stars: int) -> list[tuple[int, int | None]]:
    """[(low, high)] star bands covering `min_stars` upward; `None` is open."""
    edges = [e for e in _BAND_EDGES if e > min_stars]
    lows = [min_stars, *edges]
    highs: list[int | None] = [e - 1 for e in edges] + [None]
    return list(zip(lows, highs, strict=True))


def split_band(low: int, high: int | None) -> list[tuple[int, int | None]]:
    """Halve a band that holds more than the cap; an open band is cut at
    double its floor, leaving the top still open."""
    if high is None:
        return [(low, 2 * low - 1), (2 * low, None)]
    mid = (low + high) // 2
    return [(low, mid), (mid + 1, high)]


def _query(since_date: str, low: int, high: int | None) -> str:
    stars = f"{low}..{high}" if high is not None else f">={low}"
    return f"created:>{since_date} stars:{stars} fork:false archived:false"


def mark_recent(item: Item) -> Item:
    return replace(item, raw={**item.raw, "board_only": True, "recent": True})


async def fetch_recent(client: httpx.AsyncClient, since_date: str,
                       min_stars: int) -> list[Item]:
    """Every repository created after `since_date` with at least `min_stars`,
    one Item per repository. Malformed rows are skipped; a failed request is
    fatal, as in the search adapter."""
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    seen: dict[str, Item] = {}
    bands = initial_bands(min_stars)
    while bands:
        low, high = bands.pop(0)
        page = 1
        while True:
            resp = await _retryable_get(
                client, _SEARCH_URL,
                params={"q": _query(since_date, low, high), "sort": "stars",
                        "order": "desc", "per_page": _PER_PAGE, "page": page},
                headers=headers)
            data = resp.json()
            if (page == 1 and int(data.get("total_count") or 0) > _RESULT_CAP
                    and (high is None or high > low)):
                bands[:0] = split_band(low, high)
                break
            rows = data.get("items", [])
            for row in rows:
                item = map_repo(row)
                if item is not None:
                    seen.setdefault(item.id, mark_recent(item))
            if len(rows) < _PER_PAGE or page * _PER_PAGE >= _RESULT_CAP:
                break
            page += 1
            await asyncio.sleep(_PAGE_PAUSE_S)
        await asyncio.sleep(_PAGE_PAUSE_S)
    return list(seen.values())
