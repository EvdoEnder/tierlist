"""GitHub Search-API adapter — newly created repositories ranked by stars.

One endpoint serves the whole M1: GET /search/repositories with
`q=created:>YYYY-MM-DD fork:false archived:false has:readme`,
`sort=stars&order=desc`. For a repo this young, its accumulated star total
IS the velocity ranking, so no snapshot history is needed to
surface the 新仓库热榜 — that board falls straight out of this fetch.

Auth is optional: without GITHUB_TOKEN the search rate limit is 10 req/min
from a shared IP pool, with it 30 req/min. The token is read by NAME from
the environment and sent only as a Bearer header.
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import httpx

from intel.sources.base import Item

_SEARCH_URL = "https://api.github.com/search/repositories"
_PER_PAGE = 100
_PAGE_PAUSE_S = 2.5          # between pages; keeps even the anonymous tier safe
_SEARCH_TIMEOUT_S = 30.0
_RETRIES = 3                 # per request: hit/network flakes happen in production
_MAX_CAP = 15.0              # cap any Retry-After (waiting longer is a lost cron)


def _query(since_date: str) -> str:
    return (
        f"created:>{since_date} fork:false archived:false has:readme"
    )


def _retry_delay(resp: httpx.Response) -> float:
    """Seconds to wait before retrying `resp`; capped so a hostile calendar
    (max Retry-After) cannot stall the daily cron. 429/403/5xx → backoff."""
    if resp is not None:
        raw = resp.headers.get("Retry-After")
        if raw:
            try:
                return min(float(raw), _MAX_CAP)
            except ValueError:
                # HTTP-date form, e.g. 'Wed, 21 Oct 2026 07:28:00 GMT'
                import email.utils

                parsed = email.utils.parsedate_to_datetime(raw)
                if parsed is not None:
                    delta = (parsed
                             - datetime.now(parsed.tzinfo or UTC)).total_seconds()
                    return min(max(delta, 0.0), _MAX_CAP)
    # 429 without header, or 5xx: squaring backoff 2s/4s/8s — settled and
    # short enough that the daily window still has slack after the attempt.
    return 2.0


async def _retryable_get(client: httpx.AsyncClient, url: str, *,
                         params: dict, headers: dict) -> httpx.Response:
    """GET with retries for the transient tiers (429, 403, 5xx). The M1
    criterion stands: a *failed* request is fatal, but a *flaky* one is
    retried — one 403 from the shared GitHub IP pool must not kill the day's
    brief. After `_RETRIES`, the last error is raised."""
    last: httpx.HTTPError | None = None
    for attempt in range(_RETRIES):
        try:
            resp = await client.get(url, params=params, headers=headers)
        except httpx.RequestError as exc:
            last = exc
            if attempt == _RETRIES - 1:
                raise
            await asyncio.sleep(2.0 * (2 ** attempt))
            continue
        if resp.status_code in (429, 403) or resp.status_code >= 500:
            last = httpx.HTTPStatusError(
                f"{resp.status_code} on {url}", request=resp.request,
                response=resp)
            if attempt == _RETRIES - 1:
                resp.raise_for_status()
            await asyncio.sleep(_retry_delay(resp))
            continue
        resp.raise_for_status()  # other 4xx: genuine client bug, don't retry
        return resp
    raise last  # pragma: no cover — an exhausted loop cannot exit this way


def map_repo(repo: dict) -> Item | None:
    """One search-result row → Item, or None when the row is unusable."""
    full_name = (repo.get("full_name") or "").strip()
    html_url = (repo.get("html_url") or "").strip()
    if not full_name or not html_url:
        return None
    created = repo.get("created_at")
    try:
        created_at = datetime.fromisoformat(created) if created else None
    except ValueError:
        created_at = None
    return Item(
        id=f"github:{full_name}",
        source="github",
        url=html_url,
        name=full_name,
        description=(repo.get("description") or "").strip(),
        lang=repo.get("language") or None,
        stars=int(repo.get("stargazers_count") or 0),
        created_at=created_at,
        is_fork=bool(repo.get("fork")),
        raw=repo,
    )


async def fetch_new_repos(client: httpx.AsyncClient, since_date: str,
                          max_pages: int) -> list[Item]:
    """Newest-by-stars repositories created after `since_date` (ISO date).

    Pages are walked until `max_pages`, a short page, or an empty page.
    Malformed rows are skipped, never fatal; a failed request is.
    """
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    items: list[Item] = []
    for page in range(1, max_pages + 1):
        resp = await _retryable_get(
            client, _SEARCH_URL,
            params={
                "q": _query(since_date),
                "sort": "stars",
                "order": "desc",
                "per_page": _PER_PAGE,
                "page": page,
            },
            headers=headers,
        )
        rows = resp.json().get("items", [])
        for row in rows:
            item = map_repo(row)
            if item is not None:
                items.append(item)
        if len(rows) < _PER_PAGE:
            break
        if page < max_pages:
            await asyncio.sleep(_PAGE_PAUSE_S)
    return items
