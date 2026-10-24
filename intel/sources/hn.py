"""Hacker News adapter — the live ``search_by_date`` endpoint.

Two callers, one adapter. The news cron reads it every fifteen minutes and
displays the result as-is: no judgment, no LLM, zero cost per run. The daily
sweep reads the same endpoint over its own window and sends the posts to the
scorer, where Show HN is the earliest public signal a tool exists at all —
usually days ahead of the GitHub star curve that would otherwise find it.

Item mapping: id = hn:<objectID> (globally unique, same scheme rule as
github:), stars = points (a post's score, NOT stars — every display and
prompt that shows it says so), url = the story link when present, else the
HN discussion URL, created_at = the post time from `created_at_i`.

`created_at` is load-bearing twice over. The prefilter ranks HN by points
per hour, which without a timestamp is undefined and drops every post; and
the news table stores it as `published_at`, which is what the news page
sorts on — while it went unmapped, all 811 HN rows carried NULL and sorted
below every RSS item on a page whose whole purpose is freshness.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx

from intel.sources.base import Item

_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
_TIMEOUT_S = 20.0
_RETRIES = 2


async def _get_page(client: httpx.AsyncClient, endpoint: str,
                    retries: int) -> dict:
    import asyncio

    for attempt in range(retries):
        try:
            resp = await client.get(endpoint)
        except httpx.RequestError:
            if attempt == retries - 1:
                raise
            await asyncio.sleep(2.0 * (2 ** attempt))
            continue
        if resp.status_code in (429, 403) or resp.status_code >= 500:
            if attempt == retries - 1:
                resp.raise_for_status()
            await asyncio.sleep(2.0 * (2 ** attempt))
            continue
        resp.raise_for_status()
        return resp.json()
    return {}  # pragma: no cover — the loop above returns or raises


async def fetch_hn(client: httpx.AsyncClient, *, since_hours: int,
                   hits_per_page: int, tags: str = "show_hn",
                   retries: int = _RETRIES, max_pages: int = 1) -> list[Item]:
    """Newest posts matching `tags` since (now − since_hours).

    `max_pages` exists because the window and the page size argue: Show HN
    runs at roughly 90 posts a day, so one page of 100 covers about a day
    whatever `since_hours` claims — asking for 72 hours and reading one page
    silently returned 26 (measured 2026-08-30). The news cron keeps one page
    (it wants the freshest few, not the window), the sweep walks enough
    pages to actually cover the hours it says it covers.
    """
    import time

    now = int(time.time())
    since = now - int(timedelta(hours=since_hours).total_seconds())
    base = (f"{_SEARCH_URL}?tags={tags}&hitsPerPage={hits_per_page}"
            f"&numericFilters=created_at_i%3E{since}")
    rows: list[dict] = []
    for page in range(max(max_pages, 1)):
        payload = await _get_page(
            client, base if page == 0 else f"{base}&page={page}", retries)
        hits = payload.get("hits", [])
        rows.extend(hits)
        if len(hits) < hits_per_page:
            break
    items: list[Item] = []
    for row in rows:
        title = (row.get("title") or row.get("story_title") or "").strip()
        object_id = str(row.get("objectID", "")).strip()
        if not title or not object_id:
            continue
        source_url = (row.get("url") or "").strip() \
            or f"https://news.ycombinator.com/item?id={object_id}"
        points = int(row.get("points") or row.get("score") or 0)
        items.append(Item(
            id=f"hn:{object_id}",
            source="hn",
            url=source_url,
            name=title,
            description=story_text(row),
            lang=None,
            stars=points,
            created_at=posted_at(row),
            raw=row,
        ))
    return items


def posted_at(row: dict) -> datetime | None:
    """When the post went up, from Algolia's `created_at_i` (unix seconds),
    falling back to the ISO `created_at`. None when neither parses — such a
    row has no measurable momentum and the prefilter drops it rather than
    guessing."""
    epoch = row.get("created_at_i")
    if epoch is not None:
        try:
            return datetime.fromtimestamp(int(epoch), tz=UTC)
        except (TypeError, ValueError, OSError):
            pass
    raw = (row.get("created_at") or "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


_CLOSE_TAG_RE = re.compile(r"</[^>]+>")
_TAG_RE = re.compile(r"<[^>]+>")


def story_text(row: dict) -> str:
    """The post's own body when it has one (a Show HN often explains itself
    there), flattened out of HTML. Most posts are a title and a link and
    return an empty string — which is a fact about HN, not a defect, and the
    scoring prompt is told so.

    A closing tag becomes nothing and an opening one becomes a space: the
    other way round, "<b>vectors</b>." reads as "vectors ." and every
    sentence in the corpus grows a floating full stop.
    """
    text = (row.get("story_text") or row.get("comment_text") or "").strip()
    if not text:
        return ""
    text = _CLOSE_TAG_RE.sub("", text)
    return " ".join(_TAG_RE.sub(" ", text).split())


_GITHUB_REPO = re.compile(
    r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?(?:[#?].*)?$")


def repo_full_name(url: str) -> str | None:
    """`https://github.com/owner/repo` → `owner/repo`, else None. Deep links
    (a file, an issue, a release) are deliberately not matched: they are
    about a repository but they are not one, and enriching from them would
    attribute a whole project's stars to a link about one line of it."""
    match = _GITHUB_REPO.match((url or "").strip())
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if owner.lower() in {"orgs", "topics", "collections", "sponsors",
                         "settings", "features", "about"}:
        return None
    return f"{owner}/{repo}"


async def enrich_from_github(client: httpx.AsyncClient, items: list[Item], *,
                             limit: int = 40) -> list[Item]:
    """Fill in what a repository knows about itself for posts that link to
    one: the description and the language, which HN does not carry.

    `stars` is NOT overwritten. It holds the post's points, the number the
    prefilter ranks on and the card prints behind ▲; replacing it with the
    repository's star count would silently change what the row means. The
    repository's own star count goes into the description instead, where it
    reads as evidence rather than as the post's score.

    Enrichment needs GITHUB_TOKEN (unauthenticated REST allows 60 requests
    an hour, and one sweep can want more than that), and is skipped without
    it — the posts still get scored, with less context.
    """
    import os

    from intel.sources import github

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        return items

    headers = {"Accept": "application/vnd.github+json",
               "Authorization": f"Bearer {token}"}
    out: list[Item] = []
    spent = 0
    for candidate in items:
        full_name = repo_full_name(candidate.url) if spent < limit else None
        if not full_name:
            out.append(candidate)
            continue
        spent += 1
        try:
            resp = await client.get(
                f"https://api.github.com/repos/{full_name}", headers=headers)
            resp.raise_for_status()
            repo = github.map_repo(resp.json())
        except Exception:      # a post is worth scoring without its repo row
            out.append(candidate)
            continue
        if repo is None:
            out.append(candidate)
            continue
        blurb = " · ".join(part for part in (
            f"GitHub {repo.name} ★{repo.stars}",
            repo.description, candidate.description) if part)
        out.append(replace(candidate, description=blurb,
                           lang=repo.lang or candidate.lang))
    return out


async def fetch_new_hn(client: httpx.AsyncClient, cfg: dict) -> list[Item]:
    """config.news.hn dict → items; the run_news entry receives this."""
    return await fetch_hn(
        client,
        since_hours=int(cfg["since_hours"]),
        hits_per_page=int(cfg["hits_per_page"]),
        tags=str(cfg.get("tags", "show_hn")),
    )
