"""Tier lines per model version: the percentiles, the lookup, the version
check, the calibration and the drift warning. No network, no database: the
store is an in-memory fake and the model a function of the item id."""
from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from intel import calibrate, tiers
from intel.score import llm
from intel.score.llm import ScoredItem

pytestmark = pytest.mark.real_calibration

DIMS = ["commercial", "momentum", "novelty", "utility"]   # the legacy set
TODAY = date(2026, 11, 1)


@pytest.fixture(autouse=True)
def _clean_lines():
    tiers.use_store_lines([])
    yield
    tiers.use_store_lines([])


def _current_dims() -> list[str]:
    return sorted(tiers._load("dev_tools")[2])


def _row(version, s, a, b, c, dims=None):
    return {"vertical": "dev_tools", "model_version": version,
            "dims": dims or _current_dims(), "s_line": s, "a_line": a,
            "b_line": b, "c_line": c}


# ----------------------------------------------------------- percentiles

def test_lines_are_the_lowest_score_inside_each_top_share():
    lines = calibrate.percentile_lines(list(range(1, 101)))
    assert lines == {"S": 99, "A": 91, "B": 71, "C": 41}


def test_tied_lines_are_pushed_apart_so_they_still_descend():
    assert calibrate.percentile_lines([50] * 300) == {"S": 50, "A": 49, "B": 48, "C": 47}


# ---------------------------------------------------------------- lookup

def test_a_score_without_a_version_keeps_the_rubric_lines():
    tiers.use_store_lines([_row("New-Model", 90, 80, 70, 60)])
    assert tiers.lines("dev_tools") == tiers._load("dev_tools")[0]
    assert tiers.tier(69) == "S"


def test_a_versioned_score_is_read_against_its_own_lines():
    tiers.use_store_lines([_row("Old", 69, 58, 44, 29), _row("New", 80, 70, 50, 30)])
    assert tiers.tier(75, version="Old") == "S"
    assert tiers.tier(75, version="New") == "A"


def test_a_version_waiting_for_calibration_borrows_the_newest_lines():
    tiers.use_store_lines([_row("Old", 69, 58, 44, 29), _row("Newer", 80, 70, 50, 30)])
    assert tiers.lines("dev_tools", version="Not-yet") == tiers.lines("dev_tools", version="Newer")


def test_the_newest_row_for_a_version_wins():
    tiers.use_store_lines([_row("V", 69, 58, 44, 29), _row("V", 72, 60, 45, 30)])
    assert tiers.lines("dev_tools", version="V")[0] == (72, "S")


def test_an_earlier_rubric_keeps_its_lines_whatever_the_version():
    tiers.use_store_lines([_row("V", 90, 80, 70, 60)])
    dims = {k: 10 for k in DIMS}
    assert tiers.lines("dev_tools", dims, "V")[0] == (77, "S")


def test_a_calibration_changes_the_page_fingerprint():
    before = tiers.fingerprint()
    tiers.use_store_lines([_row("V", 90, 80, 70, 60)])
    assert tiers.fingerprint() != before


# ---------------------------------------------------------- model_info

def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_the_version_is_the_display_name_models_lists(monkeypatch):
    monkeypatch.delenv("TIERLIST_LLM_MODEL", raising=False)
    monkeypatch.delenv("TIERLIST_LLM_BASE_URL", raising=False)

    def handler(request):
        assert request.url.path == "/models"
        return httpx.Response(200, json={"data": [
            {"id": "deepseek-v4-pro", "name": "DeepSeek-V4-Pro"},
            {"id": "deepseek-flash", "name": "DeepSeek-V4.1-Flash",
             "max_output_tokens": 65536}]})

    async with _client(handler) as client:
        info = await llm.model_info(client)
    assert info == llm.ModelInfo("DeepSeek-V4.1-Flash", 65536)


@pytest.mark.anyio
async def test_without_a_models_route_the_version_is_the_model_id(monkeypatch):
    monkeypatch.setenv("TIERLIST_LLM_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash-1017")
    async with _client(lambda request: httpx.Response(404)) as client:
        info = await llm.model_info(client)
    assert info == llm.ModelInfo("deepseek-ai/DeepSeek-V4.1-Flash-1017", None)


# ------------------------------------------------------- the fake store

