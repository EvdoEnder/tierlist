"""The 新仓库热榜 write path — the half no test covered.

`finish_run` is the only writer of intel.runs.hot_new, and the board it
stores is the only faithful record of what a page published: the board is a
slice of the raw fetch taken BEFORE the prefilter, so those repositories may
never reach intel.items at all. A mutation that simply stopped writing the
column passed the entire suite, which is why this file exists.

No database: a recording cursor stands in, and the assertions are on the SQL
and the bound parameters.
"""
from __future__ import annotations

import pytest

from intel.store import db
from intel.store.schema import SCHEMA_STATEMENTS


class _Cur:
    def __init__(self, sink):
        self.sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.sink.append((sql, params))


class _Conn:
    def __init__(self):
        self.calls: list = []
        self.commits = 0

    def cursor(self, **kw):
        return _Cur(self.calls)

    async def commit(self):
        self.commits += 1


_BOARD = [{"rank": "01", "name": "a/b", "url": "u", "stars": 900,
           "description": "x"}]


async def _finish(conn, **over):
    kwargs = dict(status="ok", fetched=300, candidates=40, scored=30,
                  model="m", usage={}, error=None)
    kwargs.update(over)
    await db.finish_run(conn, 7, **kwargs)


@pytest.mark.anyio
async def test_finish_run_stores_the_board(anyio_backend):
    conn = _Conn()
    await _finish(conn, hot_new=_BOARD)
    sql, params = conn.calls[0]
    assert "hot_new" in sql
    payload = [p for p in params if getattr(p, "obj", None) == _BOARD]
    assert payload, "the board was never bound to the UPDATE"


@pytest.mark.anyio
async def test_finish_run_keeps_an_existing_board_when_given_none(
        anyio_backend):
    """The error path calls finish_run without a board. Writing NULL there
    would erase the board of a successful earlier run of the same day."""
    conn = _Conn()
    await _finish(conn, status="error", error="boom")
    sql, params = conn.calls[0]
    assert "COALESCE(%s::jsonb, intel.runs.hot_new)" in sql
    assert None in params


@pytest.mark.anyio
async def test_the_board_parameter_is_cast_to_the_column_type(anyio_backend):
    """psycopg adapts Json() to `json`, while the column is `jsonb`. Postgres
    resolves an assignment across the two but not a COALESCE, which needs a
    common type: uncast, the statement fails with `COALESCE could not convert
    type jsonb to json` and takes the whole run down with it. The recording
    cursor of this file never reaches a server, so only the SQL can say so."""
    conn = _Conn()
    await _finish(conn, status="ok", hot_new=[{"rank": 1}])
    sql, _ = conn.calls[0]
    assert "COALESCE(%s::jsonb," in sql


@pytest.mark.anyio
async def test_finish_run_still_writes_the_counters(anyio_backend):
    conn = _Conn()
    await _finish(conn, hot_new=_BOARD)
    sql, params = conn.calls[0]
    for col in ("status", "fetched", "candidates", "scored", "usage"):
        assert col in sql
    assert 300 in params and 40 in params
    assert conn.commits == 1


def test_the_column_is_added_additively():
    assert any("ALTER TABLE intel.runs ADD COLUMN IF NOT EXISTS hot_new"
               in s for s in SCHEMA_STATEMENTS)


def test_the_read_side_selects_it():
    """run_stats_for_date is what a rebuilt page reads; a SELECT that forgets
    the column turns every rebuild back into a board-less page."""
    import inspect

    # The SELECT list, not the source text: the docstring names the column
    # too, and grepping the whole function passed a mutant that had removed
    # it from the query.
    src = inspect.getsource(db.run_stats_for_date)
    select = next(ln for ln in src.splitlines() if "SELECT" in ln)
    assert "hot_new" in select, select


def test_the_reconstruction_reads_the_fetch_population():
    """The fallback must scan star_snapshots — the whole day's fetch — not
    intel.scores, which holds only the candidates that survived prefilter."""
    import inspect

    src = inspect.getsource(db.hot_new_for_date)
    assert "star_snapshots" in src
    assert "ORDER BY s.stars DESC" in src
    assert "LIMIT 10" in src
    assert "intel.scores" not in src
