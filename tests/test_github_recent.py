"""github_recent — star bands under the 1000-result cap, and the tag queue."""
from __future__ import annotations

import asyncio

from conftest import item

from intel.pipeline import recent_tag_queue
from intel.sources import github_recent
from intel.sources.github_recent import initial_bands, split_band


def test_bands_cover_the_floor_upward_without_gaps():
    assert initial_bands(100) == [(100, 119), (120, 149), (150, 199),
                                  (200, 299), (300, 499), (500, 999),
                                  (1000, None)]
    # Edges at or below the floor are dropped, the floor opens the first band.
    assert initial_bands(400) == [(400, 499), (500, 999), (1000, None)]


def test_split_halves_a_closed_band_and_keeps_an_open_band_open():
    assert split_band(100, 119) == [(100, 109), (110, 119)]
    assert split_band(1000, None) == [(1000, 1999), (2000, None)]


def _repo(name, stars):
    return {"full_name": name, "html_url": f"https://github.com/{name}",
            "stargazers_count": stars, "created_at": "2026-08-23T13:00:39Z",
            "description": "d", "language": "Swift", "fork": False}


def test_an_oversized_band_is_split_and_every_row_is_marked(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    async def fake_get(client, url, *, params, headers):
        q = params["q"]
        calls.append(q)
        if "stars:100..119" in q:
            return Resp({"total_count": 5000, "items": []})
        if "stars:100..109" in q:
            return Resp({"total_count": 1,
                         "items": [_repo("laogou717/dogsc", 105)]})
        return Resp({"total_count": 0, "items": []})

    async def no_sleep(_):
        return None

    monkeypatch.setattr(github_recent, "_retryable_get", fake_get)
    monkeypatch.setattr(github_recent.asyncio, "sleep", no_sleep)
    rows = asyncio.run(github_recent.fetch_recent(None, "2026-08-16", 100))

    assert [r.id for r in rows] == ["github:laogou717/dogsc"]
    assert rows[0].raw["board_only"] and rows[0].raw["recent"]
    assert any("stars:110..119" in q for q in calls)
    assert all("created:>2026-08-16" in q for q in calls)


def test_tag_queue_puts_todays_arrivals_first_then_stars():
    old_big = item("github:a/big", stars=900)
    old_small = item("github:a/small", stars=150)
    arrival = item("github:laogou717/dogsc", stars=105)
    tagged = item("github:a/tagged", stars=5000)
    queue = recent_tag_queue(
        [old_small, tagged, old_big, arrival],
        untagged={"github:a/big", "github:a/small", "github:laogou717/dogsc"},
        new_ids={"github:laogou717/dogsc"}, limit=2)
    assert [i.id for i in queue] == ["github:laogou717/dogsc", "github:a/big"]
