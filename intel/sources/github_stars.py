"""Current star counts for repositories we already know about.

The search adapter (`sources.github`) only ever sees repositories created
inside the lookback window, so it can carry a project's star count for about
three days and never again. Following an item for a month — which is what
the gain boards and the score calibration both need — requires asking GitHub
about repositories by name, long after they stopped being new.

This is the GraphQL endpoint rather than REST because the cohort is
thousands of repositories: `GET /repos/{owner}/{repo}` costs one request per
repository, while one GraphQL document carries a hundred aliased
`repository` fields for one point of the 5000/hour budget. A daily refresh
of the whole cohort is therefore ~20 requests.

GraphQL requires authentication — there is no anonymous tier — so without
GITHUB_TOKEN this module raises and the caller reports the skip. The token
is read by NAME from the environment and sent only as a Bearer header.
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx

_GRAPHQL_URL = "https://api.github.com/graphql"
_TIMEOUT_S = 30.0
_RETRIES = 3
_BATCH_PAUSE_S = 1.0     # between documents; the point budget is not the limit
MAX_BATCH = 100          # aliases per document — GitHub's node cap for this shape


class MissingToken(RuntimeError):
    """GITHUB_TOKEN is absent, and GraphQL has no anonymous tier."""


def _headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise MissingToken(
            "GITHUB_TOKEN is not set — the GitHub GraphQL API rejects "
            "unauthenticated requests, so star refresh cannot run")
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"}


def build_document(full_names: list[str]) -> tuple[str, dict[str, str]]:
    """One GraphQL document asking for every repository's stargazerCount,
    plus the alias → full_name map needed to read the answer back.

    Aliases are positional (`r0`, `r1`, …) because a GraphQL alias must match
    `[_A-Za-z][_0-9A-Za-z]*` and a repository name may contain characters
    that do not. Owner and name are JSON-encoded rather than interpolated, so
    a name carrying a quote cannot reshape the query.
    """
    fields = []
    aliases: dict[str, str] = {}
    for n, full_name in enumerate(full_names):
        owner, _, name = full_name.partition("/")
        if not owner or not name:
            continue
        alias = f"r{n}"
        aliases[alias] = full_name
        fields.append(
            f"{alias}: repository(owner: {json.dumps(owner)}, "
            f"name: {json.dumps(name)}) {{ stargazerCount isArchived }}")
    return "query {\n  " + "\n  ".join(fields) + "\n}", aliases


async def _post(client: httpx.AsyncClient, document: str) -> dict:
    """POST with the same retry posture as the search adapter: a flaky 403 or
    5xx from the shared edge must not lose the day's refresh, a genuine 4xx
    is a bug and fails fast."""
    last: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            resp = await client.post(_GRAPHQL_URL, json={"query": document},
                                     headers=_headers())
        except httpx.RequestError as exc:
            last = exc
            if attempt == _RETRIES - 1:
                raise
            await asyncio.sleep(2.0 * (2 ** attempt))
            continue
        if resp.status_code in (429, 403) or resp.status_code >= 500:
            last = httpx.HTTPStatusError(f"{resp.status_code} on graphql",
                                         request=resp.request, response=resp)
            if attempt == _RETRIES - 1:
                resp.raise_for_status()
            await asyncio.sleep(2.0 * (2 ** attempt))
            continue
        resp.raise_for_status()
        return resp.json()
    raise last  # pragma: no cover — an exhausted loop cannot exit this way


def read_counts(payload: dict, aliases: dict[str, str]) -> dict[str, int]:
    """Alias-keyed GraphQL payload → {full_name: stars}.

    A repository that was deleted, renamed or made private answers as a null
    node beside a NOT_FOUND entry in `errors`, and the rest of the document
    still resolves. Those names are simply absent from the result: the caller
    writes no snapshot for them, which is the honest record — we do not know
    today's star count, and a repeated stale value would look like a project
    that stopped growing.
    """
    data = payload.get("data") or {}
    counts: dict[str, int] = {}
    for alias, node in data.items():
        full_name = aliases.get(alias)
        if not full_name or not isinstance(node, dict):
            continue
        stars = node.get("stargazerCount")
        if isinstance(stars, int):
            counts[full_name] = stars
    return counts


async def fetch_star_counts(client: httpx.AsyncClient,
                            full_names: list[str],
                            batch_size: int = MAX_BATCH) -> dict[str, int]:
    """Current stars for every repository we can still see, batched.

    Names that no longer resolve are omitted rather than reported as zero;
    the caller compares lengths when it wants the miss count.
    """
    counts: dict[str, int] = {}
    batches = [full_names[i:i + batch_size]
               for i in range(0, len(full_names), batch_size)]
    for n, batch in enumerate(batches):
        document, aliases = build_document(batch)
        if not aliases:
            continue
        counts.update(read_counts(await _post(client, document), aliases))
        if n < len(batches) - 1:
            await asyncio.sleep(_BATCH_PAUSE_S)
    return counts
