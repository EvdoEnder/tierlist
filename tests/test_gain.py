"""Gain-board math, offline: the pure core plus the readiness wrapper.

No database is touched — `compute_gains` takes plain pairs, `gain_rows` runs
against a recording cursor, and `editions.wrap_gain` shapes whatever
`gain_rows` would return.
"""
from __future__ import annotations

import pytest

from intel.editions import wrap_gain
from intel.store import gain
from intel.store.gain import compute_gains, reference_slack

pytestmark = pytest.mark.anyio


def test_gain_is_now_minus_reference():
    gains = compute_gains([("a", 500), ("b", 120)],
                          [("a", 400), ("b", 130)], window=7)
    assert dict((g[0], g[2]) for g in gains) == {"a": 100, "b": -10}


def test_stars_now_travels_through_the_tuple():
    gains = compute_gains([("a", 555)], [("a", 500)], window=1)
    assert gains == [("a", 555, 55)]


def test_sorted_best_first_ties_break_by_item_id():
    # all three gain 50; the id tiebreak keeps the order deterministic.
    gains = compute_gains(
        [("c", 300), ("b", 210), ("a", 200)],
        [("c", 250), ("b", 160), ("a", 150)], window=1)
    assert [(g[0], g[1], g[2]) for g in gains] == [
        ("a", 200, 50), ("b", 210, 50), ("c", 300, 50)]


def test_missing_past_pair_is_excluded():
    gains = compute_gains([("new", 90), ("old", 80)],
                          [("old", 70)], window=30)
    assert gains == [("old", 80, 10)]


def test_zero_gain_items_rank_last_but_survive():
    gains = compute_gains([("flat", 100), ("riser", 150)],
                          [("flat", 100), ("riser", 100)], window=7)
    assert [g[0] for g in gains] == ["riser", "flat"]


def test_empty_inputs_are_empty():
    assert compute_gains([], [], window=1) == []


def test_wrap_gain_ranks_rows_only_when_ready():
    not_ready = wrap_gain({"ready": False, "have": 2, "need": 8,
                           "rows": None}, "weekly")
    assert not_ready["ready"] is False and not_ready["rows"] is None
    assert not_ready["window_days"] == 7
    ready = wrap_gain({"ready": True, "have": 9, "need": 8,
                       "rows": [{"name": "x/y", "url": "u", "lang": "Rust",
                                 "stars_now": 90, "gain": 30}]}, "monthly")
    assert ready["ready"] is True
    assert ready["window_days"] == 30
    assert ready["rows"][0]["rank"] == "01"


# ------------------------------------------------- readiness is per item now

def test_slack_is_one_sided_and_at_least_a_day():
    # A reference slightly OLDER than the window overstates the gain a
    # little; one slightly NEWER would print a short span under a long
    # title, which is the failure the board exists to prevent.
    assert reference_slack(1) == 1
    assert reference_slack(7) == 1
    assert reference_slack(30) == 4


class _Cur:
    def __init__(self, coverage, rows, sink):
        self.coverage = coverage
        self.rows = rows
        self.sink = sink
        self.kind = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.sink.append((sql, params))
        self.kind = "coverage" if "GROUP BY item_id" in sql else "rows"

    async def fetchone(self):
        return self.coverage

    async def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, coverage, rows=()):
        self.coverage = coverage
        self.rows = list(rows)
        self.calls: list = []

    def cursor(self, **kw):
        return _Cur(self.coverage, self.rows, self.calls)


async def test_a_table_spanning_the_window_is_not_coverage():
    # The table reached back 30 days while every single item held three days
    # of history — the state the store was actually in on 2026-08-29. The
    # board must stay dark rather than pair each item with its own birth.
    conn = _Conn(coverage=(3, 0))
    board = await gain.gain_rows(conn, 30)
    assert board["ready"] is False and board["rows"] is None
    assert (board["have"], board["need"]) == (4, 31)
    assert not [c for c in conn.calls if "past_stars" in c[0]]


async def test_one_item_spanning_the_window_lights_the_board():
    conn = _Conn(coverage=(31, 1),
                 rows=[("github:a/b", 400, 900, "a/b", "u", "Rust")])
    board = await gain.gain_rows(conn, 30)
    assert board["ready"] is True
    assert board["rows"] == [{"name": "a/b", "url": "u", "lang": "Rust",
                              "stars_now": 900, "gain": 400}]


async def test_coverage_without_a_surviving_pair_stays_dark():
    # An item can span the window and still have no snapshot on the latest
    # date (GitHub stopped answering for it), leaving the query empty. The
    # board then says 积累中 rather than showing an empty ranked list.
    board = await gain.gain_rows(_Conn(coverage=(31, 1), rows=[]), 30)
    assert board["ready"] is False and board["rows"] is None


async def test_an_empty_table_reports_no_days_of_history():
    board = await gain.gain_rows(_Conn(coverage=(-1, 0)), 7)
    assert (board["ready"], board["have"], board["need"]) == (False, 0, 8)
