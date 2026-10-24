"""awesome-list adapter — what a curated list gained since we last looked.

A GitHub search finds what is growing; a curated list finds what a human
decided was worth listing. The signal is the DIFF, not the list: an
awesome-list README is a few hundred entries that have been there for years,
and the interesting event is the line that was added last night.

Which makes the first sight of a list a trap. With nothing to diff against,
every entry is "new" — several thousand of them across the watchlists —
and they would take every slot in the day's paid batch. So the first run of
a list records it and emits nothing; from the second run on, only additions
are signals. `intel.items` is that record (design: no new table), keyed
`awesome:<owner>/<repo>:<url>`, so a list that has ever been seen has rows
and a list that has not, has none.

The store is therefore not optional here: without a connection there is
nothing to diff against, and the adapter says so and returns nothing rather
than treating an unknown past as an empty one.
"""
from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import replace

import httpx

from intel.sources.base import Item

# raw.githubusercontent is case-sensitive about the path, and awesome lists
# are not consistent about the case of their own README: agarrharr's is
# `readme.md` and 404s on `README.md`, which is silently indistinguishable
# from an empty list. Try the spellings that actually occur.
_RAW_URL = "https://raw.githubusercontent.com/{list_name}/HEAD/{filename}"
_README_NAMES = ("README.md", "readme.md", "Readme.md", "README.markdown")
_TIMEOUT_S = 30.0
_RETRIES = 2
_PAUSE_S = 0.5           # between lists; raw.githubusercontent is generous

# `- [name](url) - description`, with the separator in any of the dashes a
# list author might reach for, and the description optional. Ordered bullets
# and nested indentation both count: an entry is an entry wherever it sits.
_ENTRY_RE = re.compile(
    r"^\s*[-*+]\s+\[([^\]]+)\]\((https?://[^)\s]+)\)"
    r"\s*(?:[-—–:]+\s*(.*))?$",
    re.M,
)


def parse_entries(markdown: str, list_name: str) -> list[Item]:
    """README text → one Item per listed link, in file order.

    Deliberately narrow. A line whose link text is an image (`[![badge]…`)
    is a badge, not an entry; an anchor link is the table of contents; and
    the regex requires a bullet, so prose that happens to contain a link is
    not mistaken for a listing.
    """
    items: list[Item] = []
    seen: set[str] = set()
    for name, url, description in _ENTRY_RE.findall(markdown or ""):
        name = name.strip()
        url = url.strip().rstrip("/")
        if not name or name.startswith("!"):     # a badge row
            continue
        if url in seen:
            continue
        seen.add(url)
        items.append(Item(
            id=f"awesome:{list_name}:{url}",
            source="awesome",
            url=url,
            name=name,
            description=_clean(description),
            lang=None,
            stars=0,
            created_at=None,
            feed=list_name,          # which list endorsed it
            raw={"list": list_name},
        ))
    return items


_MD_NOISE = re.compile(r"[`*_]|\[|\]\([^)]*\)")


def _clean(text: str) -> str:
    return " ".join(_MD_NOISE.sub("", text or "").split())


async def _fetch_one(client: httpx.AsyncClient, url: str,
                     retries: int) -> str | None:
    """The body, "" for a 404 (this spelling is not the file) or None for a
    failure worth retrying another way."""
    for attempt in range(retries):
        try:
            resp = await client.get(url, follow_redirects=True)
        except httpx.RequestError:
            if attempt == retries - 1:
                return None
            await asyncio.sleep(2.0 * (2 ** attempt))
            continue
        if resp.status_code >= 500 or resp.status_code == 429:
            if attempt == retries - 1:
                return None
            await asyncio.sleep(2.0 * (2 ** attempt))
            continue
        if resp.status_code == 404:
            return ""
        if resp.status_code != 200:
            return None
        return resp.text
    return None    # pragma: no cover — the loop returns on its last attempt


async def fetch_readme(client: httpx.AsyncClient, list_name: str,
                       retries: int = _RETRIES) -> str:
    """One list's README at its default branch, trying each spelling of the
    filename in turn.

    An empty string when the list cannot be read at all — a renamed or
    deleted watchlist must not take the other lists down with it. The
    warning matters more than it looks: a 404 and a list that has genuinely
    added nothing produce the same zero, and without a line in the log the
    watchlist would sit there contributing nothing forever.
    """
    for filename in _README_NAMES:
        body = await _fetch_one(
            client, _RAW_URL.format(list_name=list_name, filename=filename),
            retries)
        if body:
            return body
    print(f"source awesome: {list_name} has no readable README "
          f"(tried {', '.join(_README_NAMES)})", file=sys.stderr)
    return ""


async def enrich_from_github(client: httpx.AsyncClient, items: list[Item], *,
                             limit: int = 40) -> list[Item]:
    """Fill in stars, language and description for entries that point at a
    GitHub repository — which most of them do.

    Unlike an HN post, an awesome entry has no number of its own, so here
    the repository's star count IS the entry's metric and is written to
    `stars`. Needs GITHUB_TOKEN; without it the entries are still scored,
    with the list's one-line description as their whole context.
    """
    import os

    from intel.sources import github, hn

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        return items
    headers = {"Accept": "application/vnd.github+json",
               "Authorization": f"Bearer {token}"}
    out: list[Item] = []
    spent = 0
    for entry in items:
        full_name = hn.repo_full_name(entry.url) if spent < limit else None
        if not full_name:
            out.append(entry)
            continue
        spent += 1
        try:
            resp = await client.get(
                f"https://api.github.com/repos/{full_name}", headers=headers)
            resp.raise_for_status()
            repo = github.map_repo(resp.json())
        except Exception:
            out.append(entry)
            continue
        if repo is None:
            out.append(entry)
            continue
        out.append(replace(
            entry,
            stars=repo.stars,
            lang=repo.lang or entry.lang,
            created_at=repo.created_at or entry.created_at,
            description=entry.description or repo.description or "",
        ))
    return out


async def new_entries(client: httpx.AsyncClient, list_name: str, *,
                      seen_ids: set[str], baseline: bool,
                      max_new: int) -> list[Item]:
    """One list's additions since we last looked.

    `baseline` (the list has no rows at all) returns every entry so the
    caller can record them, and the caller then emits none of them. Past the
    baseline, `max_new` caps what one list may contribute: a README that is
    restructured — every URL rewritten, every entry "new" — is a formatting
    event, not several hundred discoveries, and must not be able to buy the
    whole day's batch.
    """
    entries = parse_entries(await fetch_readme(client, list_name), list_name)
    if baseline:
        return entries
    fresh = [entry for entry in entries if entry.id not in seen_ids]
    if len(fresh) > max_new:
        print(f"source awesome: {list_name} added {len(fresh)} entries at "
              f"once — capped at {max_new} (a restructured README looks "
              f"exactly like this)", file=sys.stderr)
        fresh = fresh[:max_new]
    return fresh
