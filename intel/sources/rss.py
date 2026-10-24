"""Generic RSS adapter — one parser, many feeds.

The news page needs a handful of tech feeds, and every real-world feed is
either RSS 2.0 (<item> + <pubDate> + <description>) or Atom (<entry> +
<updated> + <summary>). `xml.etree` handles both locally with zero
dependencies; the parser is defensive (a feed element that fails to parse
is dropped, the run continues) because a malformed third-party blob must
never kill the day's refresh.

Mapping → Item:
  id = f"rss:{feed_key}:{guid}"  (guid falls back to the link)
  stars = 0 (feeds have no score)
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

import httpx

from intel.sources.base import Item

_TIMEOUT_S = 20.0
_RETRIES = 2

_HTML_RE = re.compile(r"<[^>]+>")
_TAG_RE = re.compile(r"\s+")
_EL__NS = "{http://www.w3.org/2005/Atom}"


def clean_text(raw: str | None, limit: int = 280) -> str:
    """Strip tags/whitespace, cap at `limit` for a one-line summary."""
    if not raw:
        return ""
    text = _HTML_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text).strip()
    return text[:limit]


def _field(item, names: tuple[str, ...]) -> str:
    """First non-empty child text among `names` (covers RSS + Atom shapes)."""
    for name in names:
        el = item.find(name)
        if el is not None and el.text:
            return el.text.strip()
    return ""


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None:
        return parsed
    # RFC 822 like 'Tue, 25 Aug 2026 08:00:00 +0000'
    import email.utils

    try:
        return email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def parse_xml(xml_text: str, feed_key: str) -> list[Item]:
    """One feed document → Items. Immune to: missing guid, bad links, bad
    dates, description-only fields. Malformed XML → [] (caller decides)."""
    from xml.etree import ElementTree

    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    entries = []
    if root.tag.endswith("rss"):
        for it in root.iter("item"):
            entries.append(_rss_item(it, feed_key))
    elif root.tag.endswith("feed"):
        for it in root.iter(f"{_EL__NS}entry"):
            entries.append(_atom_item(it, feed_key))
    return [e for e in entries if e is not None]


def _rss_item(el, feed_key: str) -> Item | None:
    title = _field(el, ("title",))[:200]
    link = _field(el, ("link",))
    guid = _field(el, ("guid",)) or link
    if not title or not link:
        return None
    published = _parse_dt(_field(el, ("pubDate", "dc:date")))
    summary = clean_text(_field(el, ("description", "summary")))
    return Item(
        id=f"rss:{feed_key}:{guid[:160]}",
        source="rss",
        feed=feed_key,
        url=link,
        name=title,
        description=summary,
        lang=None,
        stars=0,
        created_at=published,
        raw={},
    )


def _atom_item(el, feed_key: str) -> Item | None:
    title = _field(el, (f"{_EL__NS}title",))[:200]
    link = ""
    for child in el.iter(f"{_EL__NS}link"):
        href = child.get("href") or ""
        if href:
            link = href
            break
    guid = _field(el, (f"{_EL__NS}id",)) or link
    if not title or not link:
        return None
    published = _parse_dt(_field(el, (f"{_EL__NS}updated",
                                      f"{_EL__NS}published")))
    summary = clean_text(_field(el, (f"{_EL__NS}summary",
                                     f"{_EL__NS}content")))
    return Item(
        id=f"rss:{feed_key}:{guid[:160]}",
        source="rss",
        feed=feed_key,
        url=link,
        name=title,
        description=summary,
        lang=None,
        stars=0,
        created_at=published,
        raw={},
    )


async def fetch_feed(client: httpx.AsyncClient, url: str,
                     feed_key: str, *, retries: int = _RETRIES) -> list[Item]:
    """GET + parse one feed URL. Transient errors retry; parse failure []."""
    for attempt in range(retries):
        try:
            resp = await client.get(url)
        except httpx.RequestError:
            if attempt == retries - 1:
                return []
            await asyncio.sleep(2.0 * (2 ** attempt))
            continue
        if resp.status_code in (429, 403) or resp.status_code >= 500:
            if attempt == retries - 1:
                return []
            await asyncio.sleep(2.0 * (2 ** attempt))
            continue
        if resp.status_code != 200:
            return []
        return parse_xml(resp.text, feed_key)
    return []
