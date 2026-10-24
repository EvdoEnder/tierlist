"""Edition contexts, offline: label math, aggregation of fabricated score
rows, and the pinned context contract itself."""
from __future__ import annotations

from datetime import date

import pytest
from conftest import item

from intel import editions
from intel.editions import (
    archive_cells,
    best_per_item,
    build_aggregate_context,
    daily_context,
    edition_label,
    iso_week_key,
    month_key,
    period_label,
    recent_issues,
    week_span,
    window_days,
)
from intel.pipeline import RunResult
from intel.score.llm import ScoredItem


def _row(item_id, score, name=None, tags=None, verdict="watch"):
    return {"item_id": item_id, "score": score, "dims": {"novelty": 20},
            "reason": f"why {item_id}", "verdict": verdict,
            "name": name or item_id, "url": f"https://github.com/{item_id}",
            "lang": "Rust", "description": "一段描述",
            "stars": 100, "tags": tags or []}


SUNDAY = date(2026, 8, 23)   # ISO 2026-W34


def test_window_days_per_edition():
    assert (window_days("daily"), window_days("weekly"),
            window_days("monthly")) == (1, 7, 30)


def test_period_start_spans_exactly_the_window():
    assert editions.period_start("weekly", SUNDAY) == date(2026, 8, 17)
    assert editions.period_start("monthly", SUNDAY) == date(2026, 7, 25)
    assert editions.period_start("daily", SUNDAY) == SUNDAY


def test_week_span_is_monday_to_sunday():
    start, end = week_span(SUNDAY)
    assert (start, end) == (date(2026, 8, 17), date(2026, 8, 23))
    # mid-week dates land in the same week
    assert week_span(date(2026, 8, 19)) == (start, end)


def test_iso_week_and_month_keys():
    assert iso_week_key(SUNDAY) == "2026-W34"
    assert month_key(SUNDAY) == "2026-08"


def test_period_label_matches_design_example():
    assert period_label("weekly", SUNDAY) == "2026-W34 · 08-17 → 08-23"
    assert period_label("monthly", SUNDAY) == "2026-08"
    assert period_label("daily", SUNDAY) == "2026-08-23"


def test_edition_label_swaps_the_masthead_suffix():
    assert edition_label("dev_tools", "daily") == "开发者工具晨报"
    assert edition_label("dev_tools", "weekly") == "开发者工具周报"
    assert edition_label("dev_tools", "monthly") == "开发者工具月报"


def test_best_per_item_keeps_the_top_score_only():
    rows = [_row("a/b", 61), _row("a/b", 88), _row("c/d", 70)]
    best = best_per_item(rows)
    assert [(r["item_id"], r["score"]) for r in best] == [
        ("a/b", 88), ("c/d", 70)]


def test_best_per_item_respects_limit():
    rows = [_row(f"x/{i}", i) for i in range(20)]
    assert len(best_per_item(rows, limit=10)) == 10
    assert best_per_item(rows)[0]["score"] == 19


def test_recent_issues_slices_each_section_newest_last():
    dates = [date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 23)]
    recent = recent_issues(dates, per_section=2)
    assert recent["daily"][-1] == {"date": "2026-08-23",
                                   "url": "/zh/daily/2026-08-23.html"}
    assert recent["weekly"] == [{"date": "2026-07-27",
                                 "url": "/zh/weekly/2026-W31.html"},
                                {"date": "2026-08-17",
                                 "url": "/zh/weekly/2026-W34.html"}]
    assert recent["monthly"] == [{"date": "2026-08-01",
                                  "url": "/zh/monthly/2026-08.html"}]


def test_archive_cells_cover_gap_months():
    cells = archive_cells([date(2026, 5, 10), date(2026, 8, 23)],
                          today=date(2026, 8, 24))
    assert list(cells["daily"]) == ["2026-05", "2026-06", "2026-07",
                                    "2026-08"]
    assert cells["daily"]["2026-06"] == []          # gap month renders gray
    assert cells["daily"]["2026-05"][0]["url"] == "/zh/daily/2026-05-10.html"
    assert cells["weekly"]["2026-08"][0]["url"] == "/zh/weekly/2026-W34.html"
    assert cells["monthly"]["2026-05"] == [{"date": "2026-05-01",
                                            "url": "/zh/monthly/2026-05.html"}]


