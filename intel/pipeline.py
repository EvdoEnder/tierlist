"""One radar sweep of a single vertical: fetch → prefilter → score → rank.

Pure orchestration — each stage is imported and called in order; the only
logic here is what the stages share (the clock, the window) and the summary
counts the report prints. With TIERLIST_PG_DSN set, the sweep also persists:
every fetched item is upserted, only never-scored candidates reach the paid
stage, and scores/rollup/run-audit land in Postgres. Without it the M1
in-memory behavior is unchanged.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import yaml

from intel import calibrate, resnapshot, sections
from intel.rubrics import Rubric
from intel.score import llm, prefilter
from intel.sources import collect, github, github_readme
from intel.sources.base import Item
from intel.sources.collect import SourceConfig
from intel.store import db, trend


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    lookback_hours: int
    max_candidates: int
    temperature: float
    max_tokens: int
    top_n: int
    # config.sources block: which adapters run, and the admission rules each
    # one is judged by. Per-source rather than global because a threshold is
    # a statement in one platform's units - "five stars a day" means nothing
    # to a Hacker News post.
    sources: tuple = ()
    news: dict = None  # config.news block, opaque to the sweep (run_news reads it)
    # config.snapshots block: how much of the store the daily star refresh
    # follows. Defaults hold when the block is absent, so an older
    # config.yaml still loads.
    snapshot_track_days: int = 45
    snapshot_max_items: int = 20000
    snapshot_batch_size: int = 100


def parse_sources(raw: dict) -> tuple[SourceConfig, ...]:
    """config.yaml's `sources` block, in file order.

    Order is kept because it is the order the fetches are reported in and
    the order the prefilter's round-robin visits; a dict literal in YAML
    preserves it, and nothing here should re-sort what the file says.
    """
    return tuple(
        SourceConfig(name=name,
                     enabled=bool((options or {}).get("enabled", False)),
                     options=dict(options or {}))
        for name, options in (raw.get("sources") or {}).items()
    )


def source_rules(cfg: PipelineConfig) -> dict:
    """Enabled sources → the prefilter rules each is judged by.

    A source with no thresholds of its own admits everything it fetched, up
    to the shared cap; that is a decision the config file makes explicitly,
    not an accident of a missing key.
    """
    rules = {}
    for source in cfg.sources:
        if not source.enabled:
            continue
        options = source.options
        rules[source.name] = prefilter.PrefilterRules(
            max_candidates=cfg.max_candidates,
            min_description_chars=int(
                options.get("min_description_chars", 0)),
            min_star_velocity_per_day=float(
                options.get("min_star_velocity_per_day", 0.0)),
            min_points_per_hour=float(options.get("min_points_per_hour", 0.0)),
            min_points=int(options.get("min_points", 0)),
            drop_forks=bool(options.get("drop_forks",
                                        source.name == "github")),
            burst_min_cluster=int(options.get("star_farm_cluster", 0)),
            burst_window_minutes=int(
                options.get("star_farm_window_minutes", 60)),
        )
    return rules


def load_config(path: Path) -> PipelineConfig:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    snaps = dict(raw.get("snapshots") or {})
    return PipelineConfig(
        lookback_hours=int(raw["lookback_hours"]),
        max_candidates=int(raw["prefilter"]["max_candidates"]),
        temperature=float(raw["scoring"]["temperature"]),
        max_tokens=int(raw["scoring"]["max_tokens"]),
        top_n=int(raw["top_n"]),
        sources=parse_sources(raw),
        news=dict(raw.get("news") or {}),
        snapshot_track_days=int(snaps.get("track_days", 45)),
        snapshot_max_items=int(snaps.get("max_items", 20000)),
        snapshot_batch_size=int(snaps.get("batch_size", 100)),
    )


@dataclass(slots=True)
class RunResult:
    vertical: str
    since_date: str
    ran_at: str
    fetched: int
    candidates: int        # count after the prefilter
    scored: int            # rows that survived strict parsing
    watch_min: int         # rubric threshold the Top 榜 applied
    stored_new: int        # items that reached scoring as first-time entries
    run_mode: str          # 'pg' | 'off'
    hot_new: list          # [Item] 新仓库热榜 — star-desc, pre-prefilter
    candidate_items: list  # [Item] what the scorer saw; joins ScoredItem ids
    top: list              # [ScoredItem] weighted total ≥ watch_min, best-first
    usage: dict
    edition: str = "daily"  # which site section this run feeds (run.py flag)
    per_source: dict = None  # {source name: rows fetched} for the stats line
    farmed: dict = None      # {source name: rows dropped as a bought batch}
    trending: dict = None    # {period: rows} GitHub Trending boards, display shape
    rated: list = None       # [ScoredItem] every row rated today, best-first —
                             # the daily page shows all of them; `top` stays the
                             # `top_n` cut the push and the rollup read


def _top(scores: list, rubric: Rubric, cfg: PipelineConfig,
         items_by_id: dict, *, every: bool = False) -> list:
    """The day's picks, one board at a time: score-desc, `top_n` PER board.

    Repositories and Show HN posts are ranked separately (intel.sections) and
    each takes its own cap, because a single ranked list's order decides how
    much of each the reader sees and nobody chose that ratio. The two boards
    come back in `sections.ORDER`, concatenated: the deliver layer splits
    them again by source, and everything downstream that only wants "what the
    day picked" (the rollup, the JSON payload) reads the list whole.

    NO watch_min gate on the 精选 board — a day with nothing over the line
    still shows its best entries; the report marks them 'below the line' (the
    gate moved into the template's annotation, so the board can never be
    silently empty). The Show HN board carries the floor sections declares,
    because it is a second push message rather than the brief itself.

    `every` drops the cap: the daily page lists every row the day rated.
    """
    ranked = sorted(scores, key=lambda s: (-s.score, s.item_id))
    boards = sections.split(
        ranked,
        source=lambda s: getattr(items_by_id.get(s.item_id), "source", None),
        score=lambda s: s.score,
        limit=None if every else cfg.top_n)
    return [scored for name in sections.ORDER for scored in boards[name]]


def _check_output_ceiling(info: llm.ModelInfo, cfg: PipelineConfig) -> None:
    """Say so when a full batch may ask for more output than the endpoint
    allows; the request would be refused or cut short."""
    asked = llm.score_max_tokens(llm.SCORE_BATCH, cfg.max_tokens)
    if info.max_output_tokens and asked > info.max_output_tokens:
        print(f"scorer: a full batch asks for up to {asked} output tokens and "
              f"{info.version} accepts {info.max_output_tokens}; lower "
              "scoring.max_tokens in config.yaml", file=sys.stderr)


async def _ensure_lines(conn, rubric: Rubric, cfg: PipelineConfig,
                        version: str) -> None:
    """Tier lines for `version` before it scores. A calibration that cannot
    finish must not cost the day its issue: it reports, and the scores are
    read against the previous lines until the next run measures them."""
    try:
        await calibrate.ensure_lines(conn, rubric, version,
                                     temperature=cfg.temperature,
                                     max_tokens=cfg.max_tokens)
    except Exception as exc:  # noqa: BLE001 — reported; the run goes on
        await conn.rollback()
        print(f"calibrate: failed ({exc}); {version} borrows the previous "
              "lines", file=sys.stderr)


async def run_vertical(cfg: PipelineConfig, rubric: Rubric,
                       store: bool = True) -> RunResult:
    now = datetime.now(UTC)
    since_date = (now - timedelta(hours=cfg.lookback_hours)).date().isoformat()

    # The store opens BEFORE the fetch, because one source needs it to
    # fetch at all: the awesome-list diff is defined against what we have
    # already seen, and with no store every standing entry reads as new.
    dsn = os.environ.get(db.DSN_ENV_VAR, "") if store else ""
    conn = None
    if dsn:
        print(f"store: pg ({db.dsn_host(dsn)})")
        conn = await db.connect()
        await db.ensure_schema(conn)
    else:
        print("store: off (TIERLIST_PG_DSN not set)")

    try:
        return await _sweep(cfg, rubric, conn, now=now, since_date=since_date)
    finally:
        if conn is not None:
            await conn.close()


# GitHub Trending's period → the gain board window it speaks for.
TRENDING_WINDOWS = {"daily": 1, "weekly": 7, "monthly": 30}


def trending_bases(items: list[Item]) -> dict[int, dict[str, int]]:
    """{window days: {item_id: stars one window ago}} for trending rows,
    reconstructed as current stars minus GitHub's own gain for that period
    ("stars today", "this week", "this month")."""
    bases: dict[int, dict[str, int]] = {}
    for item in items:
        for period, gain in (item.raw.get("period_stars") or {}).items():
            days = TRENDING_WINDOWS.get(period)
            if days and gain:
                bases.setdefault(days, {})[item.id] = max(item.stars - gain, 0)
    return bases


def trending_boards(items: list[Item],
                    tags_by_id: dict[str, list[str]]) -> dict:
    from intel.editions import trending_rows  # lazy, like hot_new_rows

    return trending_rows(items, tags_by_id)


async def _tag_items(conn, items: list[Item], label: str) -> None:
    """Tag repositories that have never been tagged: one tags-only LLM call,
    the same one `intel.backfill_tags` makes. A miss is stored as an empty
    array so the item is not asked about again. Failure is reported and
    swallowed; the boards render without tags rather than not at all."""
    if not items:
        return
    readmes = await github_readme.fetch_excerpts(items)
    try:
        tagged, usage = await llm.tag_candidates(items, readmes)
    except Exception as exc:
        print(f"{label} tags: failed ({exc})", file=sys.stderr)
        return
    for item in items:
        await db.set_tags_for_item(conn, item.id, tagged.get(item.id) or [])
    print(f"{label} tags: {len(tagged)}/{len(items)} tagged, "
          f"{usage.get('completion_tokens', 0)} completion tokens",
          file=sys.stderr)


# Recent-cohort rows per tags-only call: the size `intel.backfill_tags` runs.
RECENT_TAG_BATCH = 50


def recent_tag_queue(items: list[Item], untagged: set[str],
                     new_ids: set[str], limit: int) -> list[Item]:
    """The untagged recent-cohort rows to tag this run: today's arrivals
    first, then the most-starred, at most `limit`. A first run meets the
    whole untagged cohort; the cap spreads it over the following days."""
    queue = [i for i in items if i.id in untagged]
    queue.sort(key=lambda i: (i.id not in new_ids, -i.stars, i.id))
    return queue[:max(limit, 0)]


def source_options(cfg: PipelineConfig, name: str) -> dict:
    for source in cfg.sources:
        if source.name == name:
            return source.options
    return {}


async def _sweep(cfg: PipelineConfig, rubric: Rubric, conn, *,
                 now: datetime, since_date: str) -> RunResult:
    async with httpx.AsyncClient(timeout=github._SEARCH_TIMEOUT_S) as client:
        items, per_source = await collect.fetch_enabled(
            client, cfg.sources,
            collect.FetchContext(since_date=since_date, now=now, conn=conn,
                                 vertical=rubric.vertical))

    # Board-only rows (github_trending) are established repositories tracked
    # for the star series alone. They join the store and the snapshots and
    # nothing else: the paid batch, the 新仓库热榜 and the new-item count are
    # all about new repositories. A row the search also returned is the
    # search's, so its own raw payload is not overwritten.
    # The recent cohort (github_recent) is board-only as well, but it is
    # thousands of rows the store mostly holds already: it is inserted where
    # new, snapshotted and tagged, and it never reaches the trending boards.
    recent_items = [i for i in items if i.raw.get("recent")]
    items = [i for i in items if not i.raw.get("recent")]
    trending_items = [i for i in items if i.raw.get("board_only")]
    fetched_ids = {i.id for i in items if not i.raw.get("board_only")}
    board_only = [i for i in items
                  if i.raw.get("board_only") and i.id not in fetched_ids]
    items = [i for i in items if not i.raw.get("board_only")]

    # Bought batches leave before anything reads the fetch: the paid batch,
    # the 新仓库热榜 and the star snapshots are all slices of these same rows.
    # `items` keeps the full fetch — the stats line's 抓取 count says what the
    # sources returned, and the store keeps the farm rows so the next one is
    # diagnosable.
    rules = source_rules(cfg)
    kept, farmed = prefilter.drop_bursts(items, rules)
    for source, n in farmed.items():
        print(f"source {source}: dropped {n} rows as bought batches "
              f"(same star count, minutes apart)", file=sys.stderr)

    # Each source is filtered by its own rules and the survivors are merged
    # round-robin, so the shared cap is shared rather than taken by whichever
    # source returned the most rows.
    candidates = prefilter.apply_by_source(kept, rules,
                                           cfg.max_candidates, now)
    # The board is GitHub's by definition - it ranks new REPOSITORIES by
    # accumulated stars. An HN post has points, which are not stars and do
    # not belong in the same column.
    repos = [i for i in kept if i.source == "github"]

    info = await llm.model_info()
    _check_output_ceiling(info, cfg)
    if conn is None:
        scores, usage = await llm.score_candidates(
            candidates, rubric,
            now=now, temperature=cfg.temperature, max_tokens=cfg.max_tokens,
            model_version=info.version)
        return RunResult(
            vertical=rubric.vertical,
            since_date=since_date,
            ran_at=now.isoformat(timespec="seconds"),
            fetched=len(items),
            candidates=len(candidates),
            scored=len(scores),
            watch_min=rubric.watch_min,
            stored_new=len(candidates),
            run_mode="off",
            hot_new=repos[:10],
            per_source=per_source,
            farmed=farmed,
            trending=trending_boards(trending_items, {}),
            candidate_items=candidates,
            top=_top(scores, rubric, cfg, {i.id: i for i in items}),
            rated=_top(scores, rubric, cfg, {i.id: i for i in items},
                       every=True),
            usage=usage,
        )

    # The version, not the request alias: `deepseek-flash` names whatever
    # model DeepSeek serves under it today, and the page says which one.
    model = info.version
    run_id = await db.start_run(conn, rubric.vertical, model)
    scores: list = []
    usage: dict = {}
    try:
        new_count = await db.upsert_items(conn, items, rubric.vertical)
        await db.upsert_items(conn, board_only, rubric.vertical)
        listed = {i.id for i in items + trending_items}
        recent = [i for i in recent_items if i.id not in listed]
        recent_new = await db.insert_new_items(conn, recent, rubric.vertical)
        if recent_items:
            print(f"source github_recent: {len(recent_new)} new of "
                  f"{len(recent_items)}", file=sys.stderr)
        snap_date = now.date()
        # Repositories only: intel.star_snapshots is a star series, and
        # an HN post's `stars` field holds points. Mixing the two would
        # put a comment count on the star gain board.
        await db.save_snapshots(conn, repos + board_only + recent, snap_date)
        # A trending row arrives with no series behind it, so each gain
        # board would have to wait its whole window to rank it. GitHub
        # states the period's gain itself; total minus that gain is the
        # count one window back, written only where no measured snapshot
        # exists.
        for days, counts in trending_bases(board_only).items():
            await db.seed_star_counts(conn, counts,
                                      snap_date - timedelta(days=days))
        # The fetch only carries stars for repositories young enough to
        # still match the search window, so on its own it gives every
        # project a three-day series and no board wider than that can
        # ever be honest. The refresh re-reads the rest of the tracked
        # cohort and writes it under the SAME snap_date, before the
        # pages are rendered below — the boards read the latest
        # snapshot date, so a refresh landing after the render would
        # only reach tomorrow's issue. It never raises.
        await resnapshot.refresh_quietly(
            conn, snap_date, track_days=cfg.snapshot_track_days,
            limit=cfg.snapshot_max_items,
            batch_size=cfg.snapshot_batch_size)
        fresh = await db.filter_new_candidates(conn, candidates)
        # The read above opened a transaction. Close it before the LLM call:
        # the database role has idle_in_transaction_session_timeout=5min, and a
        # scoring call can take longer, so Postgres would drop the connection
        # mid-run and the scores would have nowhere to be written.
        untagged = await db.untagged_ids(conn,
                                         [i.id for i in trending_items])
        recent_untagged = await db.untagged_ids(conn, [i.id for i in recent])
        await conn.commit()
        await _tag_items(conn, [i for i in trending_items
                                if i.id in untagged], "trending")
        queue = recent_tag_queue(
            recent, recent_untagged, recent_new,
            int(source_options(cfg, "github_recent").get("tag_limit", 200)))
        for start in range(0, len(queue), RECENT_TAG_BATCH):
            await _tag_items(conn, queue[start:start + RECENT_TAG_BATCH],
                             "recent")
        await _ensure_lines(conn, rubric, cfg, model)
        await conn.commit()
        scores, usage = await llm.score_candidates(
            fresh, rubric,
            now=now, temperature=cfg.temperature, max_tokens=cfg.max_tokens,
            model_version=model)
        # Rows the model wrote without English get it before they are saved,
        # so this run's English page shows them in English too.
        from intel import translate_reasons

        scores, used = await translate_reasons.fill_missing(scores)
        usage = llm.merge_usage(usage, used)
        top = _top(scores, rubric, cfg, {i.id: i for i in items})
        await db.save_scores(conn, scores, {i.id: i for i in items},
                             snap_date)
        # Today's arrivals whose author wrote the description in a foreign
        # script get an English line before the pages are rendered.
        try:
            await translate_reasons.backfill_descriptions(conn, limit=500)
        except Exception as exc:  # noqa: BLE001 — the issue goes out regardless
            print(f"describe: failed ({exc})", file=sys.stderr)
        # Today's new tags may spell a known entity a new way; fold them
        # before the tag pages are written.
        from intel import retag

        try:
            await retag.run(conn, apply=True)
        except Exception as exc:  # noqa: BLE001 — the issue goes out regardless
            print(f"retag: failed ({exc})", file=sys.stderr)
        warning = await calibrate.drift(conn, rubric.vertical, model)
        if warning:
            print(warning, file=sys.stderr)
        await db.save_rollup(conn, rubric.vertical, snap_date, new_count,
                             [s.item_id for s in top],
                             trend.lang_dist(candidates))
        # The board goes to the store in the same shape the page renders
        # it in, so a rebuild reproduces the board rather than a
        # reconstruction of it. It is a slice of `items` — the raw fetch,
        # before the prefilter — so nothing else in the schema holds it.
        from intel.editions import hot_new_rows

        trending = trending_boards(
            trending_items,
            await db.tags_for_ids(conn, [i.id for i in trending_items]))
        await db.finish_run(conn, run_id, status="ok", fetched=len(items),
                            candidates=len(candidates), scored=len(scores),
                            model=model, usage=usage, error=None,
                            hot_new=hot_new_rows(repos[:10]),
                            trending=trending)
    except Exception as exc:
        await db.finish_run(conn, run_id, status="error",
                            fetched=len(items),
                            candidates=len(candidates),
                            scored=len(scores), model=model,
                            usage=usage, error=str(exc))
        raise

    return RunResult(
        vertical=rubric.vertical,
        since_date=since_date,
        ran_at=now.isoformat(timespec="seconds"),
        fetched=len(items),
        candidates=len(candidates),
        scored=len(scores),
        watch_min=rubric.watch_min,
        stored_new=len(fresh),
        run_mode="pg",
        hot_new=repos[:10],
        trending=trending,
        per_source=per_source,
        farmed=farmed,
        candidate_items=fresh,
        top=top,
        rated=_top(scores, rubric, cfg, {i.id: i for i in items}, every=True),
        usage=usage,
    )
