"""Editions: one context builder per site section — daily / weekly / monthly.

Daily consumes a fresh RunResult (fetch+score happened upstream). Weekly and
monthly are DB-only aggregates: best score per item over the trailing window,
no fetch, no LLM. Every builder emits exactly the pinned context contract the
render layer (`intel.render.site`) consumes.

Each context carries `lang` ("zh" | "en") and `p` — the path prefix baked
into every internal URL it produces ("" for English at the site root, "/zh"
for Chinese). Chrome text and the
language switch live in the template; URLs and labels live here, because
they are data-shaped.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from intel import sections
from intel.deliver.context import (
    _DESC_MAX_CHARS,
    _HIGH_SCORE_MIN,
    _one_line,
    _truncate,
    cost_str,
    llm_model_label,
    rank_rows,
    source_badge,
    source_metric,
    vertical_label,
)
from intel.store import db, gain
from intel.tiers import rank, tier

WINDOW_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}

EDITION_SUFFIXES = {"daily": "晨报", "weekly": "周报", "monthly": "月报"}
EDITION_SUFFIXES_EN = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly"}

NAV = {"daily": "/daily/", "weekly": "/weekly/", "monthly": "/monthly/",
       "news": "/news/", "archive": "/archive/", "tags": "/tags/",
       "search": "/search/", "docs": "/docs/"}


def window_days(edition: str) -> int:
    return WINDOW_DAYS[edition]


def period_start(edition: str, run_date: date) -> date:
    """First day of the edition's window ending at `run_date`, inclusive.

    A 7-day weekly window is run_date minus 6: subtracting the full 7 while
    keeping both ends closed read eight days of scores (and a monthly
    thirty-one)."""
    return run_date - timedelta(days=WINDOW_DAYS[edition] - 1)


def week_span(d: date) -> tuple[date, date]:
    """The ISO week containing d — Monday through Sunday."""
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


def iso_week_key(d: date) -> str:
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def period_label(edition: str, run_date: date) -> str:
    if edition == "weekly":
        start, end = week_span(run_date)
        return f"{iso_week_key(run_date)} · {start:%m-%d} → {end:%m-%d}"
    if edition == "monthly":
        return month_key(run_date)
    return run_date.isoformat()


def edition_label(vertical: str, edition: str, lang: str = "zh") -> str:
    base = vertical_label(vertical, lang)
    if lang == "en":
        # vertical_label already ends in "Daily" for dev_tools; the other
        # editions swap the word, never append a second one.
        suffix = EDITION_SUFFIXES_EN[edition]
        return base.rsplit(" ", 1)[0] + f" {suffix}" if base.endswith("Daily") \
            else f"{base} · {suffix}"
    suffix = EDITION_SUFFIXES[edition]
    return base.replace(EDITION_SUFFIXES["daily"], suffix) \
        if base.endswith(EDITION_SUFFIXES["daily"]) else f"{base}·{suffix}"


# ---------------------------------------------------------------- aggregations

def best_per_item(rows: list, limit: int | None = 10) -> list[dict]:
    """db.window_score_rows output → best row per item, score-desc, top limit
    PER BOARD (intel.sections), repositories first.

    Per board rather than overall: the two are ranked against different
    kinds, so one list capped at ten hands its slots to whichever board had
    the better day. The caller splits the result again by source; the cap is
    applied here because this is where the ordering is known.

    Rows are read structurally (`row["col"]`), so psycopg Row objects and
    plain test dicts both fit. `limit=None` keeps every item: a daily issue
    lists everything the day rated.
    """
    best: dict[str, dict] = {}
    for r in rows:
        item_id = r["item_id"]
        cur = best.get(item_id)
        if cur is None or r["score"] > cur["score"]:
            best[item_id] = r
    ranked = sorted(best.values(),
                    key=lambda r: (-r["score"], r["item_id"]))
    boards = sections.split(ranked, limit=limit)
    return [r for name in sections.ORDER for r in boards[name]]


def recent_issues(dates: list[date], per_section: int = 6,
                  lang: str = "zh") -> dict:
    """Latest issues per section as {section: [{date, url}]}, newest last —
    the landing page's 最近归档条. URLs carry the language prefix."""
    p = lang_prefix(lang)
    ordered = sorted(set(dates))
    daily = [{"date": d.isoformat(), "url": f"{p}/daily/{d.isoformat()}.html"}
             for d in ordered[-per_section:]]
    weeks: dict[str, date] = {}
    months: dict[str, None] = {}
    for d in ordered:
        weeks.setdefault(iso_week_key(d), week_span(d)[0])
        months.setdefault(month_key(d))
    weekly = [{"date": monday.isoformat(),
               "url": f"{p}/weekly/{key}.html"}
              for key, monday in list(weeks.items())[-per_section:]]
    monthly = [{"date": f"{key}-01", "url": f"{p}/monthly/{key}.html"}
               for key in list(months)[-per_section:]]
    return {"daily": daily, "weekly": weekly, "monthly": monthly}