PINNED_KEYS = {"edition", "edition_label", "date", "period_label", "nav",
               "lede", "entries", "hn_entries", "hn_min_score", "gain_board",
               "hot_new", "trending", "archive_recent", "lang", "p",
               "news_rows"}
LEDE_KEYS = {"window", "window_from", "window_to", "fetched", "candidates",
             "sent", "scored", "model", "cost", "new_count", "watch_min", "top_score",
             "top_tier"}
ENTRY_KEYS = {"id", "rank", "name", "url", "stars", "lang", "score", "tier", "dims",
              "description", "reason", "tags", "is_watch", "high_score",
              "source", "badge", "metric"}


def _fake_daily_result():
    top = [ScoredItem(
        item_id="github:a/b", score=76,
        dims={"novelty": 18, "utility": 20, "momentum": 21, "commercial": 17},
        reason="实质判断", verdict="watch", tags=["mcp", "逆向"])]
    return RunResult(
        vertical="dev_tools", since_date="2026-08-22",
        ran_at="2026-08-23T12:00:00+00:00",
        fetched=120, candidates=40, scored=38, watch_min=60, stored_new=12,
        run_mode="pg", hot_new=[item("github:hot/one", stars=473)],
        candidate_items=[item("github:a/b")], top=top,
        usage={"prompt_tokens": 1000, "completion_tokens": 500})


def _board(ready=True):
    return {"label": "star 增速榜 · 日", "window_days": 1, "ready": ready,
            "need": 2, "have": 9 if ready else 1,
            "rows": [{"rank": "01", "name": "a/b", "url": "u", "lang": "Rust",
                      "stars_now": 90, "gain": 30}] if ready else None}


ARCHIVE = {"daily": [{"date": "2026-08-23", "url": "/zh/daily/2026-08-23.html"}],
           "weekly": [], "monthly": []}


def test_daily_context_emits_the_pinned_contract():
    ctx = daily_context(_fake_daily_result(), stats={"new_count": 12},
                        gain_board=_board(), archive_recent=ARCHIVE)
    assert set(ctx) == PINNED_KEYS
    assert set(ctx["lede"]) == LEDE_KEYS
    # created:>08-22 on an 08-23 run: the page prints the inclusive range.
    assert (ctx["lede"]["window_from"], ctx["lede"]["window_to"]) == (
        "2026-08-23", "2026-08-23")
    for entry in ctx["entries"]:
        assert set(entry) == ENTRY_KEYS
    assert ctx["edition"] == "daily"
    assert ctx["lang"] == "zh" and ctx["p"] == "/zh"
    assert ctx["nav"]["daily"] == "/zh/daily/"
    assert ctx["lede"]["fetched"] == 120 and ctx["lede"]["new_count"] == 12
    assert ctx["lede"]["sent"] == 12    # 送审 shows what actually reached the LLM
    assert ctx["entries"][0]["tags"] == ["mcp", "逆向"]
    assert ctx["hot_new"][0]["description"]  # full text, not truncated


def test_daily_context_en_prefixed_urls_and_label():
    ctx = daily_context(_fake_daily_result(), stats={"new_count": 12},
                        gain_board=_board(), archive_recent=ARCHIVE,
                        lang="en")
    assert ctx["lang"] == "en" and ctx["p"] == ""
    assert ctx["nav"]["daily"] == "/daily/"
    assert ctx["edition_label"] == "Dev Tools Daily"
    # The language switch is NOT in the context any more: it depends on the
    # page's own path, which the context builder does not know, so it is
    # derived at render time (see test_render_site). A stray copy here is
    # exactly how it came to be computed in six places.
    assert "switch_url" not in ctx
    # Chinese chrome must not leak into the English page
    assert "晨报" not in ctx["edition_label"]


def test_edition_labels_pass_the_language_through():
    assert editions.edition_label("dev_tools", "weekly", "en") == \
        "Dev Tools Weekly"
    assert editions.edition_label("dev_tools", "monthly", "en") == \
        "Dev Tools Monthly"
    assert editions.edition_label("dev_tools", "daily", "zh") == \
        "开发者工具晨报"
    assert editions.edition_label("dev_tools", "weekly", "zh") == \
        "开发者工具周报"


