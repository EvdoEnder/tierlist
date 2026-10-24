"""The HN adapter's mapping, against frozen Algolia payloads.

Same shape as test_github_mapping: the rows below are what the API actually
returns, so a field that quietly changes name here fails a test instead of
silently emptying a column.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from intel.sources import hn

pytestmark = pytest.mark.anyio


def hit(object_id="41000001", title="Show HN: a local-first vector store",
        points=180, created_at_i=1787990400, url="https://github.com/me/vex",
        story_text=""):
    """One `search_by_date` hit, field-for-field."""
    return {
        "objectID": object_id,
        "title": title,
        "url": url,
        "points": points,
        "num_comments": 12,
        "author": "someone",
        "created_at_i": created_at_i,
        "created_at": "2026-08-27T12:00:00.000Z",
        "story_text": story_text,
        "_tags": ["story", "show_hn", "author_someone"],
    }


# ------------------------------------------------------------------ posted_at

def test_the_post_time_comes_from_the_unix_field():
    when = hn.posted_at(hit(created_at_i=1787990400))
    assert when == datetime.fromtimestamp(1787990400, tz=UTC)


def test_the_iso_field_is_the_fallback():
    row = hit()
    row.pop("created_at_i")
    assert hn.posted_at(row) == datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def test_an_unparseable_time_is_none_not_now():
    # "now" would give a fresh post's momentum to an ancient one, and the
    # news page would file it at the top of the feed.
    row = hit(created_at_i="not-a-number")
    row["created_at"] = "yesterday-ish"
    assert hn.posted_at(row) is None


# ------------------------------------------------------------------ mapping

class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    def __init__(self, get_map=None, hits=None):
        self.get_map = get_map or {}
        self.hits = hits if hits is not None else []
        self.calls: list[str] = []

    async def get(self, url, **kw):
        self.calls.append(url)
        if url in self.get_map:
            return _Resp(self.get_map[url])
        return _Resp({"hits": self.hits})


async def test_a_hit_becomes_an_item_with_its_points_and_time():
    client = _Client(hits=[hit()])
    items = await hn.fetch_hn(client, since_hours=72, hits_per_page=100)
    assert len(items) == 1
    post = items[0]
    assert post.id == "hn:41000001" and post.source == "hn"
    assert post.stars == 180                      # points, not stars
    assert post.created_at is not None            # the prefilter needs this
    assert post.url == "https://github.com/me/vex"


async def test_a_post_without_a_link_keeps_the_discussion_url():
    client = _Client(hits=[hit(url="")])
    items = await hn.fetch_hn(client, since_hours=72, hits_per_page=100)
    assert items[0].url == "https://news.ycombinator.com/item?id=41000001"


async def test_the_posts_own_text_becomes_the_description():
    client = _Client(hits=[hit(story_text="<p>It stores <b>vectors</b>.</p>")])
    items = await hn.fetch_hn(client, since_hours=72, hits_per_page=100)
    assert items[0].description == "It stores vectors."


# --------------------------------------------------------------- repo links

@pytest.mark.parametrize("url,expected", [
    ("https://github.com/me/vex", "me/vex"),
    ("http://www.github.com/me/vex/", "me/vex"),
    ("https://github.com/me/vex.git", "me/vex"),
    ("https://github.com/me/vex?utm=hn", "me/vex"),
    # A link about a repository is not a repository: crediting a whole
    # project's stars to a link about one issue is a category error.
    ("https://github.com/me/vex/issues/12", None),
    ("https://github.com/topics/rust", None),
    ("https://example.com/vex", None),
    ("", None),
])
def test_only_a_repository_root_is_a_repository(url, expected):
    assert hn.repo_full_name(url) == expected


async def test_enrichment_fills_the_blurb_and_leaves_the_points_alone(
        monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    repo_payload = {"full_name": "me/vex",
                    "html_url": "https://github.com/me/vex",
                    "description": "Local-first vector store",
                    "language": "Rust", "stargazers_count": 900,
                    "created_at": "2026-08-20T00:00:00Z", "fork": False}
    client = _Client({"https://api.github.com/repos/me/vex": repo_payload},
                     hits=[hit()])
    items = await hn.fetch_hn(client, since_hours=72, hits_per_page=100)
    enriched = await hn.enrich_from_github(client, items)

    post = enriched[0]
    assert post.stars == 180              # still the post's points
    assert post.lang == "Rust"
    assert "★900" in post.description and "Local-first" in post.description


async def test_enrichment_is_skipped_without_a_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    client = _Client(hits=[hit()])
    items = await hn.fetch_hn(client, since_hours=72, hits_per_page=100)
    before = len(client.calls)
    assert await hn.enrich_from_github(client, items) == items
    assert len(client.calls) == before      # no requests were made


async def test_a_failing_repo_lookup_keeps_the_post(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")

    class _Boom(_Client):
        async def get(self, url, **kw):
            if url.startswith("https://api.github.com"):
                return _Resp({}, status=404)
            return await super().get(url, **kw)

    client = _Boom(hits=[hit()])
    items = await hn.fetch_hn(client, since_hours=72, hits_per_page=100)
    enriched = await hn.enrich_from_github(client, items)
    assert len(enriched) == 1 and enriched[0].stars == 180


async def test_enrichment_stops_at_its_limit(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    rows = [hit(object_id=str(n), url=f"https://github.com/me/r{n}")
            for n in range(5)]
    client = _Client(hits=rows)
    items = await hn.fetch_hn(client, since_hours=72, hits_per_page=100)
    before = len(client.calls)
    await hn.enrich_from_github(client, items, limit=2)
    assert len(client.calls) - before == 2
