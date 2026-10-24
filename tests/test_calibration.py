"""Score calibration, offline: the pairing rules and the statistic.

The report's value depends entirely on refusing to answer when the data
cannot support an answer, so most of this file is about what it declines to
count — a series that stops short of the horizon, a re-score used as if it
were the original prediction, a rank correlation over three points.
"""
from __future__ import annotations

from datetime import date

import pytest

from intel import calibration

pytestmark = pytest.mark.anyio

D = date(2026, 8, 1)


def _series(pairs):
    return [(date.fromordinal(D.toordinal() + day), stars)
            for day, stars in pairs]


# ------------------------------------------------------------------- pairing

def test_growth_is_the_endpoint_minus_the_score_day():
    series = _series([(0, 100), (30, 460)])
    assert calibration.growth_at(series, D, 30) == (360, 30)


def test_a_series_that_stops_short_of_the_horizon_is_not_an_observation():
    # Three days of history cannot answer a 30-day question. Counting it as
    # zero growth would drag every band toward zero and look like a rubric
    # that predicts nothing.
    assert calibration.growth_at(_series([(0, 100), (3, 130)]), D, 30) is None


def test_a_missed_refresh_inside_the_slack_still_counts():
    # 30-day horizon tolerates ~4 days; the endpoint at day 33 is accepted
    # and the span is reported as it truly was.
    assert calibration.growth_at(_series([(0, 100), (33, 300)]), D, 30) \
        == (200, 33)


def test_an_endpoint_beyond_the_slack_is_refused():
    assert calibration.growth_at(_series([(0, 100), (40, 300)]), D, 30) is None


def test_the_baseline_must_sit_near_the_score_date():
    # First snapshot ten days after the score: the growth before it was
    # never observed, so this item cannot enter the 7-day horizon.
    assert calibration.growth_at(_series([(10, 100), (17, 300)]), D, 7) is None


def test_the_nearest_snapshot_to_the_score_date_is_the_baseline():
    series = _series([(-1, 90), (0, 100), (7, 150)])
    assert calibration.growth_at(series, D, 7) == (50, 7)


def test_no_series_is_no_observation():
    assert calibration.growth_at([], D, 7) is None


def test_a_decline_is_reported_as_a_decline():
    # Stars can be removed; a negative gain is data, not an error.
    assert calibration.growth_at(_series([(0, 100), (7, 80)]), D, 7) == (-20, 7)


# ----------------------------------------------------------------- statistic

def test_a_perfect_ranking_is_rho_one():
    assert calibration.spearman([(1, 10), (2, 20), (3, 30), (4, 40)]) == 1.0


def test_an_inverted_ranking_is_rho_minus_one():
    assert calibration.spearman([(1, 40), (2, 30), (3, 20), (4, 10)]) == -1.0


def test_too_few_pairs_answer_none_rather_than_a_number():
    assert calibration.spearman([(1, 10), (2, 20), (3, 30)]) is None


def test_a_flat_series_has_no_correlation_to_report():
    assert calibration.spearman([(5, 1), (5, 2), (5, 3), (5, 4)]) is None


# -------------------------------------------------------------------- report

def test_buckets_hold_their_shape_even_when_empty():
    block = calibration.summarize([(50, 10, 30), (55, 30, 30)])
    labels = [b["label"] for b in block["buckets"]]
    assert labels == [label for _, _, label in calibration.BUCKETS]
    empty = [b for b in block["buckets"] if b["label"].startswith("70+")]
    assert empty[0]["n"] == 0 and empty[0]["median_gain"] is None


def test_the_median_carries_the_band_not_one_runaway():
    block = calibration.summarize([(65, 10, 30), (65, 20, 30), (65, 9000, 30)])
    band = [b for b in block["buckets"] if b["n"]][0]
    assert band["median_gain"] == 20 and band["max_gain"] == 9000


def test_bucket_edges_land_on_the_rubrics_line():
    assert calibration.bucket_of(59) != calibration.bucket_of(60)
    assert "过线" in calibration.bucket_of(60)


class _Cur:
    def __init__(self, rows_by_call, sink):
        self.rows_by_call = rows_by_call
        self.sink = sink
        self.last = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.sink.append(sql)
        for needle, rows in self.rows_by_call.items():
            if needle in sql:
                self.last = rows
                return
        self.last = []

    async def fetchall(self):
        return self.last

    async def fetchone(self):
        return self.last[0] if self.last else (0, None, None, 0, 0)


class _Conn:
    def __init__(self, rows_by_call):
        self.rows_by_call = rows_by_call
        self.sql: list[str] = []

    def cursor(self, **kw):
        return _Cur(self.rows_by_call, self.sql)


async def test_report_uses_the_first_score_and_only_reachable_horizons():
    scored_on = date(2026, 8, 1)
    conn = _Conn({
        "FROM intel.scores": [("github:a/b", scored_on, 72),
                              ("github:c/d", scored_on, 41)],
        "FROM intel.star_snapshots\n            WHERE item_id": [
            ("github:a/b", scored_on, 100),
            ("github:a/b", date(2026, 8, 8), 400),
            ("github:c/d", scored_on, 100),
            ("github:c/d", date(2026, 8, 8), 110),
        ],
        "FROM intel.star_snapshots": [(2, scored_on, date(2026, 8, 8), 0, 7)],
    })

    result = await calibration.report(conn, horizons=(7, 30))

    assert result["scored_items"] == 2
    assert result["horizons"][7]["n"] == 2
    assert result["horizons"][30]["n"] == 0     # nothing spans a month yet
    top = [b for b in result["horizons"][7]["buckets"]
           if b["label"] == "70+"][0]
    assert top["median_gain"] == 300


async def test_render_says_the_window_is_empty_instead_of_printing_zeroes():
    conn = _Conn({
        "FROM intel.scores": [("github:a/b", date(2026, 8, 1), 72)],
        "FROM intel.star_snapshots\n            WHERE item_id": [
            ("github:a/b", date(2026, 8, 1), 100)],
        "FROM intel.star_snapshots": [(1, date(2026, 8, 1),
                                       date(2026, 8, 1), 0, 0)],
    })
    text = calibration.render(await calibration.report(conn, horizons=(30,)))
    assert "还没有任何项目的快照跨过这个窗口" in text
