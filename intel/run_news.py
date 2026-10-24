"""CLI entry — `python -m intel.run_news`.

The live-news half of tierlist: a 15-minute cron that (1) fetches HN Show
posts + the configured RSS feeds into intel.news (idempotent upsert),
(2) rewrites the /news/ page and the landing page's news panel for BOTH
languages from Postgres. No LLM, no scoring, no Discord push, no
daily-pipeline hooks — this run is cheap, overlaps nothing, and a failure
never puts the daily sweep at risk (the daily run rebuilds the same pages
from the same DB when it runs).

Progress prints to stderr; the one machine-readable line is
`news: fetched N, new M (total T)`.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

from intel import aio
from intel.deliver import site
from intel.i18n import LANGS
from intel.pipeline import load_config
from intel.store import db

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_TIMEOUT_S = 20.0


async def _fetch_all(cfg) -> list:
    """All enabled news feeds → a batch of Items. Every adapter is
    best-effort: one broken feed yields nothing, not a failed run."""
    from intel.sources import hn, rss

    news_cfg = cfg.news
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        items: list = []
        hn_cfg = news_cfg.get("hn") or {}
        if hn_cfg:
            items += await hn.fetch_new_hn(client, hn_cfg)
        rss_cfg = (news_cfg.get("rss") or {}).get("feeds") or {}
        for feed_key, url in rss_cfg.items():
            items += await rss.fetch_feed(client, url, feed_key)
    return items


async def _refresh(conn, cfg, *, limit: int, ticker: int) -> int:
    """Fetch → store → render both languages; returns new rows."""
    from intel.render import site as render

    fetched = await _fetch_all(cfg)
    new_count = await db.upsert_news(conn, fetched)
    print(f"news: fetched {len(fetched)}, new {new_count}", file=sys.stderr)

    written: list[Path] = []
    for lang in LANGS:
        ctxs = await site.render_news(conn, render, lang,
                                      limit=limit, ticker=ticker)
        written.append(site.write_web(
            f"{site.editions.lang_prefix(lang).lstrip('/')}/news/index.html",
            render.render_news_page(ctxs["news"])))
        written.append(site.write_web(
            f"{site.editions.lang_prefix(lang).lstrip('/')}/index.html",
            render.render_landing(ctxs["landing"],
                                  ctxs["landing"]["archive_recent"])))

    total = await db.news_count(conn)
    print(f"news: total {total}", file=sys.stderr)
    for p in written:
        print(f"site:  {p}", file=sys.stderr)
    return new_count


async def _main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="intel.run_news")
    parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(_PROJECT_ROOT / ".env")

    cfg = load_config(_PROJECT_ROOT / "config.yaml")
    dsn = os.environ.get(db.DSN_ENV_VAR, "")
    if not dsn:
        print("news: skipped (TIERLIST_PG_DSN not set)", file=sys.stderr)
        return 1
    limit = int(cfg.news.get("page_limit", 60))
    ticker = int(cfg.news.get("ticker_limit", 6))
    conn = await db.connect()
    try:
        await db.ensure_schema(conn)
        # The home page shows the latest issue, so its tiers need the lines.
        from intel import calibrate

        await calibrate.load_lines(conn)
        await _refresh(conn, cfg, limit=limit, ticker=ticker)
    finally:
        await conn.close()
    return 0


def main() -> int:
    try:
        return aio.run(_main_async(sys.argv[1:]))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"news run failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
