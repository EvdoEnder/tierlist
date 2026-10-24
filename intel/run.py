"""CLI entry — `python -m intel.run --vertical dev_tools [--edition daily]`.

M1 prints to stdout only: the two leaderboards plus a stats line. Exit 0
when a report was produced, 1 on configuration or network failure. `--json`
emits the machine-readable payload.

Since M3 a successful run also delivers: the brief lands as markdown on disk
and the static site is rewritten (daily fetch+score; weekly/monthly
aggregate from Postgres with no fetch and no LLM). Delivery notes print to
stderr so `--json` stdout stays machine-clean.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from intel import aio, features, sections
from intel.pipeline import load_config, run_vertical
from intel.rubrics import Rubric, load_rubric

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _item_json(item) -> dict:
    return {
        "id": item.id,
        "url": item.url,
        "name": item.name,
        "description": item.description,
        "lang": item.lang,
        "stars": item.stars,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def result_to_dict(result) -> dict:
    by_id = {i.id: i for i in result.candidate_items}
    return {
        "vertical": result.vertical,
        "since_date": result.since_date,
        "ran_at": result.ran_at,
        "stats": {
            "fetched": result.fetched,
            "candidates": result.candidates,
            "scored": result.scored,
            "usage": result.usage,
        },
        "hot_new": [_item_json(i) for i in result.hot_new],
        "top": [{
            **_item_json(by_id[s.item_id]),
            "score": s.score,
            "dims": s.dims,
            "reason": s.reason,
            "reason_en": s.reason_en,
            "verdict": s.verdict,
        } for s in result.top],
    }


def _print_human(result, rubric: Rubric) -> None:
    print(f"tierlist — {result.vertical} 雷达 (创建 > {result.since_date})")
    farmed = sum((result.farmed or {}).values())
    farm_note = f" · 剔除刷星 {farmed}" if farmed else ""
    print(f"抓取 {result.fetched}{farm_note} · 粗筛后 {result.candidates} · "
          f"模型返回 {result.scored} 条")
    usage = result.usage or {}
    print(f"LLM tokens: prompt={usage.get('prompt_tokens')} "
          f"completion={usage.get('completion_tokens')}")
    print()

    print(f"== 新仓库热榜 (按累计 stars, 前 {len(result.hot_new)})")
    for rank, item in enumerate(result.hot_new, 1):
        lang = f" {item.lang}" if item.lang else ""
        print(f"{rank:>3}. {item.name}  ★{item.stars}{lang}")
        print(f"     {item.url}")
        if item.description:
            print(f"     {item.description[:100]}")
    print()

    print(f"== 精选 Top (rubric 加权总分 ≥ {result.watch_min}, "
          f"按分展示前 {len(result.top)} 个)")
    if not result.top:
        print("(今天没有项目过线)")
        return
    by_id = {i.id: i for i in result.candidate_items}
    boards = sections.split(
        result.top,
        source=lambda s: getattr(by_id.get(s.item_id), "source", None),
        score=lambda s: s.score)
    for name in sections.ORDER:
        rows = boards[name]
        if not rows:
            continue
        if name == sections.HN:
            print(f"\n== Show HN (≥ {sections.min_score(name)} 分)")
        for rank, s in enumerate(rows, 1):
            dims = " ".join(f"{n}{v}" for n, v in s.dims.items())
            print(f"{rank:>3}. [{s.score}] {s.item_id}  ({s.verdict})")
            print(f"     {dims} · {s.reason}")


def _deliver_dir(env_var: str) -> Path:
    return Path(os.environ.get(env_var) or (_PROJECT_ROOT / "data" / "briefs"))


async def deliver(result, vertical: str, edition: str = "daily") -> None:
    """Write the brief + the static site. Best-effort: a delivery failure
    must not lose the report the run already paid for."""
    from intel.deliver import context as ctx
    from intel.deliver import markdown

    try:
        if result is not None:  # daily only — weekly/monthly have no RunResult
            c = ctx.build_context(result)
            brief_dir = _deliver_dir("TIERLIST_BRIEF_DIR")
            brief_dir.mkdir(parents=True, exist_ok=True)
            md_path = brief_dir / f"{c['date']}-{vertical}.md"
            # The repositories, then the Show HN board: one file per day.
            text = markdown.render_markdown(c)
            hn_text = markdown.render_hn_brief(c)
            md_path.write_text(f"{text}\n\n{hn_text}\n" if hn_text else text,
                               encoding="utf-8", newline="\n")
            print(f"brief: {md_path}", file=sys.stderr)

        dsn = os.environ.get("TIERLIST_PG_DSN", "")
        if not dsn:
            print("site: skipped (TIERLIST_PG_DSN not set)", file=sys.stderr)
        else:
            from intel import editions
            from intel.deliver import site
            from intel.store import db, gain

            conn = await db.connect()
            try:
                await db.ensure_schema(conn)
                # Tiers read each score against its model version's lines.
                from intel import calibrate

                await calibrate.load_lines(conn)
                from intel.i18n import LANGS

                contexts: dict[str, dict[str, dict]] = {}
                if edition == "daily" and features.enabled("funding_page"):
                    for lang in LANGS:
                        stats = await db.edition_stats(conn,
                                                       result.since_date)
                        board = editions.wrap_gain(
                            await gain.gain_rows(conn, 1), "daily")
                        archive = editions.recent_issues(
                            await db.issue_dates(conn), lang=lang)
                        contexts.setdefault("daily", {})[lang] = \
                            editions.daily_context(
                                result, stats=stats, gain_board=board,
                                archive_recent=archive, lang=lang)
                else:
                    for lang in LANGS:
                        ctx = await editions.aggregate_context(
                            conn, edition, vertical, lang=lang)
                        contexts.setdefault(edition, {})[lang] = ctx
                paths = await site.write_site(conn, contexts)
                # The previous issue's "next" link; a failure here must not
                # fail the run, the Sunday rebuild repairs it anyway.
                try:
                    paths += await site.refresh_previous(conn, vertical,
                                                         contexts)
                except Exception as exc:
                    print(f"refresh_previous failed: {exc}", file=sys.stderr)
                for p in paths:
                    print(f"site:  {p}", file=sys.stderr)
            finally:
                await conn.close()
    except Exception as exc:  # delivery is a side channel — warn, keep going
        print(f"deliver failed: {exc}", file=sys.stderr)


async def _rebuild_site(vertical: str, *, force: bool = False) -> int:
    """`--rebuild-site`: every dated permalink plus all shared chrome,
    rendered from the store with today's templates.

    Refreshes `<edition>/index.html` as well, from the newest issue of each
    section. It used to skip them because the daily context carries a
    新仓库热榜 that was not persisted anywhere — and the second half of that
    sentence, "the permalinks have never carried it either, so they lose
    nothing", was simply false: the sweep writes ONE rendering into both the
    rolling copy and the dated permalink, so a permalink did carry the board
    until the first rebuild silently stripped it. The board is stored with
    the run now (intel.runs.hot_new), so neither page loses anything.

    Only pages whose inputs changed are rendered: `deliver.manifest` keeps
    a fingerprint per page and `deliver.site.write_history` renders the ones
    that no longer match, which is what keeps this weekly job from costing
    O(history) forever. `force=True` (--force-render) renders everything;
    a template change is caught by the code fingerprint on its own, so the
    flag is for a change no fingerprint can see.

    Reads the store and writes files; makes no network call and no LLM call,
    and pushes nothing.
    """
    dsn = os.environ.get("TIERLIST_PG_DSN", "")
    if not dsn:
        print("rebuild: TIERLIST_PG_DSN not set", file=sys.stderr)
        return 1

    from intel.deliver import site
    from intel.store import db

    conn = await db.connect()
    try:
        await db.ensure_schema(conn)
        from intel import calibrate

        await calibrate.load_lines(conn)
        stats: dict = {}
        # False from the CLI must not mask TIERLIST_RENDER_FORCE: pass None
        # so Manifest.load still reads the env, True so --force-render wins.
        issues = await site.write_history(conn, vertical,
                                          force=force or None,
                                          stats=stats)
        # Empty contexts on purpose: write_site then refreshes only the
        # shared chrome — style.css, feeds, the search index, /search/,
        # /archive/, /tags/, robots and the sitemap — and skips the landing
        # and every <edition>/index.html for want of a context.
        chrome = await site.write_site(conn, {}, force=force or None)
    finally:
        await conn.close()

    for path in issues + chrome:
        print(f"site:  {path}", file=sys.stderr)
    print(f"rebuild: {len(issues)} issue pages "
          f"({stats.get('skipped', 0)} of {stats.get('pages', 0)} unchanged, "
          f"skipped), {len(chrome)} chrome files")
    return 0


async def _backfill_date(vertical: str, day_str: str, *, force: bool,
                         as_json: bool) -> int:
    """`--date`: rescore one stored day and re-render the pages it feeds.

    Reads the store, calls the scorer once, writes scores and the rollup back
    under that date, then hands rendering to the same `write_history` pass
    `--rebuild-site` uses. Nothing is fetched and nothing is pushed: the
    recovered day belongs on the site, not in a channel that already moved on.
    """
    from intel.backfill import rescore_day
    from intel.deliver import site
    from intel.store import db

    try:
        day = date.fromisoformat(day_str)
    except ValueError:
        print(f"--date: not a date: {day_str}", file=sys.stderr)
        return 2

    cfg = load_config(_PROJECT_ROOT / "config.yaml")
    rubric = load_rubric(vertical)
    try:
        result = await rescore_day(cfg, rubric, day, force=force)
    except RuntimeError as exc:
        print(f"backfill: {exc}", file=sys.stderr)
        return 1

    conn = await db.connect()
    try:
        pages = await site.write_history(conn, vertical)
        # The chrome too, not just the issue: /archive/, the search index and
        # the feeds are what make a recovered day reachable. A page nothing
        # links to is a page nobody reads.
        pages += await site.write_site(conn, {})
    finally:
        await conn.close()
    for path in pages:
        print(f"site:  {path}", file=sys.stderr)

    payload = {
        "date": day.isoformat(), "vertical": result.vertical,
        "cohort": result.cohort, "candidates": result.candidates,
        "already_scored": result.already_scored, "scored": result.scored,
        "day_total": result.day_total, "pages": len(pages),
        "usage": result.usage,
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"backfill {day}: cohort {result.cohort} · candidates "
              f"{result.candidates} · scored {result.scored} "
              f"(skipped {result.already_scored} already judged) · "
              f"day now holds {result.day_total} · {len(pages)} pages")
        usage = result.usage or {}
        if usage:
            print(f"  tokens: prompt {usage.get('prompt_tokens')} · "
                  f"completion {usage.get('completion_tokens')} · "
                  f"total {usage.get('total_tokens')}")
    return 0


async def _main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="intel.run",
                                     description="tierlist daily radar")
    parser.add_argument("--vertical", default="dev_tools")
    parser.add_argument("--edition", default="daily",
                        choices=("daily", "weekly", "monthly"),
                        help="daily fetches+scores; weekly/monthly aggregate "
                             "the store with no fetch and no LLM")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output instead of the report")
    parser.add_argument("--no-deliver", action="store_true",
                        help="skip the M3 deliver layer entirely "
                             "(no files, no push)")
    parser.add_argument("--rebuild-site", action="store_true",
                        help="re-render every past issue and all chrome from "
                             "Postgres with the current templates; no fetch, "
                             "no LLM, no push. Run this after a redesign — "
                             "dated pages are otherwise frozen at the markup "
                             "of the day they were generated while style.css "
                             "is overwritten on every sweep")
    parser.add_argument("--force-render", action="store_true",
                        help="with --rebuild-site: render every page even if "
                             "its inputs are unchanged (same as "
                             "TIERLIST_RENDER_FORCE=1)")
    parser.add_argument("--date", metavar="YYYY-MM-DD",
                        help="rescore that date from the store and re-render "
                             "its page: no fetch, no push, one LLM call over "
                             "the cohort the day recorded. Recovers a day "
                             "whose scoring stage failed; items that date "
                             "already scored are skipped unless --rescore")
    parser.add_argument("--rescore", action="store_true",
                        help="with --date: judge the whole cohort again, "
                             "including items that date already scored")
    args = parser.parse_args(argv)

    if args.rebuild_site:
        return await _rebuild_site(args.vertical, force=args.force_render)

    if args.date:
        return await _backfill_date(args.vertical, args.date,
                                    force=args.rescore, as_json=args.json)

    cfg = load_config(_PROJECT_ROOT / "config.yaml")
    rubric = load_rubric(args.vertical)
    if args.edition == "daily":
        result = await run_vertical(cfg, rubric)
    else:
        result = None

    if not args.no_deliver:
        await deliver(result, args.vertical, args.edition)

    if args.edition != "daily":
        print(f"{args.edition}: aggregated from store, delivered")
        return 0

    if args.json:
        print(json.dumps(result_to_dict(result), ensure_ascii=False, indent=2))
    else:
        _print_human(result, rubric)
    return 0


def main() -> int:
    # Repo descriptions are user text in any script; on the Windows console the
    # default cp1252 would raise mid-report. Same guard tools/env_check.py uses.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(_PROJECT_ROOT / ".env")
    try:
        return aio.run(_main_async(sys.argv[1:]))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # CLI boundary — report, don't traceback-spam
        print(f"tierlist run failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