def test_aggregate_context_lede_degrades_to_none():
    ctx = build_aggregate_context(
        "weekly", "dev_tools", SUNDAY,
        rows=[_row("a/b", 80), _row("a/b", 90), _row("c/d", 55)],
        stats={"new_count": 33, "scored_count": 21}, gain_board=_board(),
        archive_recent=ARCHIVE)
    lede = ctx["lede"]
    assert (lede["fetched"], lede["candidates"], lede["sent"], lede["model"],
            lede["cost"]) == (None, None, None, None, None)
    assert lede["new_count"] == 33 and lede["scored"] == 21
    assert lede["window"] == "2026-08-17"          # 7 days ending on run date
    assert (lede["window_from"], lede["window_to"]) == ("2026-08-17", "2026-08-23")
    assert ctx["hot_new"] is None
    assert [(e["rank"], e["score"]) for e in ctx["entries"]] == [
        ("01", 90), ("02", 55)]


def test_daily_context_without_stats_falls_back_to_stored_new():
    result = _fake_daily_result()
    ctx = daily_context(result, stats=None, gain_board=_board(),
                        archive_recent=ARCHIVE)
    assert ctx["lede"]["new_count"] == result.stored_new


# ------------------------------------------- run row: lede + the hot board

_RUN_ROW = {"fetched": 357, "candidates": 41, "scored": 30,
            "model": "deepseek-v4",
            "usage": {"prompt_tokens": 100000, "completion_tokens": 5000},
            "hot_new": [{"rank": "01", "name": "a/b", "url": "u",
                         "stars": 900, "description": "x"}]}


def test_lede_recovers_the_sweeps_counters():
    """A rebuilt daily page prints the provenance the sweep recorded."""
    lede = editions.lede_from_run(_RUN_ROW)
    assert lede["fetched"] == 357
    assert lede["candidates"] == 41
    assert lede["model"] == "deepseek-v4"
    assert lede["cost"].startswith("$")


def test_lede_never_blanks_a_value_the_context_already_has():
    """The run row is sparse for older sweeps. A None in it must not
    overwrite `scored`, which build_aggregate_context derives from
    edition_stats — the merge drops missing keys rather than writing None."""
    assert editions.lede_from_run(
        {"fetched": None, "candidates": None, "scored": None,
         "model": None, "usage": None}) == {}


def test_lede_recovers_the_sweeps_fetch_window():
    """A rebuilt daily page prints the created-range the live sweep printed,
    not the single scored day."""
    from datetime import UTC, datetime
    row = {**_RUN_ROW,
           "started_at": datetime(2026, 9, 17, 0, 0, 5, tzinfo=UTC)}
    lede = editions.lede_from_run(row, lookback_hours=72)
    assert (lede["window_from"], lede["window_to"]) == (
        "2026-09-15", "2026-09-17")
    assert "window_from" not in editions.lede_from_run(row)


def test_lede_is_empty_when_no_run_that_day():
    assert editions.lede_from_run(None) == {}


def test_hot_board_comes_back_off_the_run_row():
    board = editions.hot_new_from_run(_RUN_ROW)
    assert board == _RUN_ROW["hot_new"]


def test_hot_board_is_none_for_a_run_that_predates_the_column():
    """None, not [] — the board is unrecoverable for those runs, and the
    template hides both, so only None reports which case it is."""
    assert editions.hot_new_from_run({"fetched": 1, "hot_new": None}) is None
    assert editions.hot_new_from_run({"fetched": 1}) is None
    assert editions.hot_new_from_run({"fetched": 1, "hot_new": []}) is None
    assert editions.hot_new_from_run(None) is None


def test_hot_new_rows_number_the_board():
    """The rank cell rendered empty on every page the site ever served: the
    site's copy of this builder omitted `rank` while the template printed
    it. One builder now, so the two cannot drift again."""
    class _I:
        def __init__(self, n):
            self.name, self.url, self.stars, self.description = n, "u", 9, ""

    rows = editions.hot_new_rows([_I("a/b"), _I("c/d")])
    assert [r["rank"] for r in rows] == ["01", "02"]
    assert rows[0]["name"] == "a/b" and rows[0]["stars"] == 9