# Re-exported from i18n, which owns the language-tree layout.
from intel.i18n import lang_prefix, reason_in  # noqa: E402


def _iter_months(first: date, until: date):
    y, m = first.year, first.month
    while (y, m) <= (until.year, until.month):
        yield f"{y:04d}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def archive_cells(dates: list[date], today: date, lang: str = "zh") -> dict:
    """section → month → calendar cells ({date, url}), covering EVERY month
    from the first issue to the current one — gap months get empty lists and
    render as all-gray calendars. URLs carry the language prefix."""
    p = lang_prefix(lang)
    ordered = sorted(set(dates)) or [today]
    span = list(_iter_months(ordered[0], today))
    cells = {section: {m: [] for m in span}
             for section in ("daily", "weekly", "monthly")}
    seen_weeks: set[str] = set()
    for d in ordered:
        cells["daily"][month_key(d)].append(
            {"date": d.isoformat(),
             "url": f"{p}/daily/{d.isoformat()}.html"})
        wk = iso_week_key(d)
        if wk not in seen_weeks:
            seen_weeks.add(wk)
            cells["weekly"][month_key(d)].append(
                {"date": week_span(d)[0].isoformat(),
                 "url": f"{p}/weekly/{wk}.html"})
        mk = month_key(d)
        if not any(c["url"] == f"{p}/monthly/{mk}.html"
                   for c in cells["monthly"][mk]):
            cells["monthly"][mk].append(
                {"date": f"{mk}-01", "url": f"{p}/monthly/{mk}.html"})
    return cells


def issue_slugs(edition: str, dates: list) -> list[str]:
    """Every slug a section HAS a page for, oldest first.

    `dates` is store.issue_dates output — the dates that carry a score, which
    is the same ground truth /archive/ paints its calendars from. Weekly and
    monthly collapse those dates onto their period key, so a week with four
    scored days is one slug, not four.
    """
    ordered = sorted(set(dates))
    if edition == "weekly":
        return list(dict.fromkeys(iso_week_key(d) for d in ordered))
    if edition == "monthly":
        return list(dict.fromkeys(month_key(d) for d in ordered))
    return [d.isoformat() for d in ordered]


def issue_neighbours(edition: str, slug: str, slugs: list[str],
                     lang: str = "zh") -> dict:
    """The previous/next issue of one section as
    {"prev": {"url", "label"} | None, "next": ...} — the issue pager.

    Neighbours come from the slugs that EXIST, never from date arithmetic:
    the sweep skips days, and stepping one calendar day back off 2026-08-24
    publishes a link to a page that was never written. This is the same rule
    `deliver.site.archive_neighbour` follows for the month calendars, for the
    same reason.

    `slug` need not be in `slugs`: a sweep renders its own issue in the same
    pass that wrote its scores, and whether the new date is already visible
    to `issue_dates` is a matter of transaction timing. It is placed in sort
    order instead, so the freshest page still links back to the one before
    it. All three slug shapes (ISO date, `YYYY-Www`, `YYYY-MM`) sort
    lexicographically in chronological order, which is what makes that
    placement correct without parsing them.
    """
    ordered = sorted(set(slugs) | {slug})
    i = ordered.index(slug)
    p = lang_prefix(lang)

    def _at(j: int) -> dict | None:
        if not 0 <= j < len(ordered):
            return None
        return {"url": f"{p}/{edition}/{ordered[j]}.html",
                "label": ordered[j]}

    return {"prev": _at(i - 1), "next": _at(i + 1)}


