"""The star-refresh pass, offline.

Nothing here reaches GitHub: the GraphQL document and the reading of its
answer are pure functions, and `refresh` runs against a recording cursor
with the fetch stubbed. What the tests pin down is the behaviour that makes
the pass safe to run inside the daily sweep — that a repository which no
longer resolves writes no snapshot, that a token-less box degrades to a skip
instead of an exception, and that ids keep their `github:` prefix on the way
back into the store.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from intel import resnapshot
from intel.sources import github_stars

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------- pure halves

def test_full_names_strips_the_source_prefix():
    assert resnapshot.full_names(["github:owner/repo", "github:a/b"]) == [
        "owner/repo", "a/b"]


def test_full_names_drops_ids_no_repository_query_could_use():
    # hn/rss items have no owner/name, and a malformed github id is not a repo
    assert resnapshot.full_names(
        ["hn:12345", "rss:devto:abc", "github:nolslash", "github:ok/one"]
    ) == ["ok/one"]


def test_document_aliases_are_positional_and_map_back():
    document, aliases = github_stars.build_document(["a/b", "c/d"])
    assert aliases == {"r0": "a/b", "r1": "c/d"}
    assert "r0: repository(owner: \"a\", name: \"b\")" in document
    assert "stargazerCount" in document


def test_document_escapes_a_hostile_name_instead_of_interpolating_it():
    # A repository name may contain a quote; interpolation would let it close
    # the string and rewrite the query.
    document, aliases = github_stars.build_document(['ow"ner/re"po'])
    assert json.dumps('ow"ner') in document
    assert document.count("repository(") == 1


def test_read_counts_keeps_the_nodes_that_resolved():
    payload = {"data": {"r0": {"stargazerCount": 120},
                        "r1": None,                      # deleted or private
                        "r2": {"stargazerCount": 7}},
               "errors": [{"type": "NOT_FOUND"}]}
    aliases = {"r0": "a/b", "r1": "gone/away", "r2": "c/d"}
    assert github_stars.read_counts(payload, aliases) == {"a/b": 120, "c/d": 7}


def test_read_counts_ignores_an_alias_it_did_not_ask_for():
    assert github_stars.read_counts(
        {"data": {"rX": {"stargazerCount": 5}}}, {"r0": "a/b"}) == {}


def test_missing_token_is_a_named_error(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(github_stars.MissingToken):
        github_stars._headers()


# ------------------------------------------------------------------- refresh

class _Cur:
    def __init__(self, sink, rows):
        self.sink = sink
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.sink.append((sql, params))

    async def executemany(self, sql, params=None):
        self.sink.append((sql, params))

    async def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.calls: list = []
        self.rows = rows
        self.commits = 0

    def cursor(self, **kw):
        return _Cur(self.calls, self.rows)

    async def commit(self):
        self.commits += 1


def _stub_fetch(monkeypatch, counts):
    async def fake(client, names, batch_size=100):
        return {name: counts[name] for name in names if name in counts}

    monkeypatch.setattr(github_stars, "fetch_star_counts", fake)


async def test_refresh_writes_todays_row_for_every_answer(monkeypatch):
    conn = _Conn([("github:a/b",), ("github:c/d",)])
    _stub_fetch(monkeypatch, {"a/b": 300, "c/d": 12})

    result = await resnapshot.refresh(conn, date(2026, 8, 30))

    assert (result.cohort, result.refreshed, result.written, result.missing) \
        == (2, 2, 2, 0)
    written = [c for c in conn.calls if "star_snapshots" in c[0]]
    assert written and sorted(written[0][1]) == [
        ("github:a/b", date(2026, 8, 30), 300),
        ("github:c/d", date(2026, 8, 30), 12),
    ]


async def test_a_repository_that_no_longer_resolves_writes_nothing(monkeypatch):
    # Carrying yesterday's number forward would draw a project that GitHub
    # deleted as one that simply stopped growing.
    conn = _Conn([("github:a/b",), ("github:gone/away",)])
    _stub_fetch(monkeypatch, {"a/b": 300})

    result = await resnapshot.refresh(conn, date(2026, 8, 30))

    assert (result.refreshed, result.missing) == (1, 1)
    written = [c for c in conn.calls if "star_snapshots" in c[0]]
    assert [row[0] for row in written[0][1]] == ["github:a/b"]


async def test_dry_run_fetches_and_reports_but_writes_nothing(monkeypatch):
    conn = _Conn([("github:a/b",)])
    _stub_fetch(monkeypatch, {"a/b": 300})

    result = await resnapshot.refresh(conn, date(2026, 8, 30), dry_run=True)

    assert (result.refreshed, result.written) == (1, 0)
    assert not [c for c in conn.calls if "INSERT INTO intel.star_snapshots" in c[0]]


async def test_an_empty_cohort_makes_no_request(monkeypatch):
    async def explode(*a, **kw):  # pragma: no cover - must not be called
        raise AssertionError("fetched with an empty cohort")

    monkeypatch.setattr(github_stars, "fetch_star_counts", explode)
    result = await resnapshot.refresh(_Conn([]), date(2026, 8, 30))
    assert result == resnapshot.RefreshResult(0, 0, 0, 0)


async def test_the_sweep_entry_point_swallows_a_missing_token(monkeypatch,
                                                              capsys):
    # The brief is already paid for by the time the refresh runs; losing it
    # to an unset token would be the expensive failure, not the cheap one.
    async def raise_missing(*a, **kw):
        raise github_stars.MissingToken("GITHUB_TOKEN is not set")

    monkeypatch.setattr(github_stars, "fetch_star_counts", raise_missing)
    out = await resnapshot.refresh_quietly(_Conn([("github:a/b",)]),
                                           date(2026, 8, 30),
                                           track_days=45, limit=10,
                                           batch_size=100)
    assert out is None
    assert "resnapshot: skipped" in capsys.readouterr().err


async def test_the_sweep_entry_point_swallows_a_network_failure(monkeypatch,
                                                                capsys):
    async def boom(*a, **kw):
        raise RuntimeError("502 on graphql")

    monkeypatch.setattr(github_stars, "fetch_star_counts", boom)
    out = await resnapshot.refresh_quietly(_Conn([("github:a/b",)]),
                                           date(2026, 8, 30),
                                           track_days=45, limit=10,
                                           batch_size=100)
    assert out is None
    assert "resnapshot: failed" in capsys.readouterr().err


async def test_refresh_commits_the_cohort_read_before_fetching(monkeypatch):
    # The fetch outlasts idle_in_transaction_session_timeout on a large
    # cohort; an open read transaction across it costs the whole run.
    conn = _Conn([("github:a/b",)])
    seen: list[int] = []

    async def fake(client, names, batch_size=100):
        seen.append(conn.commits)
        return {"a/b": 1}

    monkeypatch.setattr(github_stars, "fetch_star_counts", fake)
    await resnapshot.refresh(conn, date(2026, 8, 30))
    assert seen == [1]
