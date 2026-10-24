"""Tier lines that follow the scoring model.

A tier is a score read against lines, and the lines are percentiles of one
model's own scores: the top 2% are S, the top 10% A, the top 30% B, the top
60% C. The model behind an alias such as DeepSeek's `deepseek-flash` changes
when the provider updates it, and a new model scores the same rubric
differently, so lines measured on the old one would quietly hand out the
wrong share of every tier.

So the lines are kept per model version (`intel.tier_lines`) and every
score records the version that made it. Before a daily run scores
anything, `ensure_lines` checks the version `llm.model_info` reports:

* a version with lines goes ahead;
* the first version a store ever sees takes the rubric's own `tiers:`
  lines, which were measured on the model of that time;
* a new version is calibrated: the items rated in the last 30 days are
  scored again by it, into `intel.calibration_scores` and never into
  `intel.scores`, so past issues keep their tiers; the lines are the same
  percentiles of those scores. A calibration that fails or is cut short
  leaves the version waiting, its scores read against the lines of the
  version before it, and the next run resumes where it stopped.

A provider can also change the model and keep the name. `drift` watches for
that: when the share of A-or-better over the last week leaves the band
around its expected 10%, it says so in the log. It does not recalibrate on
its own, because a genuinely strong or weak week moves that share too;
`python -m intel.calibrate --force` measures the current version again.

    python -m intel.calibrate             # the daily check, on its own
    python -m intel.calibrate --status    # versions, lines and drift
    python -m intel.calibrate --force     # measure the current version again

Not to be confused with `intel.calibration`, the read-only report of whether
scores predicted star growth.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv

from intel import aio, tiers
from intel.rubrics import Rubric
from intel.score import llm
from intel.sources.base import Item
from intel.store import db

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

SHARES = (("S", 0.02), ("A", 0.10), ("B", 0.30), ("C", 0.60))
DEFAULTS = {
    "auto": True,          # calibrate a new version without asking
    "window_days": 30,     # how far back the rescored items are taken from
    "max_items": 2000,     # cost ceiling of one calibration
    "min_items": 200,      # fewer than this and percentiles are not trusted
    "extend_days": 90,     # widened window when 30 days hold too few
    "drift_days": 7,
    "drift_min": 150,      # scores needed before the drift check speaks
    "drift_factor": 3.0,   # alarm outside expected/factor .. expected*factor
}


def settings(path: Path | None = None) -> dict:
    """`calibration:` in config.yaml over the defaults."""
    raw = yaml.safe_load((path or _PROJECT_ROOT / "config.yaml")
                         .read_text(encoding="utf-8")) or {}
    return {**DEFAULTS, **(raw.get("calibration") or {})}


def percentile_lines(scores: list[int]) -> dict[str, int]:
    """The lowest score still inside each tier's top share: the method the
    rubric's own lines were set with. Lines that tie are pushed apart by one
    point, lowest first, so they always descend S > A > B > C."""
    ordered = sorted(scores)
    n = len(ordered)
    lines = {t: ordered[max(n - int(round(q * n)), 0)] for t, q in SHARES}
    for upper, lower in (("S", "A"), ("A", "B"), ("B", "C")):
        if lines[lower] >= lines[upper]:
            lines[lower] = lines[upper] - 1
    return lines


async def load_lines(conn) -> None:
    """Make every measured line available to `tiers` in this process."""
    tiers.use_store_lines(await db.tier_line_rows(conn))


def _lines_text(pairs) -> str:
    return " ".join(f"{t}>={n}" for n, t in pairs)


async def ensure_lines(conn, rubric: Rubric, version: str, *,
                       temperature: float, max_tokens: int,
                       today: date | None = None, cfg: dict | None = None) -> str:
    """Make sure `version` has lines before it scores; returns what happened:
    'known', 'seeded', 'calibrated' or 'pending'."""
    cfg = cfg or settings()
    await load_lines(conn)
    if tiers.has_store_lines(rubric.vertical, version):
        return "known"
    dims = sorted(rubric.dimensions)
    rows = [r for r in await db.tier_line_rows(conn)
            if r["vertical"] == rubric.vertical and sorted(r["dims"]) == dims]
    if not rows:
        lines = {t: n for n, t in tiers.lines(rubric.vertical)}
        await db.save_tier_lines(conn, vertical=rubric.vertical,
                                 model_version=version, dims=dims, lines=lines,
                                 sample=0, source="rubric")
        await load_lines(conn)
        print(f"calibrate: first version seen, {version}, takes the rubric's "
              f"lines ({_lines_text(tiers.lines(rubric.vertical))})",
              file=sys.stderr)
        return "seeded"
    previous = rows[-1]["model_version"]
    if not cfg["auto"]:
        print(f"calibrate: model changed from {previous} to {version}; "
              "calibration.auto is off, so its scores borrow the old lines "
              "until `python -m intel.calibrate --force` is run",
              file=sys.stderr)
        return "pending"
    print(f"calibrate: model changed from {previous} to {version}, "
          "measuring its lines", file=sys.stderr)
    return await recalibrate(conn, rubric, version, temperature=temperature,
                             max_tokens=max_tokens, today=today, cfg=cfg)


async def recalibrate(conn, rubric: Rubric, version: str, *,
                      temperature: float, max_tokens: int,
                      today: date | None = None, cfg: dict | None = None,
                      fresh: bool = False) -> str:
    """Score recent items with `version` and record the lines they give.

    `fresh` discards what an earlier calibration of the same version holds,
    for a model that changed without changing its name."""
    from intel.backfill import anchor_time

    cfg = cfg or settings()
    today = today or datetime.now(UTC).date()
    dims = sorted(rubric.dimensions)
    cohort: list = []
    for days in (int(cfg["window_days"]), int(cfg["extend_days"])):
        since = today - timedelta(days=days)
        cohort = await db.calibration_cohort(conn, dims=dims, since=since,
                                             until=today,
                                             limit=int(cfg["max_items"]))
        if len(cohort) >= int(cfg["min_items"]):
            break
    if len(cohort) < int(cfg["min_items"]):
        print(f"calibrate: only {len(cohort)} rated items in the last "
              f"{cfg['extend_days']} days, under the {cfg['min_items']} a "
              f"calibration needs; {version} borrows the previous lines",
              file=sys.stderr)
        return "pending"

    if fresh:
        await db.clear_calibration_scores(conn, version)
    done = await db.calibration_scores(conn, version)
    by_day: dict[date, list[Item]] = defaultdict(list)
    for r in cohort:
        if r["item_id"] in done:
            continue
        raw = r["raw"] or {}
        stars = r["stars"]
        if stars is None:
            stars = int(raw.get("stargazers_count") or 0)
        by_day[r["snap_date"]].append(Item(
            id=r["item_id"], source=r["source"], url=r["url"], name=r["name"],
            description=r["description"] or "", lang=r["lang"],
            stars=int(stars), created_at=r["created_at"], raw=raw))

    usage: dict = {}
    try:
        for day in sorted(by_day):
            items = by_day[day]
            now = await anchor_time(conn, rubric.vertical, day)
            # End the read transaction before the model call (the database
            # role drops a connection idle in a transaction for 5 minutes).
            await conn.commit()
            scored, got = await llm.score_candidates(
                items, rubric, now=now, temperature=temperature,
                max_tokens=max_tokens, model_version=version)
            await db.save_calibration_scores(conn, version, scored,
                                             {i.id: day for i in items})
            usage = llm.merge_usage(usage, got)
    except Exception as exc:  # noqa: BLE001 — kept, reported, resumed next run
        print(f"calibrate: stopped part way ({exc}); {version} borrows the "
              "previous lines and the next run resumes", file=sys.stderr)
        return "pending"

    held = await db.calibration_scores(conn, version)
    scores = [held[r["item_id"]] for r in cohort if r["item_id"] in held]
    if len(scores) < int(cfg["min_items"]):
        print(f"calibrate: {len(scores)} of {len(cohort)} items came back "
              f"scored, too few; {version} borrows the previous lines",
              file=sys.stderr)
        return "pending"
    before = _lines_text(tiers.lines(rubric.vertical, version=version))
    lines = percentile_lines(scores)
    days = [r["snap_date"] for r in cohort]
    await db.save_tier_lines(conn, vertical=rubric.vertical,
                             model_version=version, dims=dims, lines=lines,
                             sample=len(scores), source="calibration",
                             window_from=min(days), window_to=max(days))
    await load_lines(conn)
    after = _lines_text(tiers.lines(rubric.vertical, version=version))
    print(f"calibrate: {version} measured on {len(scores)} items "
          f"({min(days)} to {max(days)}): {after} (was {before}); "
          f"tokens in {usage.get('prompt_tokens', 0)}, "
          f"out {usage.get('completion_tokens', 0)}", file=sys.stderr)
    return "calibrated"


def drift_band(cfg: dict) -> tuple[float, float]:
    expected = dict(SHARES)["A"]
    factor = float(cfg["drift_factor"])
    return expected / factor, expected * factor


async def drift(conn, vertical: str, version: str, *,
                today: date | None = None, cfg: dict | None = None) -> str | None:
    """A warning when the share of A-or-better over the last week leaves
    its band, else None. A, not S: at 2% a week holds a handful of S scores
    and their share swings by chance alone."""
    cfg = cfg or settings()
    today = today or datetime.now(UTC).date()
    scores = await db.recent_version_scores(
        conn, model_version=version,
        since=today - timedelta(days=int(cfg["drift_days"])), until=today)
    if len(scores) < int(cfg["drift_min"]):
        return None
    a_line = dict((t, n) for n, t in tiers.lines(vertical, version=version))["A"]
    share = sum(1 for s in scores if s >= a_line) / len(scores)
    low, high = drift_band(cfg)
    if low <= share <= high:
        return None
    return (f"calibrate: drift — {share:.1%} of {len(scores)} scores by "
            f"{version} in the last {cfg['drift_days']} days reached A, "
            f"expected about 10% (band {low:.1%} to {high:.1%}). The model may "
            "have changed under the same name; `python -m intel.calibrate "
            "--force` measures it again.")


async def _main_async(argv: list[str]) -> int:
    from intel.pipeline import load_config
    from intel.rubrics import load_rubric

    parser = argparse.ArgumentParser(prog="python -m intel.calibrate")
    parser.add_argument("--vertical", default="dev_tools")
    parser.add_argument("--status", action="store_true",
                        help="print the versions, their lines and the drift "
                             "check; calls no model")
    parser.add_argument("--force", action="store_true",
                        help="measure the current version again from scratch")
    args = parser.parse_args(argv)

    cfg = load_config(_PROJECT_ROOT / "config.yaml")
    rubric = load_rubric(args.vertical)
    conn = await db.connect()
    try:
        await db.ensure_schema(conn)
        info = await llm.model_info()
        if args.status:
            await load_lines(conn)
            for r in await db.tier_line_rows(conn):
                if r["vertical"] != args.vertical:
                    continue
                print(f"{r['created_at']:%Y-%m-%d}  {r['model_version']:<28} "
                      f"S>={r['s_line']} A>={r['a_line']} B>={r['b_line']} "
                      f"C>={r['c_line']}  {r['source']}, {r['sample']} items")
            print(f"current model: {info.version}"
                  + ("" if tiers.has_store_lines(args.vertical, info.version)
                     else " (no lines yet)"))
            print(await drift(conn, args.vertical, info.version)
                  or "drift: within the band, or too few scores to tell")
            return 0
        if args.force:
            await load_lines(conn)
            status = await recalibrate(conn, rubric, info.version,
                                       temperature=cfg.temperature,
                                       max_tokens=cfg.max_tokens, fresh=True)
        else:
            status = await ensure_lines(conn, rubric, info.version,
                                        temperature=cfg.temperature,
                                        max_tokens=cfg.max_tokens)
        print(f"calibrate: {info.version}: {status}")
        return 0 if status != "pending" else 1
    finally:
        await conn.close()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(_PROJECT_ROOT / ".env")
    try:
        return aio.run(_main_async(sys.argv[1:]))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # CLI boundary — report, don't traceback-spam
        print(f"calibrate failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
