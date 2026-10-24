"""News adapters: HN mapping + RSS/Atom parsing (frozen samples), clean_text."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from intel.sources import hn, rss  # noqa: E402

# frozen real-world-shaped samples, not live calls
HN_PAYLOAD = {
    "hits": [{
        "objectID": "41234567",
        "title": "Show HN: A tiny vector DB in pure Rust",
        "url": "https://github.com/x/y",
        "points": 214,
        "created_at_i": 1756018800,
        "created_at": "2026-08-24T08:45:00.000Z",
    }, {
        "objectID": "41234568",            # no story URL → HN discussion link
        "title": "Show HN: I built a search engine for my notes",
        "points": 32,
        "created_at_i": 1756010000,
    }],
}

RSS_SAMPLE = """<?xml version="1.0"?>
<rss version="2.0">
<channel><title>Example</title>
<item>
  <title>One plus two</title>
  <link>https://ex.com/1</link>
  <guid>ex-1</guid>
  <pubDate>Mon, 24 Aug 2026 09:12:00 +0000</pubDate>
  <description>&lt;p&gt;A &lt;b&gt;summary&lt;/b&gt; with tags.&lt;/p&gt;</description>
</item>
<item>
  <title>No link skipped</title>
  <guid>ex-2</guid>
</item>
<item>
  <title>Bad date tolerated</title>
  <link>https://ex.com/3</link>
  <pubDate>not a date</pubDate>
</item>
</channel></rss>
"""

ATOM_SAMPLE = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom feed</title>
  <entry>
    <title>An entry</title>
    <link href="https://ex.com/a"/>
    <id>urn:ex:a</id>
    <updated>2026-08-24T08:45:00Z</updated>
    <summary>Atom summary text</summary>
  </entry>
</feed>
"""


class _OkResp:
    status_code = 200

    def json(self):
        return HN_PAYLOAD

    def raise_for_status(self):
        return None


class _StubClient(httpx.AsyncClient):
    """AsyncClient double: any GET returns the frozen HN payload. fetch_hn
    is a coroutine; AsyncClient.post never called here."""

    async def get(self, url, **kwargs):  # noqa: F841  (url ignored by design)
        return _OkResp()


def test_hn_mapping_keeps_id_url_points():
    import asyncio

    items = asyncio.run(hn.fetch_hn(_StubClient(), since_hours=24,
                                    hits_per_page=100))
    by_id = {i.id: i for i in items}
    assert "hn:41234567" in by_id
    first = by_id["hn:41234567"]
    assert first.source == "hn" and first.name.startswith("Show HN")
    assert first.url == "https://github.com/x/y"
    assert first.stars == 214               # points mapped onto stars
    # no story URL → HN discussion link
    assert by_id["hn:41234568"].url == \
        "https://news.ycombinator.com/item?id=41234568"


def test_rss_parse_items_and_summary_cleaning():
    items = rss.parse_xml(RSS_SAMPLE, "exfeed")
    # ex-2 has no link → skipped; ex-3 has no guid → link is the id fallback
    assert [i.id for i in items] == \
        ["rss:exfeed:ex-1", "rss:exfeed:https://ex.com/3"]
    one = items[0]
    assert one.name == "One plus two"
    assert one.description == "A summary with tags."   # tags stripped
    assert one.created_at is not None                  # RFC 822 parsed
    bad_date = items[1]
    assert bad_date.created_at is None                 # tolerated, not fatal

def test_atom_parse():
    items = rss.parse_xml(ATOM_SAMPLE, "atomfeed")
    assert len(items) == 1
    assert items[0].id == "rss:atomfeed:urn:ex:a"
    assert items[0].url == "https://ex.com/a"
    assert items[0].description == "Atom summary text"


def test_clean_text_caps_and_strips():
    raw = "<b>" + "x" * 400 + "</b>"
    out = rss.clean_text(raw)
    assert len(out) == 280 and "<" not in out
