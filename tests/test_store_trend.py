"""Store-side pure math: language distribution and repeat-strength mapping."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from intel.score.llm import ScoredItem
from intel.sources.base import Item
from intel.store.trend import annotate_repeat, lang_dist


def _item(item_id, lang):
    return Item(id=item_id, source="github", url=f"https://github.com/{item_id}",
                name=item_id, description="d", lang=lang, stars=1,
                created_at=None)


def test_lang_dist_counts_each_language():
    items = [_item("a", "Rust"), _item("b", "Rust"), _item("c", "Python")]
    assert lang_dist(items) == {"Rust": 2, "Python": 1}


def test_lang_dist_buckets_missing_language_as_unknown():
    items = [_item("a", None), _item("b", None), _item("c", "Go")]
    assert lang_dist(items) == {"unknown": 2, "Go": 1}


def test_lang_dist_empty_input_is_empty_map():
    assert lang_dist([]) == {}


@dataclass(frozen=True, slots=True)
class _Prior:
    item_id: str
    snap_date: date
    score: int


TODAY = date(2026, 8, 24)


def _scored(item_id="github:a/b"):
    return ScoredItem(item_id=item_id, score=70, dims={}, reason="",
                      verdict="watch")


def test_repeat_maps_the_best_prior_score():
    rows = [_Prior("github:a/b", date(2026, 8, 20), 55),
            _Prior("github:a/b", date(2026, 8, 22), 61)]
    assert annotate_repeat([_scored()], rows, today=TODAY) == {"github:a/b": 61}


def test_repeat_ignores_same_day_and_later_rows():
    # Today's own snapshot and future-dated noise are not history; the
    # strictly-earlier row is all that survives.
    rows = [_Prior("github:a/b", TODAY, 90),
            _Prior("github:a/b", date(2026, 8, 25), 95),
            _Prior("github:a/b", date(2026, 8, 21), 40)]
    assert annotate_repeat([_scored()], rows, today=TODAY) == {"github:a/b": 40}


def test_repeat_only_covers_scored_items():
    rows = [_Prior("github:x/y", date(2026, 8, 20), 88),
            _Prior("github:a/b", date(2026, 8, 20), 50)]
    assert annotate_repeat([_scored()], rows, today=TODAY) == {"github:a/b": 50}


def test_repeat_empty_history_is_empty_map():
    assert annotate_repeat([_scored()], [], today=TODAY) == {}
