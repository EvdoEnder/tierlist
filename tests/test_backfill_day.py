"""Rescoring a day the store already holds.

Two properties the recovery path lives or dies by: it must not pay twice for
a judgment the day already has, and it must not shrink a day it only topped
up. The second one is not hypothetical — on 2026-09-09 a second pass over a
day that already held 50 scores rewrote its rollup from the 2 items still
unscored.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from intel import backfill
from intel.score.llm import ScoredItem
from intel.sources.base import Item

DAY = date(2026, 9, 8)


def _item(item_id: str) -> Item:
    return Item(id=item_id, source="github", url=f"https://x/{item_id}",
                name=item_id, description="d", lang="Python", stars=10,
                created_at=datetime(2026, 9, 7, tzinfo=UTC))


def _scored(item_id: str, score: int = 70) -> ScoredItem:
    return ScoredItem(item_id=item_id, score=score,
                      dims={"novelty": 20}, reason="r", verdict="watch",
                      tags=[])


@pytest.fixture
def store(monkeypatch):
    """A stand-in store: three items seen that day, one already scored."""
    state = {
        "items": [_item("github:a/a"), _item("github:b/b"),
                  _item("github:c/c")],
        "already": {"github:a/a"},
        "day_rows": [{"item_id": "github:a/a", "score": 90},
                     {"item_id": "github:b/b", "score": 70},
                     {"item_id": "github:c/c", "score": 60}],
        "asked": [],
        "saved": [],
        "rollup": None,
        "run_started": datetime(2026, 9, 8, 0, 0, tzinfo=UTC),
    }

    async def connect():
        return SimpleNamespace(close=_noop, commit=_noop)

    async def _noop(*_a, **_kw):
        return None

    async def items_seen_on(_conn, _vertical, day):
        assert day == DAY
        return state["items"]

    async def scored_ids_on(_conn, day):
        assert day == DAY
        return set(state["already"])

    async def run_started_on(_conn, _vertical, _day):
        return state["run_started"]

    async def save_scores(_conn, scores, _items, day):
        state["saved"].append((day, [s.item_id for s in scores]))

    async def window_score_rows(_conn, days, as_of=None):
        assert (days, as_of) == (0, DAY)
        return state["day_rows"]

    async def save_rollup(_conn, vertical, day, new_count, top_ids, _dist):
        state["rollup"] = (vertical, day, new_count, list(top_ids))

    async def score_candidates(candidates, _rubric, **_kw):
        state["asked"].append([c.id for c in candidates])
        return [_scored(c.id) for c in candidates], {"total_tokens": 5}

    fake_db = SimpleNamespace(
        DSN_ENV_VAR="TIERLIST_PG_DSN", connect=connect, ensure_schema=_noop,
        items_seen_on=items_seen_on, scored_ids_on=scored_ids_on,
        run_started_on=run_started_on, save_scores=save_scores,
        window_score_rows=window_score_rows, save_rollup=save_rollup)
    monkeypatch.setattr(backfill, "db", fake_db)
    monkeypatch.setattr(backfill.llm, "score_candidates", score_candidates)
    monkeypatch.setattr(backfill, "source_rules", lambda _cfg: {})
    monkeypatch.setattr(backfill.prefilter, "drop_bursts",
                        lambda items, _rules: (items, {}))
    monkeypatch.setattr(backfill.prefilter, "apply_by_source",
                        lambda items, _r, _cap, _now: list(items))
    monkeypatch.setenv("TIERLIST_PG_DSN", "postgres://test")
    return state


CFG = SimpleNamespace(max_candidates=50, temperature=0.3, max_tokens=1000,
                      top_n=10)


@pytest.mark.anyio
async def test_a_day_pays_only_for_what_it_has_not_judged(store, rubric,
                                                          anyio_backend):
    result = await backfill.rescore_day(CFG, rubric, DAY)

    assert store["asked"] == [["github:b/b", "github:c/c"]]
    assert (result.scored, result.already_scored) == (2, 1)


@pytest.mark.anyio
async def test_rescore_judges_the_whole_cohort_again(store, rubric,
                                                     anyio_backend):
    await backfill.rescore_day(CFG, rubric, DAY, force=True)

    assert store["asked"] == [["github:a/a", "github:b/b", "github:c/c"]]


@pytest.mark.anyio
async def test_the_rollup_holds_the_whole_day_not_just_this_pass(store, rubric,
                                                                 anyio_backend):
    """The board is rebuilt from every score the date carries. A top-up pass
    that judged two items must not leave a day whose board is those two."""
    result = await backfill.rescore_day(CFG, rubric, DAY)

    _vertical, day, cohort, top_ids = store["rollup"]
    assert (day, cohort) == (DAY, 3)
    assert top_ids == ["github:a/a", "github:b/b", "github:c/c"]
    assert result.day_total == 3


@pytest.mark.anyio
async def test_a_fully_scored_day_makes_no_call(store, rubric, anyio_backend):
    store["already"] = {"github:a/a", "github:b/b", "github:c/c"}

    result = await backfill.rescore_day(CFG, rubric, DAY)

    assert store["asked"] == []
    assert (result.scored, result.already_scored) == (0, 3)


@pytest.mark.anyio
async def test_the_prefilter_reads_the_days_clock_not_todays(store, rubric,
                                                             monkeypatch,
                                                             anyio_backend):
    """Item age decides admission, so the anchor is when that sweep ran."""
    seen = {}
    monkeypatch.setattr(backfill.prefilter, "apply_by_source",
                        lambda items, _r, _cap, now: seen.setdefault(
                            "now", now) and None or list(items))

    await backfill.rescore_day(CFG, rubric, DAY)

    assert seen["now"] == datetime(2026, 9, 8, 0, 0, tzinfo=UTC)


@pytest.mark.anyio
async def test_a_day_without_a_run_row_anchors_at_noon(store, rubric,
                                                       anyio_backend):
    store["run_started"] = None
    conn = SimpleNamespace()

    anchor = await backfill.anchor_time(conn, "dev_tools", DAY)

    assert anchor == datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


@pytest.mark.anyio
async def test_backfill_without_a_store_refuses(monkeypatch, rubric,
                                                anyio_backend):
    monkeypatch.delenv("TIERLIST_PG_DSN", raising=False)

    with pytest.raises(RuntimeError, match="TIERLIST_PG_DSN"):
        await backfill.rescore_day(CFG, rubric, DAY)


class _RecordingCursor:
    def __init__(self, sink):
        self.sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.sink.append(sql)

    async def fetchall(self):
        return []


@pytest.mark.anyio
async def test_the_cohort_leaves_out_rows_the_sweep_never_scores(
        anyio_backend):
    # The star-only cohorts and an awesome-list's catalogue share first_seen
    # with the day's real candidates; on 2026-10-16 they outnumbered them
    # ten to one, and rescoring them billed for rows no live run rated.
    from intel.store import db

    sink: list = []
    conn = SimpleNamespace(cursor=lambda **kw: _RecordingCursor(sink))

    assert await db.items_seen_on(conn, "dev_tools", DAY) == []

    (sql,) = sink
    assert "raw->>'board_only'" in sql
    assert "raw->>'baseline'" in sql
