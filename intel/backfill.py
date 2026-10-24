"""Rescore a day the store already holds, without refetching it.

A sweep that fetches successfully and then dies at the paid stage leaves the
day half-made: `intel.items` has the cohort, `intel.scores` has nothing, and
the site links a date it cannot render. That happened on 2026-09-08, when the
gateway began rejecting every scoring call, and the fetch window had moved on
by the time anyone noticed. Refetching cannot recover it — GitHub's search
returns repositories created inside a rolling window, so a day's candidates
are gone the moment the window slides past them.

The cohort is therefore read back from the store: the items `first_seen` on
that date, filtered by the same prefilter rules the live sweep applies, judged
by the same scorer, and written under that date's `snap_date`. Rendering is
left to `deliver.site.write_history`, which builds every dated page from the
store, so a backfilled day is assembled by exactly the code that assembles a
live one.

Nothing is pushed. A brief is a message about today; a day recovered later is
history, and the site is where history belongs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time

from intel.pipeline import PipelineConfig, source_rules
from intel.rubrics import Rubric
from intel.score import llm, prefilter
from intel.store import db, trend


@dataclass(slots=True)
class BackfillResult:
    """What one rescore did, in the order a reader asks about it."""

    day: date
    vertical: str
    cohort: int          # items the store holds for that date
    candidates: int      # survivors of the prefilter
    already_scored: int  # skipped because the day already judged them
    scored: int          # rows this pass added
    day_total: int       # rows the day carries now, this pass included
    usage: dict = field(default_factory=dict)


async def anchor_time(conn, vertical: str, day: date) -> datetime:
    """The clock the prefilter is judged against for `day`.

    A prefilter reads item age, so rescoring with today's clock would drop
    entries for being three days older than they were when the day ran. The
    day's own run row carries the real time; a date that never opened one
    (nothing to recover, but the CLI still accepts it) falls back to noon UTC.
    """
    started = await db.run_started_on(conn, vertical, day)
    if started is not None:
        return started if started.tzinfo else started.replace(tzinfo=UTC)
    return datetime.combine(day, time(12, 0), tzinfo=UTC)


async def rescore_day(cfg: PipelineConfig, rubric: Rubric, day: date, *,
                      force: bool = False) -> BackfillResult:
    """Score `day`'s stored cohort and leave the store ready to render it.

    `force` rescores items the day already holds a score for; by default they
    are skipped, which makes a repeated run cost nothing and makes an
    interrupted one safe to resume.
    """
    if not os.environ.get(db.DSN_ENV_VAR, ""):
        raise RuntimeError(
            f"backfill needs the store — {db.DSN_ENV_VAR} is not set")

    conn = await db.connect()
    try:
        await db.ensure_schema(conn)
        now = await anchor_time(conn, rubric.vertical, day)
        items = await db.items_seen_on(conn, rubric.vertical, day)
        rules = source_rules(cfg)
        kept, _farmed = prefilter.drop_bursts(items, rules)
        candidates = prefilter.apply_by_source(kept, rules,
                                               cfg.max_candidates, now)

        done = set() if force else await db.scored_ids_on(conn, day)
        pending = [c for c in candidates if c.id not in done]
        already = len(candidates) - len(pending)

        scores: list = []
        usage: dict = {}
        if pending:
            # End the read transaction before the LLM call; see the same
            # commit in pipeline._sweep (database role: 5min idle-in-transaction
            # timeout, observed killing a 2026-09-27 backfill).
            from intel import calibrate

            version = (await llm.model_info()).version
            await calibrate.ensure_lines(conn, rubric, version,
                                         temperature=cfg.temperature,
                                         max_tokens=cfg.max_tokens)
            await conn.commit()
            scores, usage = await llm.score_candidates(
                pending, rubric, now=now, temperature=cfg.temperature,
                max_tokens=cfg.max_tokens, model_version=version)
            from intel import translate_reasons

            scores, used = await translate_reasons.fill_missing(scores)
            usage = llm.merge_usage(usage, used)
            await db.save_scores(conn, scores, {i.id: i for i in items}, day)

        # The rollup is rebuilt from every score the day holds, not from this
        # pass alone: a second pass over a day that already had picks used to
        # replace its board with whatever few items were still unscored.
        rows = await db.window_score_rows(conn, 0, as_of=day)
        top_ids = [row["item_id"] for row in
                   sorted(rows, key=lambda r: (-(r["score"] or 0),
                                               r["item_id"]))[:cfg.top_n]]
        await db.save_rollup(conn, rubric.vertical, day, len(items), top_ids,
                             trend.lang_dist(candidates))

        return BackfillResult(
            day=day, vertical=rubric.vertical, cohort=len(items),
            candidates=len(candidates), already_scored=already,
            scored=len(scores), day_total=len(rows), usage=usage)
    finally:
        await conn.close()