def hot_new_rows(items) -> list[dict]:
    """`RunResult.hot_new` (Item objects) → the board's display rows.

    One builder, because these rows are written twice: into the page the
    sweep renders, and into intel.runs so every later rebuild renders the
    same board. Two hand-written copies had already drifted — the site copy
    omitted `rank`, and the template's `<span class="r">{{ h.rank }}</span>`
    printed an empty cell on every page the site has ever served.
    """
    return [{"rank": f"{rank:02d}", "name": i.name, "url": i.url,
             "stars": i.stars, "description": i.description or ""}
            for rank, i in enumerate(items, 1)]


TRENDING_PERIODS = ("daily", "weekly", "monthly")
TRENDING_TOP_N = 10


def trending_rows(items, tags_by_id: dict[str, list[str]]) -> dict:
    """GitHub Trending Items -> {period: display rows}, in GitHub's own order.

    Written into the page the sweep renders and into intel.runs.trending,
    so a rebuilt issue shows the boards as GitHub ranked them that day.
    A period the fetch did not return is absent rather than empty."""
    boards: dict[str, list[dict]] = {}
    for period in TRENDING_PERIODS:
        ranked = sorted(
            (i for i in items if period in (i.raw.get("period_rank") or {})),
            key=lambda i: i.raw["period_rank"][period])[:TRENDING_TOP_N]
        if ranked:
            boards[period] = [
                {"rank": f"{n:02d}", "name": i.name, "url": i.url,
                 "stars": i.stars,
                 "gain": (i.raw.get("period_stars") or {}).get(period) or 0,
                 "tags": tags_by_id.get(i.id, [])}
                for n, i in enumerate(ranked, 1)]
    return boards


def wrap_gain(gain_result: dict, edition: str) -> dict:
    """store.gain_rows output → the pinned gain_board block (ranked rows only
    when ready)."""
    ready = bool(gain_result["ready"])
    raw = gain_result.get("rows") or []
    return {
        "window_days": WINDOW_DAYS[edition],
        "ready": ready,
        "need": gain_result["need"],
        "have": gain_result["have"],
        "rows": [{"rank": f"{i:02d}", **row}
                 for i, row in enumerate(raw, 1)] if ready else None,
    }


# ------------------------------------------------------------------ contexts

# Threaded through every context: the pages chrome reads (nav labels live in
# the template via t(); the prefix is needed for asset hrefs). The language
# switch is NOT here — it is derived from the page's path in
# render._page_vars, so no context builder can get it wrong.
_EXTRA_META = ("lang", "p")


def _base_ctx(edition: str, vertical: str, run_date: date, *,
              lang: str = "zh") -> dict:
    p = lang_prefix(lang)
    return {
        "edition": edition,
        "edition_label": edition_label(vertical, edition, lang),
        "date": run_date.isoformat(),
        "period_label": period_label(edition, run_date),
        "nav": {k: f"{p}{v}" for k, v in NAV.items()},
        "lang": lang,
        "p": p,
    }


def _entry(*, name, url, stars, lang, score, dims, description,
           reason, tags, verdict, source="github", item_id=None,
           model_version=None) -> dict:
    """One entry row, unranked — `_boards` numbers it once it knows which
    board it is on. `id` is the store's item id, which the JSON API keys
    item documents by; the page does not print it."""
    return {
        "id": item_id,
        "name": name,
        "url": url,
        "stars": stars,
        "source": source or "github",
        "badge": source_badge(source),
        "metric": source_metric(source, stars),
        # Only a repository has a language. An HN post left to the "语言未知"
        # fallback would claim a missing attribute rather than an absent one.
        "lang": (lang or "语言未知") if (source or "github") == "github"
                else lang,
        "score": score,
        "tier": tier(score, dims=dims, version=model_version),
        "dims": dict(dims or {}),
        "description": _truncate(_one_line(description or ""), _DESC_MAX_CHARS),
        "reason": reason or "",
        "tags": list(tags or []),
        "is_watch": verdict == "watch",
        "high_score": score >= _HIGH_SCORE_MIN,
    }


