"""The static JSON API: what each document holds and, above all, what it
never holds — the 0-100 score and its dimensions stay in the store."""
from __future__ import annotations

import json

import pytest

from intel.deliver import api


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def _entry(**kw):
    return {"id": "github:a/b", "name": "a/b", "url": "https://github.com/a/b",
            "source": "github", "tier": "A", "reason": "does one thing well",
            "description": "a tool", "tags": ["cli"], "stars": 40,
            "lang": "Rust", "score": 61, "dims": {"novelty": 18},
            "high_score": True, "is_watch": True, **kw}


def _ctx():
    return {
        "lede": {"window_from": "2026-10-22", "window_to": "2026-10-22",
                 "new_count": 300, "fetched": 320, "model": "m",
                 "top_score": 61, "watch_min": 44},
        "entries": [_entry()],
        "hn_entries": [],
        "gain_board": {"ready": True, "window_days": 1,
                       "rows": [{"rank": "01", "name": "c/d", "url": "u",
                                 "gain": 9, "stars_now": 90, "score": 70}]},
        "hot_new": [{"rank": "01", "name": "e/f", "url": "u2", "stars": 5,
                     "description": "x"}],
        "trending": {"daily": [{"rank": "01", "name": "g/h", "url": "u3",
                                "stars": 1000, "gain": 50, "tags": []}]},
    }


def test_issue_doc_never_carries_a_score():
    doc = api.issue_doc(_ctx(), edition="daily", slug="2026-10-22",
                        site_url="https://tierlist.tech")
    keys = set(_walk_keys(doc))
    assert not keys & {"score", "dims", "top_score", "watch_min",
                       "high_score", "is_watch"}
    assert doc["items"][0]["tier"] == "A"
    assert "61" not in json.dumps(doc)


def test_issue_doc_names_its_page_and_item_documents():
    doc = api.issue_doc(_ctx(), edition="daily", slug="2026-10-22",
                        site_url="https://tierlist.tech")
    assert doc["api_version"] == 1
    assert doc["page"] == "https://tierlist.tech/daily/2026-10-22.html"
    assert doc["items"][0]["api"] == (
        "https://tierlist.tech/api/v1/items/github/a/b.json")
    assert set(doc["boards"]) == {"hot_new", "gain_1d", "trending_daily"}


def test_an_unknown_language_is_null_not_the_chinese_fallback():
    doc = api.issue_doc({**_ctx(), "entries": [_entry(lang="语言未知")]},
                        edition="daily", slug="2026-10-22", site_url="")
    assert doc["items"][0]["language"] is None


@pytest.mark.parametrize("item_id,path", [
    ("github:a/b", "items/github/a/b.json"),
    ("github:a.b-c/d_e.f", "items/github/a.b-c/d_e.f.json"),
    ("hn:49464834", "items/hn/49464834.json"),
    ("github:a/..", None),
    ("github:../b", None),
    ("github:a/b/c", None),
    ("awesome:x/y:https://z", None),
    (None, None),
])
def test_item_path_maps_only_safe_ids(item_id, path):
    assert api.item_path(item_id) == path


def test_item_doc_lists_every_rating_as_a_tier():
    doc = api.item_doc(
        {"id": "github:a/b", "name": "a/b", "url": "u", "source": "github",
         "description": "d", "lang": "Go", "tags": ["cli"],
         "first_seen": "2026-10-20"},
        [{"date": "2026-10-22", "score": 70, "dims": {"novelty": 1},
          "reason": "r", "reason_en": "e", "stars": 9}],
        tier_of=lambda score, dims, version: "S" if score >= 69 else "A")
    assert doc["ratings"] == [{"date": "2026-10-22", "tier": "S",
                               "reason": "r", "reason_en": "e", "stars": 9}]
    assert not set(_walk_keys(doc)) & {"score", "dims"}


def test_latest_points_at_the_newest_issue_of_each_edition():
    doc = api.index_doc({"daily": ["2026-10-21", "2026-10-22"],
                         "weekly": [], "monthly": ["2026-10"]},
                        site_url="https://t")
    assert doc["latest"] == {"daily": "https://t/api/v1/daily/2026-10-22.json",
                             "weekly": None,
                             "monthly": "https://t/api/v1/monthly/2026-10.json"}
    assert doc["issues"]["daily"] == ["2026-10-21", "2026-10-22"]


def test_tag_urls_are_percent_encoded():
    doc = api.tags_index_doc([{"tag": "逆向", "count": 3}],
                             {"逆向": {"zh": "逆向", "en": "Reverse"}},
                             site_url="https://t")
    assert doc["tags"][0]["api"] == "https://t/api/v1/tags/%E9%80%86%E5%90%91.json"