class FakeStore:
    """The slice of intel.store.db that calibrate touches. It has no
    save_scores: a calibration that tried to write a published score would
    fail here."""

    def __init__(self, cohort=None, lines=None):
        self.cohort = cohort or []
        self.lines = list(lines or [])
        self.cal: dict[str, dict[str, int]] = {}
        self.recent: list[int] = []

    async def tier_line_rows(self, conn):
        return list(self.lines)

    async def save_tier_lines(self, conn, *, vertical, model_version, dims,
                              lines, sample, source, window_from=None,
                              window_to=None):
        self.lines.append({**_row(model_version, lines["S"], lines["A"],
                                  lines["B"], lines["C"], sorted(dims)),
                           "sample": sample, "source": source})

    async def calibration_cohort(self, conn, *, dims, since, until, limit):
        return [r for r in self.cohort if since <= r["snap_date"] <= until][:limit]

    async def calibration_scores(self, conn, model_version):
        return dict(self.cal.get(model_version, {}))

    async def save_calibration_scores(self, conn, model_version, scored, days):
        self.cal.setdefault(model_version, {}).update(
            {s.item_id: s.score for s in scored})

    async def clear_calibration_scores(self, conn, model_version):
        self.cal.pop(model_version, None)

    async def recent_version_scores(self, conn, *, model_version, since, until):
        return list(self.recent)


class FakeConn:
    async def commit(self):
        pass

    async def rollback(self):
        pass


def _cohort(n, day=date(2026, 10, 20)):
    return [{"item_id": f"github:o/r{i}", "snap_date": day, "stars": 10,
             "source": "github", "url": "u", "name": f"o/r{i}",
             "description": "d", "lang": "Rust",
             "created_at": datetime(2026, 10, 19, tzinfo=UTC), "raw": {}}
            for i in range(n)]


@pytest.fixture
def model(monkeypatch):
    """The new model: item i scores i % 100, and every call is recorded."""
    calls: list[list[str]] = []

    async def score_candidates(items, rubric, *, now, temperature, max_tokens,
                               model_version=None):
        calls.append([i.id for i in items])
        return ([ScoredItem(item_id=i.id, score=int(i.id.rsplit("r", 1)[1]) % 100,
                            dims={}, reason="", verdict="skip",
                            model_version=model_version) for i in items], {})

    async def anchor_time(conn, vertical, day):
        return datetime(2026, 10, 20, tzinfo=UTC)

    monkeypatch.setattr(calibrate.llm, "score_candidates", score_candidates)
    monkeypatch.setattr("intel.backfill.anchor_time", anchor_time)
    return calls


def _store(monkeypatch, **kw) -> FakeStore:
    store = FakeStore(**kw)
    monkeypatch.setattr(calibrate, "db", store)
    return store


async def _ensure(rubric, version, cfg=None):
    return await calibrate.ensure_lines(
        FakeConn(), rubric, version, temperature=0.3, max_tokens=100,
        today=TODAY, cfg={**calibrate.DEFAULTS, **(cfg or {})})


# ---------------------------------------------------------- ensure_lines

@pytest.fixture
def rubric_now():
    from intel.rubrics import load_rubric
    return load_rubric("dev_tools")


@pytest.mark.anyio
async def test_the_first_version_takes_the_rubric_lines(monkeypatch, rubric_now, model):
    store = _store(monkeypatch)
    assert await _ensure(rubric_now, "DeepSeek-V4.1-Flash") == "seeded"
    assert store.lines[-1]["source"] == "rubric" and store.lines[-1]["s_line"] == 69
    assert model == []


@pytest.mark.anyio
async def test_a_known_version_costs_nothing(monkeypatch, rubric_now, model):
    _store(monkeypatch, lines=[_row("V", 69, 58, 44, 29)])
    assert await _ensure(rubric_now, "V") == "known"
    assert model == []


@pytest.mark.anyio
async def test_a_new_version_is_measured_without_touching_published_scores(
        monkeypatch, rubric_now, model):
    store = _store(monkeypatch, cohort=_cohort(300),
                   lines=[_row("Old", 69, 58, 44, 29)])
    assert await _ensure(rubric_now, "New") == "calibrated"
    new = store.lines[-1]
    assert new["model_version"] == "New" and new["source"] == "calibration"
    assert (new["s_line"], new["a_line"], new["b_line"], new["c_line"]) == (98, 90, 70, 40)
    assert len(store.cal["New"]) == 300
    assert tiers.tier(95, version="New") == "A" and tiers.tier(95, version="Old") == "S"


