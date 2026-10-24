"""GitHub Trending adapter — established repositories that are surging now.

The search adapter only sees repositories created inside the lookback
window, so a project that is months old and suddenly takes off never enters
the store, and the star gain boards cannot rank what the store does not
hold. morluto/rea (created 2026-04-14) gained 19,375 stars on 2026-10-09 and
appeared on no board for exactly that reason.

github.com/trending is the cheapest place that names those projects. It has
no API, so this adapter parses the page; a markup change shows up as a page
that yields zero rows, which the run's per-source count makes visible.

Rows are marked `board_only`: they are tracked for the star series and the
gain boards, shown on their own GitHub Trending boards (day, week, month,
in GitHub's order) and tagged, and kept out of the paid scoring batch, the
新仓库热榜 and the new-item count, all of which are about NEW repositories.
"""
from __future__ import annotations

import html
import re

import httpx

from intel.sources.base import Item

_TRENDING_URL = "https://github.com/trending"
_TIMEOUT_S = 30.0
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; tierlist)",
            "Accept": "text/html"}

_ARTICLE = re.compile(r'<article class="Box-row">(.*?)</article>', re.S)
_NAME = re.compile(r'<h2[^>]*>\s*<a[^>]*\bhref="/([^"/\s]+/[^"/\s]+)"', re.S)
_DESC = re.compile(r'<p class="col-9[^"]*">(.*?)</p>', re.S)
_LANG = re.compile(r'itemprop="programmingLanguage">([^<]+)<')
_STARS = re.compile(r'/stargazers"[^>]*>.*?([\d,]+)\s*</a>', re.S)
_PERIOD_STARS = re.compile(r'([\d,]+)\s+stars?\s+(?:today|this week|this month)')
_TAG = re.compile(r"<[^>]+>")


def _int(text: str | None) -> int:
    return int(text.replace(",", "")) if text else 0


def parse_trending(page: str, period: str) -> list[Item]:
    """One trending page → Items, in GitHub's own order. Rows without a
    repository link are skipped."""
    items: list[Item] = []
    for block in _ARTICLE.findall(page):
        name_m = _NAME.search(block)
        if name_m is None:
            continue
        full_name = name_m.group(1)
        desc_m = _DESC.search(block)
        description = (html.unescape(_TAG.sub("", desc_m.group(1))).strip()
                       if desc_m else "")
        lang_m = _LANG.search(block)
        stars_m = _STARS.search(block)
        period_m = _PERIOD_STARS.search(block)
        items.append(Item(
            id=f"github:{full_name}",
            source="github",
            url=f"https://github.com/{full_name}",
            name=full_name,
            description=description,
            lang=lang_m.group(1).strip() if lang_m else None,
            stars=_int(stars_m.group(1) if stars_m else None),
            created_at=None,
            raw={"board_only": True,
                 "period_rank": {period: len(items) + 1},
                 "period_stars": {
                     period: _int(period_m.group(1) if period_m else None)}},
        ))
    return items


async def fetch_trending(client: httpx.AsyncClient,
                         periods: list[str]) -> list[Item]:
    """Every repository on the trending page for each period, one Item per
    repository. A repository on several pages keeps the first row and
    collects each page's gain under `raw["period_stars"]`."""
    seen: dict[str, Item] = {}
    for period in periods:
        resp = await client.get(_TRENDING_URL, params={"since": period},
                                headers=_HEADERS, timeout=_TIMEOUT_S,
                                follow_redirects=True)
        resp.raise_for_status()
        for item in parse_trending(resp.text, period):
            first = seen.setdefault(item.id, item)
            if first is not item:
                first.raw["period_stars"].update(item.raw["period_stars"])
                first.raw["period_rank"].update(item.raw["period_rank"])
    return list(seen.values())