def _best(entries: list[dict]) -> dict:
    """The lede's best entry. The highest tier, not the highest score: a
    score is read against the lines of the rubric and model version that
    gave it, so on a day that mixes versions the top score can sit in a
    lower tier than a smaller score beside it. 2026-10-24 held an
    old-rubric 64 (B) at the head of three new-rubric 64s (A), and the page
    said nothing had reached A."""
    if not entries:
        return {"top_score": None, "top_tier": None}
    best = max(entries, key=lambda e: (rank(e["tier"]), e["score"]))
    return {"top_score": best["score"], "top_tier": best["tier"]}


def _boards(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Entry rows → (精选 board, Show HN board), each numbered from 01.

    The one place a context turns a ranked list into the two boards the page
    and the push both render, so neither can end up with a different idea of
    which entries belong where.
    """
    split = sections.split(rows)
    return (rank_rows(split[sections.PICKS]),
            rank_rows(split[sections.HN]))


def daily_context(result, *, stats: dict | None, gain_board: dict,
                  archive_recent: dict, lang: str = "zh",
                  news_rows: list | None = None) -> dict:
    """RunResult (+ store side-reads) → the pinned daily issue context.
    `news_rows` (store.news_recent output) rides on the landing page only —
    the issue page ignores it, so a daily cron that predates the news feed
    keeps rendering unchanged."""
    ctx = _base_ctx("daily", result.vertical,
                    datetime.fromisoformat(result.ran_at).date(), lang=lang)
    items_by_id = {i.id: i for i in result.candidate_items}
    rows = [_entry(name=items_by_id[s.item_id].name,
                   url=items_by_id[s.item_id].url,
                   stars=items_by_id[s.item_id].stars,
                   lang=items_by_id[s.item_id].lang,
                   score=s.score, dims=s.dims,
                   description=items_by_id[s.item_id].description,
                   reason=reason_in(lang, s.reason, s.reason_en),
                   tags=s.tags, verdict=s.verdict,
                   source=items_by_id[s.item_id].source,
                   item_id=s.item_id, model_version=s.model_version)
            for s in (result.rated or result.top)]
    entries, hn_entries = _boards(rows)
    ctx.update({
        "lede": {
            "window": result.since_date,
            # The fetch asks for `created:>since_date`, so the first day the
            # board can hold is the one after it; the page prints that
            # inclusive range rather than the exclusive bound.
            "window_from": (date.fromisoformat(result.since_date)
                            + timedelta(days=1)).isoformat(),
            "window_to": datetime.fromisoformat(result.ran_at).date().isoformat(),
            "fetched": result.fetched,
            "candidates": result.candidates,
            "sent": result.stored_new,
            "scored": result.scored,
            "model": llm_model_label(),
            "cost": cost_str(result.usage),
            "new_count": stats["new_count"] if stats else result.stored_new,
            "watch_min": result.watch_min,
            **_best(entries),
        },
        "entries": entries,
        "hn_entries": hn_entries,
        "hn_min_score": sections.min_score(sections.HN),
        "gain_board": gain_board,
        "hot_new": hot_new_rows(result.hot_new),
        "trending": result.trending or None,
        "archive_recent": archive_recent,
        "news_rows": news_rows,
    })
    return ctx


def build_aggregate_context(edition: str, vertical: str, run_date: date, *,
                            rows: list, stats: dict, gain_board: dict,
                            archive_recent: dict, lang: str = "zh",
                            watch_min: int | None = None) -> dict:
    """Weekly/monthly context from fabricated-or-real score rows — the SQL
    lives elsewhere, this half is pure and offline-testable. `watch_min`
    is the rubric threshold when the caller knows it (daily_db_context →
    annotations like 未达 60 线); weekly/monthly have no ruby → None."""
    ctx = _base_ctx(edition, vertical, run_date, lang=lang)
    rows = [_entry(name=r["name"], url=r["url"], stars=r["stars"],
                   lang=r["lang"], score=r["score"], dims=r["dims"],
                   description=r["description"],
                   reason=reason_in(lang, r["reason"], r.get("reason_en")),
                   tags=r["tags"], verdict=r["verdict"],
                   source=r.get("source", "github"),
                   item_id=r.get("item_id"),
                   model_version=r.get("model_version"))
            for r in best_per_item(
                rows, limit=None if edition == "daily" else 10)]
    entries, hn_entries = _boards(rows)
    window_start = period_start(edition, run_date)
    ctx.update({
        "lede": {
            "window": window_start.isoformat(),
            # Both ends inclusive, matching the rows aggregate_context reads.
            "window_from": window_start.isoformat(),
            "window_to": run_date.isoformat(),
            "fetched": None,
            "candidates": None,
            "sent": None,
            "scored": stats["scored_count"] if stats else None,
            "model": None,
            "cost": None,
            "new_count": stats["new_count"] if stats else None,
            "watch_min": watch_min,
            **_best(entries),
        },
        "entries": entries,
        "hn_entries": hn_entries,
        "hn_min_score": sections.min_score(sections.HN),
        "gain_board": gain_board,
        "hot_new": None,
        "trending": None,
        "archive_recent": archive_recent,
        "news_rows": None,
    })
    return ctx


async def aggregate_context(conn, edition: str, vertical: str, *,
                            now: datetime | None = None,
                            lang: str = "zh") -> dict:
    """The DB-reading wrapper around build_aggregate_context (weekly/monthly
    runs do no fetch and no LLM)."""
    now = now or datetime.now(UTC)
    run_date = now.date()
    days = WINDOW_DAYS[edition]
    start = period_start(edition, run_date)
    rows = await db.window_score_rows(conn, (run_date - start).days,
                                      as_of=run_date)
    stats = await db.edition_stats(conn, start.isoformat(),
                                   run_date.isoformat())
    board = wrap_gain(await gain.gain_rows(conn, days, as_of=run_date),
                      edition)
    archive = recent_issues(await db.issue_dates(conn), lang=lang)
    return build_aggregate_context(
        edition, vertical, run_date,
        rows=rows, stats=stats, gain_board=board, archive_recent=archive,
        lang=lang)


async def daily_db_context(conn, vertical: str, *, run_date: date,
                           lang: str = "zh",
                           news_rows: list | None = None,
                           watch_min: int | None = None,
                           lookback_hours: int | None = None) -> dict:
    """The landing context, rebuilt straight from Postgres — the same page
    both the daily sweep and the 15-minute news cron render, so whichever
    runs last wins with an identical, fully-assembled page instead of two
    pages fighting over the index.html file.

    Uses `run_date`'s score rows only — `window_score_rows(0, as_of=d)`
    compiles to `snap_date = d`, which keeps neighbouring days' picks out of
    a page titled 今日精选 (observed 2026-08-25: a manual rebuild leaked
    yesterday's 78-point entry into today's board). Every window this
    context reads is anchored to `run_date` rather than to the clock, which
    is what makes rebuilding a past issue reproduce that issue instead of
    re-stamping it with today's data."""
    rows = await db.window_score_rows(conn, 0, as_of=run_date)
    stats = await db.edition_stats(conn, run_date.isoformat(),
                                   run_date.isoformat())
    board = wrap_gain(
        await gain.gain_rows(conn, WINDOW_DAYS["daily"], as_of=run_date),
        "daily")
    archive = recent_issues(await db.issue_dates(conn), lang=lang)
    ctx = build_aggregate_context(
        "daily", vertical, run_date,
        rows=rows, stats=stats, gain_board=board, archive_recent=archive,
        lang=lang, watch_min=watch_min)
    ctx["news_rows"] = news_rows
    # ONE read of the run row, two things recovered from it: the lede's
    # provenance and the day's 新仓库热榜.
    run_row = await db.run_stats_for_date(conn, vertical, run_date)
    ctx["lede"].update(lede_from_run(
        run_row, lookback_hours or config_lookback_hours()))
    # Stored board first, reconstruction second. Runs before 2026-08-27 have
    # no stored board, and the fallback is exact for them — the two agreed
    # ten of ten on the one day where a published page still held the
    # original to check against.
    ctx["hot_new"] = (hot_new_from_run(run_row)
                      or await db.hot_new_for_date(conn, run_date) or None)
    ctx["trending"] = (run_row or {}).get("trending") or None
    return ctx


def hot_new_from_run(row) -> list[dict] | None:
    """The stored 新仓库热榜 for a run row, or None when there is none.

    None and not [] on purpose: None means "this run recorded no board", and
    the caller answers it by reconstructing one from the star snapshots
    (db.hot_new_for_date). An empty list would read as "the board was empty",
    which is a different claim and would suppress that fallback."""
    if not row:
        return None
    return list(row.get("hot_new") or []) or None


def config_lookback_hours() -> int | None:
    """config.yaml's fetch lookback, so a daily page rebuilt from the store
    prints the same created-range the sweep printed. Best-effort: config
    missing → None (the lede keeps the scored day)."""
    try:
        from intel.pipeline import load_config

        root = Path(__file__).resolve().parents[1]
        return load_config(root / "config.yaml").lookback_hours
    except Exception:
        return None


def lede_from_run(row, lookback_hours: int | None = None) -> dict:
    """The provenance half of the daily lede, from an intel.runs row.

    build_aggregate_context leaves these None because a weekly/monthly page
    has no sweep behind it; a daily page does, and its counters are on
    record. `sent` stays out — the sweep's stored_new is not persisted, and
    `new_count` (items first seen that day) already carries that number."""
    if row is None:
        return {}
    recovered = {"fetched": row["fetched"],
                 "candidates": row["candidates"],
                 "scored": row["scored"],
                 "model": row["model"],
                 "cost": cost_str(row["usage"]) if row["usage"] else None}
    # The sweep's fetch window, as daily_context printed it live: the query
    # was `created:>since` with since = (start - lookback).date(). Without
    # this a rebuilt page fell back to the one scored day.
    started = row.get("started_at")
    if started is not None and lookback_hours:
        started = started.astimezone(UTC)
        since = (started - timedelta(hours=lookback_hours)).date()
        recovered.update({
            "window": since.isoformat(),
            "window_from": (since + timedelta(days=1)).isoformat(),
            "window_to": started.date().isoformat()})
    # Only what the run actually recorded: a None here would overwrite the
    # value build_aggregate_context already derived (scored comes from
    # edition_stats when the run row is missing it).
    return {k: v for k, v in recovered.items() if v is not None}


TIER_ORDER = ("S", "A", "B", "C", "D")


def tier_history(rows, lang: str) -> dict[str, dict[str, list[dict]]]:
    """Rated items → {tier: {month: [entry, ...]}} for the tier history
    pages, months newest first. Each item appears once, at the tier of its
    latest rating, which is how `db.tier_history_rows` delivers them. An
    entry is the issue-page row plus the day it was rated and that day's
    issue, so the folded-row markup renders it unchanged."""
    p = lang_prefix(lang)
    out: dict[str, dict[str, list[dict]]] = {t: {} for t in TIER_ORDER}
    for r in rows:
        e = _entry(name=r["name"], url=r["url"], stars=r["stars"],
                   lang=r["lang"], score=r["score"], dims=r["dims"],
                   description=r["description"],
                   reason=reason_in(lang, r["reason"], r["reason_en"]),
                   tags=r["tags"], verdict=r["verdict"], source=r["source"],
                   item_id=r["item_id"], model_version=r["model_version"])
        day = r["snap_date"].isoformat()
        # The English line rides along so a page's fingerprint changes when
        # a translation lands; the template looks it up by url itself.
        e.update(day=day, day_url=f"{p}/daily/{day}.html",
                 description_en=r["description_en"] or "")
        if e["tier"] in out:
            out[e["tier"]].setdefault(day[:7], []).append(e)
    return out
