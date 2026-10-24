"""README excerpts for repositories whose description says too little to tag.

About one repository in five arrives with an empty description or one under
40 characters (8,680 items seen in the 30 days to 2026-10-16: 1,064 empty,
861 short), and the tagger then has nothing but the repository name to go
on. For those rows only, the opening of the README stands in: badges, images,
HTML and code blocks are stripped, and the first `EXCERPT_CHARS` characters
of what is left are handed to the tag prompt.

Rows with a usable description are left alone: their tags are already
grounded, and a longer prompt per row is a larger batch that gets closer to
the output ceiling the reasoning shares with the JSON.

One REST request per repository (`GET /repos/{owner}/{repo}/readme`, raw
media type), at most a few dozen a day against a 5000/hour authenticated
budget. Never raises: a README that cannot be read costs that row its
excerpt, not the batch its tags.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys

import httpx

from intel.sources.base import Item

SHORT_DESCRIPTION = 40     # characters; below this the description is a name
EXCERPT_CHARS = 1500
_CONCURRENCY = 4
_TIMEOUT_S = 15.0
_URL = "https://api.github.com/repos/{full_name}/readme"

_FENCE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HTML_TAG = re.compile(r"<[^>]+>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_REF_DEF = re.compile(r"^\s*\[[^\]]+\]:\s*\S+.*$", re.M)
_INLINE_CODE = re.compile(r"`[^`]*`")
_MD_MARKS = re.compile(r"^[#>\-*+|=\s]+|[*_|]+", re.M)


def needs_readme(item: Item) -> bool:
    """A github row whose description is too thin to tag from."""
    return (item.source == "github"
            and len((item.description or "").strip()) < SHORT_DESCRIPTION)


def excerpt(markdown: str, limit: int = EXCERPT_CHARS) -> str:
    """The prose at the top of a README, one line, at most `limit` chars.

    Code blocks go first, whole: an install command says nothing about what
    the project is. Images go before links, because a badge is an image
    inside a link and stripping the link first would leave its alt text."""
    text = _FENCE.sub(" ", markdown or "")
    text = _HTML_COMMENT.sub(" ", text)
    text = _IMAGE.sub(" ", text)
    text = _HTML_TAG.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _REF_DEF.sub(" ", text)
    text = _INLINE_CODE.sub(" ", text)
    text = _MD_MARKS.sub(" ", text)
    return " ".join(text.split())[:limit]


async def _fetch_one(client: httpx.AsyncClient, full_name: str,
                     headers: dict[str, str]) -> str:
    try:
        resp = await client.get(_URL.format(full_name=full_name),
                                headers=headers, follow_redirects=True)
    except httpx.HTTPError:
        return ""
    if resp.status_code != 200:
        return ""
    return excerpt(resp.text)


async def fetch_excerpts(items: list[Item]) -> dict[str, str]:
    """{item_id: excerpt} for the rows `needs_readme` picks; rows with no
    readable README are absent. Never raises."""
    wanted = [i for i in items if needs_readme(i)
              and i.id.startswith("github:") and "/" in i.id]
    if not wanted:
        return {}
    headers = {"Accept": "application/vnd.github.raw"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    gate = asyncio.Semaphore(_CONCURRENCY)

    async def one(client: httpx.AsyncClient, item: Item) -> tuple[str, str]:
        async with gate:
            return item.id, await _fetch_one(
                client, item.id.removeprefix("github:"), headers)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            pairs = await asyncio.gather(*(one(client, i) for i in wanted))
    except Exception as exc:     # the tags still go out without excerpts
        print(f"readme: failed ({exc})", file=sys.stderr)
        return {}
    found = {item_id: text for item_id, text in pairs if text}
    print(f"readme: {len(found)}/{len(wanted)} short-description rows "
          f"got an excerpt", file=sys.stderr)
    return found