@pytest.mark.anyio
async def test_too_few_items_leave_the_version_waiting(monkeypatch, rubric_now, model):
    store = _store(monkeypatch, cohort=_cohort(50), lines=[_row("Old", 69, 58, 44, 29)])
    assert await _ensure(rubric_now, "New") == "pending"
    assert model == [] and len(store.lines) == 1


@pytest.mark.anyio
async def test_auto_off_leaves_the_version_waiting(monkeypatch, rubric_now, model):
    _store(monkeypatch, cohort=_cohort(300), lines=[_row("Old", 69, 58, 44, 29)])
    assert await _ensure(rubric_now, "New", {"auto": False}) == "pending"
    assert model == []


@pytest.mark.anyio
async def test_a_calibration_never_rescores_more_than_its_ceiling(monkeypatch, rubric_now, model):
    store = _store(monkeypatch, cohort=_cohort(2500), lines=[_row("Old", 69, 58, 44, 29)])
    assert await _ensure(rubric_now, "New") == "calibrated"
    assert sum(len(c) for c in model) == 2000 == len(store.cal["New"])


@pytest.mark.anyio
async def test_an_interrupted_calibration_resumes_without_paying_twice(
        monkeypatch, rubric_now, model):
    store = _store(monkeypatch, cohort=_cohort(300, date(2026, 10, 20))
                   + _cohort(0), lines=[_row("Old", 69, 58, 44, 29)])
    store.cal["New"] = {f"github:o/r{i}": i % 100 for i in range(250)}
    assert await _ensure(rubric_now, "New") == "calibrated"
    assert sum(len(c) for c in model) == 50


@pytest.mark.anyio
async def test_a_failed_calibration_keeps_what_it_scored(monkeypatch, rubric_now):
    store = _store(monkeypatch, cohort=_cohort(150, date(2026, 10, 20))
                   + [{**r, "item_id": f"github:o/x{i}", "snap_date": date(2026, 10, 21)}
                      for i, r in enumerate(_cohort(150))],
                   lines=[_row("Old", 69, 58, 44, 29)])
    seen = []

    async def score_candidates(items, rubric, **kw):
        if seen:
            raise RuntimeError("out of credit")
        seen.append(len(items))
        return [ScoredItem(item_id=i.id, score=50, dims={}, reason="",
                           verdict="skip") for i in items], {}

    async def anchor_time(conn, vertical, day):
        return datetime(2026, 10, 20, tzinfo=UTC)

    monkeypatch.setattr(calibrate.llm, "score_candidates", score_candidates)
    monkeypatch.setattr("intel.backfill.anchor_time", anchor_time)
    assert await _ensure(rubric_now, "New") == "pending"
    assert len(store.cal["New"]) == 150 and len(store.lines) == 1


# ----------------------------------------------------------------- drift

@pytest.mark.anyio
async def test_drift_is_quiet_inside_the_band(monkeypatch):
    store = _store(monkeypatch)
    store.recent = [60] * 15 + [10] * 185            # 7.5% reach A (58)
    assert await calibrate.drift(FakeConn(), "dev_tools", "V", today=TODAY,
                                 cfg=calibrate.DEFAULTS) is None


@pytest.mark.anyio
async def test_drift_speaks_when_a_week_runs_far_above_the_band(monkeypatch):
    store = _store(monkeypatch)
    store.recent = [60] * 80 + [10] * 120            # 40% reach A
    warning = await calibrate.drift(FakeConn(), "dev_tools", "V", today=TODAY,
                                    cfg=calibrate.DEFAULTS)
    assert warning and "40.0%" in warning and "--force" in warning


@pytest.mark.anyio
async def test_drift_says_nothing_on_too_few_scores(monkeypatch):
    store = _store(monkeypatch)
    store.recent = [90] * 20
    assert await calibrate.drift(FakeConn(), "dev_tools", "V", today=TODAY,
                                 cfg=calibrate.DEFAULTS) is None
