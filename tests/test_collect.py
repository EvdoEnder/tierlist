"""The multi-source fan-out, offline.

No adapter is called for real: each is replaced by a coroutine that returns
rows or raises. What matters here is the policy around them — that a source
switched off costs nothing, that one adapter failing does not take the
brief down with it, and that every adapter failing is loud instead of
producing an empty issue.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import item

from intel.pipeline import parse_sources, source_rules
from intel.sources import collect
from intel.sources.base import Item
from intel.sources.collect import FetchContext, SourceConfig

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 8, 30, tzinfo=UTC)
CTX = FetchContext(since_date="2026-08-27", now=NOW)


def _hn_item(item_id="hn:1"):
    return Item(id=item_id, source="hn", url="https://news.ycombinator.com/",
                name="Show HN: thing", description="", lang=None, stars=10,
                created_at=NOW)


def _sources(**enabled) -> tuple[SourceConfig, ...]:
    return tuple(SourceConfig(name=name, enabled=flag, options={})
                 for name, flag in enabled.items())


def _stub(monkeypatch, name, rows=None, error=None):
    async def fetch(client, options, ctx):
        if error is not None:
            raise error
        return list(rows or [])

    monkeypatch.setitem(collect.FETCHERS, name, fetch)


async def test_one_enabled_source_behaves_exactly_as_before(monkeypatch):
    _stub(monkeypatch, "github", [item()])
    items, counts = await collect.fetch_enabled(
        None, _sources(github=True, hn=False), CTX)
    assert [i.source for i in items] == ["github"]
    assert counts == {"github": 1}


async def test_a_disabled_source_is_never_called(monkeypatch):
    async def explode(*a, **kw):  # pragma: no cover - must not run
        raise AssertionError("a disabled source was fetched")

    monkeypatch.setitem(collect.FETCHERS, "hn", explode)
    _stub(monkeypatch, "github", [item()])
    items, counts = await collect.fetch_enabled(
        None, _sources(github=True, hn=False), CTX)
    assert counts == {"github": 1} and len(items) == 1


async def test_both_sources_arrive_with_their_counts(monkeypatch):
    _stub(monkeypatch, "github", [item()])
    _stub(monkeypatch, "hn", [_hn_item("hn:1"), _hn_item("hn:2")])
    items, counts = await collect.fetch_enabled(
        None, _sources(github=True, hn=True), CTX)
    assert counts == {"github": 1, "hn": 2}
    assert [i.source for i in items] == ["github", "hn", "hn"]


async def test_one_broken_source_does_not_lose_the_others(monkeypatch, capsys):
    # A brief with one source missing is worth far more than no brief.
    _stub(monkeypatch, "github", [item()])
    _stub(monkeypatch, "hn", error=RuntimeError("503 from algolia"))
    items, counts = await collect.fetch_enabled(
        None, _sources(github=True, hn=True), CTX)
    assert counts == {"github": 1, "hn": 0} and len(items) == 1
    assert "source hn: failed" in capsys.readouterr().err


async def test_every_source_failing_raises_instead_of_writing_an_empty_issue(
        monkeypatch):
    _stub(monkeypatch, "github", error=RuntimeError("403 from github"))
    _stub(monkeypatch, "hn", error=RuntimeError("503 from algolia"))
    with pytest.raises(RuntimeError):
        await collect.fetch_enabled(None, _sources(github=True, hn=True), CTX)


async def test_no_enabled_source_fetches_nothing():
    items, counts = await collect.fetch_enabled(
        None, _sources(github=False), CTX)
    assert (items, counts) == ([], {})


async def test_an_unknown_source_name_is_ignored_not_crashed(monkeypatch):
    # config can name a source this build has no adapter for (a half-landed
    # milestone, an older checkout); the sweep runs on what it does have.
    _stub(monkeypatch, "github", [item()])
    items, _ = await collect.fetch_enabled(
        None, _sources(github=True, npm=True), CTX)
    assert len(items) == 1


# ------------------------------------------------------------------- awesome

class _AwesomeConn:
    """Just enough store for the diff: what has been seen, and a record of
    what the fetcher wrote."""

    def __init__(self, seen=()):
        self.seen = set(seen)
        self.written: list = []
        self.commits = 0

    async def commit(self):
        self.commits += 1

    async def _fetchall(self):
        return [(item_id,) for item_id in self.seen]

    def cursor(self, **kw):
        conn = self

        class _Cur:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, sql, params=None):
                return None

            async def fetchall(self):
                return await conn._fetchall()

        return _Cur()


async def test_a_list_seen_for_the_first_time_emits_nothing(monkeypatch,
                                                            capsys):
    # Its several hundred standing entries are not several hundred
    # discoveries, and would take the whole day's paid batch.
    from intel.sources import awesome

    async def readme(client, list_name, retries=2):
        return "- [a](https://example.com/a) - one\n- [b](https://example.com/b) - two"

    monkeypatch.setattr(awesome, "fetch_readme", readme)
    conn = _AwesomeConn(seen=())
    written = []

    async def record(c, items, vertical):
        written.extend(items)
        return len(items)

    monkeypatch.setattr("intel.store.db.upsert_items", record)

    items = await collect._fetch_awesome(
        None, {"watchlists": ["own/list"]},
        FetchContext(since_date="2026-08-27", now=NOW, conn=conn))

    assert items == []
    assert [i.name for i in written] == ["a", "b"]     # recorded, not emitted
    # Marked as a catalogue import, so the issue's "new this period" count
    # does not read a list's standing entries as the day's discoveries.
    assert all(i.raw.get("baseline") is True for i in written)
    assert "baseline recorded" in capsys.readouterr().err


async def test_the_seen_read_is_committed_before_the_readme_fetch(
        monkeypatch):
    # The sweep's whole fetch runs after this read; an open transaction
    # across it is dropped by the database role's 5-minute idle timeout.
    from intel.sources import awesome

    conn = _AwesomeConn(seen=("awesome:own/list:x",))
    commits_at_fetch: list[int] = []

    async def new_entries(client, list_name, **kw):
        commits_at_fetch.append(conn.commits)
        return []

    async def enrich(client, items, limit=40):
        return items

    monkeypatch.setattr(awesome, "new_entries", new_entries)
    monkeypatch.setattr(awesome, "enrich_from_github", enrich)
    monkeypatch.setattr(awesome, "_PAUSE_S", 0)
    await collect._fetch_awesome(
        None, {"watchlists": ["own/list"]},
        FetchContext(since_date="2026-08-27", now=NOW, conn=conn))
    assert commits_at_fetch == [1]


async def test_a_dry_run_records_nothing(monkeypatch, capsys):
    from intel.sources import awesome

    async def readme(client, list_name, retries=2):
        return "- [a](https://example.com/a) - one"

    async def explode(*a, **kw):  # pragma: no cover - must not run
        raise AssertionError("a dry run wrote a baseline")

    monkeypatch.setattr(awesome, "fetch_readme", readme)
    monkeypatch.setattr("intel.store.db.upsert_items", explode)

    items = await collect._fetch_awesome(
        None, {"watchlists": ["own/list"]},
        FetchContext(since_date="2026-08-27", now=NOW,
                     conn=_AwesomeConn(seen=()), dry_run=True))
    assert items == []
    assert "would be recorded" in capsys.readouterr().err


async def test_without_a_store_the_diff_refuses_rather_than_guesses(capsys):
    items = await collect._fetch_awesome(
        None, {"watchlists": ["own/list"]},
        FetchContext(since_date="2026-08-27", now=NOW, conn=None))
    assert items == []
    assert "no store to diff against" in capsys.readouterr().err


async def test_no_watchlists_is_not_an_error():
    assert await collect._fetch_awesome(None, {}, CTX) == []


# ------------------------------------------------------------ config → rules

def test_parse_sources_keeps_file_order_and_the_enabled_flag():
    parsed = parse_sources({"sources": {"github": {"enabled": True},
                                        "hn": {"enabled": False}}})
    assert [(s.name, s.enabled) for s in parsed] == [("github", True),
                                                     ("hn", False)]


def test_rules_are_built_only_for_enabled_sources():
    cfg = _cfg({"github": {"enabled": True, "min_star_velocity_per_day": 5},
                "hn": {"enabled": False, "min_points_per_hour": 1}})
    assert set(source_rules(cfg)) == {"github"}


def test_each_source_gets_its_own_thresholds_and_the_shared_cap():
    cfg = _cfg({"github": {"enabled": True, "min_description_chars": 20,
                           "min_star_velocity_per_day": 5},
                "hn": {"enabled": True, "min_points_per_hour": 1,
                       "min_points": 5}})
    rules = source_rules(cfg)
    assert rules["github"].min_star_velocity_per_day == 5
    assert rules["github"].drop_forks is True
    assert (rules["hn"].min_points_per_hour, rules["hn"].min_points) == (1, 5)
    # HN posts have neither stars nor a description field to measure.
    assert rules["hn"].min_star_velocity_per_day == 0
    assert rules["hn"].min_description_chars == 0
    assert rules["hn"].drop_forks is False
    assert {r.max_candidates for r in rules.values()} == {cfg.max_candidates}


def _cfg(sources: dict):
    from intel.pipeline import PipelineConfig

    return PipelineConfig(lookback_hours=72, max_candidates=50,
                          temperature=0.3, max_tokens=20000, top_n=10,
                          sources=parse_sources({"sources": sources}))
