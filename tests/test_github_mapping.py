"""The GitHub adapter's row→Item mapping, against recorded-shape payloads."""
from __future__ import annotations

from datetime import UTC, datetime

from conftest import repo_row

from intel.sources.github import map_repo


def test_row_maps_to_normalized_item():
    item = map_repo(repo_row(full_name="foo/bar", stars=42,
                             description="  does things  ",
                             created_at="2026-08-21T10:00:00Z"))
    assert item.id == "github:foo/bar"
    assert item.source == "github"
    assert item.name == "foo/bar"
    assert item.url == "https://github.com/foo/bar"
    assert item.description == "does things"
    assert item.stars == 42
    assert item.created_at == datetime(2026, 8, 21, 10, 0, 0,
                                       tzinfo=UTC)
    assert item.is_fork is False


def test_null_description_becomes_empty_not_none():
    item = map_repo(repo_row(description=None))
    assert item.description == ""


def test_missing_name_or_url_is_dropped():
    assert map_repo({"full_name": "x/y"}) is None
    assert map_repo({"html_url": "https://github.com/x/y"}) is None


def test_bad_timestamp_does_not_kill_the_row():
    item = map_repo(repo_row(created_at="not-a-date"))
    assert item.created_at is None


def test_fork_flag_carries_through():
    assert map_repo(repo_row(fork=True)).is_fork is True