def test_hot_new_rows_survive_a_missing_description():
    class _I:
        name, url, stars, description = "a/b", "u", 3, None

    assert editions.hot_new_rows([_I()])[0]["description"] == ""


@pytest.mark.anyio
async def test_daily_context_prefers_the_stored_board(monkeypatch,
                                                      anyio_backend):
    """What was published wins over what can be recomputed."""
    ctx = await _daily_ctx(monkeypatch, run_row=_RUN_ROW, derived=[{"rank":
                           "01", "name": "derived/x", "url": "u", "stars": 1,
                           "description": ""}])
    assert [r["name"] for r in ctx["hot_new"]] == ["a/b"]


@pytest.mark.anyio
async def test_daily_context_reconstructs_a_board_the_run_never_stored(
        monkeypatch, anyio_backend):
    """Every run before 2026-08-27 has hot_new NULL. Those pages showed a
    blank space where the board belongs — the landing page included, on every
    rebuild the 15-minute news cron did."""
    derived = [{"rank": "01", "name": "derived/x", "url": "u", "stars": 1,
                "description": ""}]
    row = {k: v for k, v in _RUN_ROW.items() if k != "hot_new"}
    ctx = await _daily_ctx(monkeypatch, run_row=row, derived=derived)
    assert ctx["hot_new"] == derived


@pytest.mark.anyio
async def test_daily_context_board_is_none_when_neither_source_has_one(
        monkeypatch, anyio_backend):
    """None, not [] — the template hides the board either way, but a page
    context that says [] claims the board was empty."""
    ctx = await _daily_ctx(monkeypatch, run_row=None, derived=[])
    assert ctx["hot_new"] is None


async def _daily_ctx(monkeypatch, *, run_row, derived):
    """daily_db_context with every store read stubbed but the two this
    assertion is about."""
    async def _rows(*a, **k):
        return []

    async def _stats(*a, **k):
        return {"new_count": 0, "scored_count": 0}

    async def _gain(*a, **k):
        return {"ready": False, "need": 8, "have": 1, "rows": []}

    async def _dates(*a, **k):
        return []

    async def _run(*a, **k):
        return run_row

    async def _hot(*a, **k):
        return list(derived)

    monkeypatch.setattr(editions.db, "window_score_rows", _rows)
    monkeypatch.setattr(editions.db, "edition_stats", _stats)
    monkeypatch.setattr(editions.gain, "gain_rows", _gain)
    monkeypatch.setattr(editions.db, "issue_dates", _dates)
    monkeypatch.setattr(editions.db, "run_stats_for_date", _run)
    monkeypatch.setattr(editions.db, "hot_new_for_date", _hot)
    return await editions.daily_db_context(None, "dev_tools",
                                           run_date=date(2026, 8, 24))


# ------------------------------------------------------- the issue pager

_PAGER_DATES = [date(2026, 8, 21),   # 2026-W34
                date(2026, 8, 23),   # 2026-W34
                date(2026, 8, 25)]   # 2026-W35


def test_issue_slugs_collapse_dates_onto_their_period():
    assert editions.issue_slugs("daily", _PAGER_DATES) == [
        "2026-08-21", "2026-08-23", "2026-08-25"]
    assert editions.issue_slugs("weekly", _PAGER_DATES) ==         ["2026-W34", "2026-W35"]
    assert editions.issue_slugs("monthly", _PAGER_DATES) == ["2026-08"]


def test_pager_skips_the_days_that_were_never_published():
    """08-22 and 08-24 have no page. Stepping one calendar day back off
    08-23 would link to a 404 the site invented for itself."""
    slugs = editions.issue_slugs("daily", _PAGER_DATES)
    nb = editions.issue_neighbours("daily", "2026-08-23", slugs)
    assert nb["prev"]["url"] == "/zh/daily/2026-08-21.html"
    assert nb["next"]["url"] == "/zh/daily/2026-08-25.html"
    assert nb["prev"]["label"] == "2026-08-21"


