"""Did the score predict anything? — the rubric's after-the-fact calibration.

`intel.scores` records what the model believed about a project on the day it
appeared; `intel.star_snapshots` records what actually happened to it
afterwards. Until the two are put side by side, nothing in the system says
whether a 70 grows faster than a 40 — the rubric is an opinion no one has
ever checked.

This is a read-only report. It writes nothing, calls no model and touches no
page:

    python -m intel.calibration
    python -m intel.calibration --horizons 7,14,30 --json

Method, and its limits:

* One row per item, using its FIRST score — the prediction made when the
  project was new. A later re-score is hindsight and would flatter the rubric.
* Growth at horizon h = stars at (score date + h) − stars at the score date,
  each read from that item's own snapshot series, with a few days of slack
  for a missed refresh. An item whose series does not reach h is absent from
  that horizon rather than counted as zero growth.
* Medians, not means: one repository going from 20 to 9000 stars would
  otherwise decide the whole band.
* Spearman's rho over the individual (score, growth) pairs is the one number
  that answers the question directly. +1 means the ranking is perfect, 0
  means the score carries no information about growth, negative means it is
  pointing the wrong way.

The horizons only start paying out 30 days after `intel.resnapshot` began
following the cohort (2026-08-30); before that every series was three days
long and the table below is honestly empty.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from intel import aio
from intel.store import db

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_HORIZONS = (7, 14, 30, 60)

# Fixed bands rather than quantiles: a quantile band silently redefines
# itself every run, so two reports a month apart could not be compared. 60 is
# the rubric's watch_min — the line the site's Top board draws.
BUCKETS: tuple[tuple[int, int, str], ...] = (
    (0, 30, "0–29"),
    (30, 45, "30–44"),
    (45, 60, "45–59"),
    (60, 70, "60–69  (过线)"),
    (70, 101, "70+"),
)


def bucket_of(score: int) -> str:
    for low, high, label in BUCKETS:
        if low <= score < high:
            return label
    return BUCKETS[-1][2]


def horizon_slack(horizon: int) -> int:
    """Days of tolerance around a horizon's endpoints. Same shape as the gain
    board's: about a seventh of the span, at least one day, so a single
    missed refresh does not erase a month of following an item."""
    return max(1, horizon // 7)


def growth_at(series: list[tuple[date, int]], scored_on: date,
              horizon: int) -> tuple[int, int] | None:
    """(stars gained, days actually spanned) for one item at one horizon, or
    None when its snapshots do not reach that far.

    `series` is (date, stars) in any order. The baseline is the snapshot
    closest to the score date and the endpoint the first snapshot at or after
    (score date + horizon); both must fall inside the slack, and the endpoint
    may never be earlier than the baseline.
    """
    if not series:
        return None
    slack = horizon_slack(horizon)
    ordered = sorted(series)

    near = [(abs((d - scored_on).days), d, s) for d, s in ordered
            if abs((d - scored_on).days) <= slack]
    if not near:
        return None
    _, base_date, base_stars = min(near)

    target = scored_on.toordinal() + horizon
    later = [(d, s) for d, s in ordered
             if 0 <= d.toordinal() - target <= slack and d > base_date]
    if not later:
        return None
    end_date, end_stars = later[0]
    return end_stars - base_stars, (end_date - base_date).days


def spearman(pairs: list[tuple[float, float]]) -> float | None:
    """Rank correlation between two series, ties averaged. None below four
    pairs, where the statistic is noise wearing a number's clothes."""
    if len(pairs) < 4:
        return None
    xs = _ranks([p[0] for p in pairs])
    ys = _ranks([p[1] for p in pairs])
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / (var_x * var_y) ** 0.5


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def summarize(observations: list[tuple[int, int, int]]) -> dict:
    """(score, gain, span_days) → one horizon's report block.

    Buckets keep their order and appear even when empty: a band with no
    members is itself a finding (nothing scored above 70 that we have
    followed long enough), and dropping it would make two runs' tables
    disagree in shape.
    """
    by_bucket: dict[str, list[tuple[int, int]]] = {
        label: [] for _, _, label in BUCKETS}
    for score, gain, _span in observations:
        by_bucket[bucket_of(score)].append((score, gain))
    return {
        "n": len(observations),
        "rho": spearman([(float(s), float(g)) for s, g, _ in observations]),
        "buckets": [
            {
                "label": label,
                "n": len(by_bucket[label]),
                "median_gain": (
                    round(statistics.median(g for _, g in by_bucket[label]), 1)
                    if by_bucket[label] else None),
                "max_gain": max((g for _, g in by_bucket[label]),
                                default=None),
            }
            for _, _, label in BUCKETS
        ],
    }