def test_pager_ends_the_chain_at_both_extremes():
    slugs = editions.issue_slugs("daily", _PAGER_DATES)
    assert editions.issue_neighbours("daily", "2026-08-21", slugs)["prev"]         is None
    assert editions.issue_neighbours("daily", "2026-08-25", slugs)["next"]         is None


def test_pager_of_a_lone_issue_points_nowhere_either_way():
    nb = editions.issue_neighbours("monthly", "2026-08", ["2026-08"])
    assert nb == {"prev": None, "next": None}


def test_pager_places_an_issue_the_store_has_not_shown_yet():
    """The sweep renders its own issue in the pass that wrote its scores, so
    the fresh date may still be missing from issue_dates. It belongs at the
    end of the chain, not off it — otherwise today's page is the one page
    with no way back."""
    slugs = editions.issue_slugs("daily", _PAGER_DATES)
    nb = editions.issue_neighbours("daily", "2026-08-26", slugs)
    assert nb["prev"]["url"] == "/zh/daily/2026-08-25.html"
    assert nb["next"] is None
    # Placed in SORT order, not appended: a page rebuilt for a day the store
    # no longer lists still points at the days either side of it, and never
    # forward at a date older than itself.
    mid = editions.issue_neighbours("daily", "2026-08-22", slugs)
    assert mid["prev"]["url"] == "/zh/daily/2026-08-21.html"
    assert mid["next"]["url"] == "/zh/daily/2026-08-23.html"


def test_pager_urls_carry_the_language_prefix():
    nb = editions.issue_neighbours("weekly", "2026-W35",
                                   ["2026-W34", "2026-W35"], "en")
    assert nb["prev"]["url"] == "/weekly/2026-W34.html"
    nb = editions.issue_neighbours("weekly", "2026-W35",
                                   ["2026-W34", "2026-W35"], "zh")
    assert nb["prev"]["url"] == "/zh/weekly/2026-W34.html"


def test_pager_walks_weeks_across_a_year_boundary():
    """Slugs are ordered lexicographically, which is only chronological
    because the week number is zero-padded: 2026-W09 → 2026-W10 → 2027-W01."""
    slugs = ["2026-W09", "2026-W10", "2027-W01"]
    across = editions.issue_neighbours("weekly", "2026-W10", slugs)
    assert across["next"]["url"] == "/zh/weekly/2027-W01.html"
    back = editions.issue_neighbours("weekly", "2027-W01", slugs)
    assert back["prev"]["url"] == "/zh/weekly/2026-W10.html"


def test_the_lede_names_the_best_tier_not_the_first_score():
    """A day that mixes rubrics can put a lower tier ahead on score: an
    old-rubric 64 is B where a current-rubric 64 is A."""
    from intel.editions import _best

    entries = [{"score": 64, "tier": "B"}, {"score": 64, "tier": "A"},
               {"score": 63, "tier": "A"}]
    assert _best(entries) == {"top_score": 64, "top_tier": "A"}
    assert _best([]) == {"top_score": None, "top_tier": None}


def test_tier_history_groups_by_tier_and_month_in_the_pages_language():
    from intel.editions import tier_history

    def row(i, day, score):
        return {"item_id": i, "snap_date": date.fromisoformat(day),
                "score": score, "dims": None, "model_version": None,
                "reason": "中文理由", "reason_en": "English reason",
                "verdict": None, "name": f"o/{i}", "url": f"u/{i}",
                "source": "github", "lang": None, "stars": 3,
                "description": "d", "description_en": None, "tags": ["ai"]}

    rows = [row("a", "2026-10-02", 99), row("b", "2026-09-30", 99),
            row("c", "2026-10-01", 1)]
    en = tier_history(rows, "en")
    assert list(en["S"]) == ["2026-10", "2026-09"]
    assert [e["name"] for e in en["S"]["2026-10"]] == ["o/a"]
    assert en["S"]["2026-10"][0]["reason"] == "English reason"
    assert en["S"]["2026-10"][0]["day_url"] == "/daily/2026-10-02.html"
    assert en["D"]["2026-10"][0]["name"] == "o/c"
    zh = tier_history(rows, "zh")
    assert zh["S"]["2026-09"][0]["reason"] == "中文理由"
    assert zh["S"]["2026-09"][0]["day_url"] == "/zh/daily/2026-09-30.html"