async def first_scores(conn) -> list[tuple[str, date, int]]:
    """(item_id, first score date, score) — one prediction per item."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT DISTINCT ON (item_id) item_id, snap_date, score
            FROM intel.scores
            WHERE score IS NOT NULL
            ORDER BY item_id, snap_date
            """
        )
        return [(r[0], r[1], int(r[2])) for r in await cur.fetchall()]


async def series_for(conn, item_ids: list[str]) -> dict[str, list]:
    """{item_id: [(snap_date, stars), ...]} for the given items."""
    if not item_ids:
        return {}
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT item_id, snap_date, stars FROM intel.star_snapshots
            WHERE item_id = ANY(%s)
            ORDER BY item_id, snap_date
            """,
            (item_ids,),
        )
        rows = await cur.fetchall()
    series: dict[str, list] = {}
    for item_id, snap_date, stars in rows:
        series.setdefault(item_id, []).append((snap_date, int(stars)))
    return series


async def report(conn, horizons: tuple[int, ...] = DEFAULT_HORIZONS) -> dict:
    scores = await first_scores(conn)
    series = await series_for(conn, [item_id for item_id, _, _ in scores])
    coverage = await db.snapshot_coverage(conn)
    horizon_blocks = {}
    for horizon in horizons:
        observations = []
        for item_id, scored_on, score in scores:
            got = growth_at(series.get(item_id, []), scored_on, horizon)
            if got is not None:
                observations.append((score, got[0], got[1]))
        horizon_blocks[horizon] = summarize(observations)
    return {
        "scored_items": len(scores),
        "coverage": {
            "items": coverage["items"],
            "first": str(coverage["first"]),
            "last": str(coverage["last"]),
            "longest_series_days": coverage["longest_span"] + 1
            if coverage["items"] else 0,
        },
        "horizons": horizon_blocks,
    }


def render(result: dict) -> str:
    cov = result["coverage"]
    lines = [
        "tierlist 评分校准 — 分数 vs 之后的真实 star 增长",
        f"打过分的项目 {result['scored_items']} 个 · 快照覆盖 {cov['items']} 个 "
        f"({cov['first']}..{cov['last']})，最长一条序列 "
        f"{cov['longest_series_days']} 天",
        "",
    ]
    for horizon, block in result["horizons"].items():
        rho = block["rho"]
        rho_text = "样本不足" if rho is None else f"{rho:+.2f}"
        lines.append(f"== {horizon} 天后 · 样本 {block['n']} · "
                     f"Spearman ρ {rho_text}")
        if not block["n"]:
            lines.append("   (还没有任何项目的快照跨过这个窗口)")
            lines.append("")
            continue
        lines.append(f"   {'分档':<14}{'n':>5}{'中位增长':>10}{'最大增长':>10}")
        for bucket in block["buckets"]:
            median = "—" if bucket["median_gain"] is None \
                else f"{bucket['median_gain']:g}"
            biggest = "—" if bucket["max_gain"] is None \
                else str(bucket["max_gain"])
            lines.append(f"   {bucket['label']:<14}{bucket['n']:>5}"
                         f"{median:>10}{biggest:>10}")
        lines.append("")
    return "\n".join(lines)


async def _main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="intel.calibration",
        description="score bands vs the star growth that followed "
                    "(read-only)")
    parser.add_argument("--horizons", default=",".join(
        str(h) for h in DEFAULT_HORIZONS),
        help="comma-separated days after the score, e.g. 7,14,30,60")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output instead of the table")
    args = parser.parse_args(argv)
    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())

    conn = await db.connect()
    try:
        result = await report(conn, horizons)
    finally:
        await conn.close()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(render(result))
    return 0


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
        print(f"calibration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
