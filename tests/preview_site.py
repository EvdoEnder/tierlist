"""Manual preview generator — NOT collected by pytest (no test_ prefix).

Run:  .venv/Scripts/python.exe tests/preview_site.py
Writes a full BILINGUAL fixture site (en at root, zh under /zh/) to
%TEMP%/opencode/site_preview/.
"""
import os
import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from intel.deliver import site as deliver_site  # noqa: E402
from intel.render import site  # noqa: E402

LANGS = ("zh", "en")


def _p(lang):
    return "/zh" if lang == "zh" else ""


def _pager(edition, prev, nxt, lang):
    p = _p(lang)

    def _side(slug):
        return None if slug is None else {
            "url": f"{p}/{edition}/{slug}.html", "label": slug}

    return {"prev": _side(prev), "next": _side(nxt)}


def _nav(lang):
    p = _p(lang)
    return {"daily": f"{p}/daily/", "weekly": f"{p}/weekly/",
            "monthly": f"{p}/monthly/", "news": f"{p}/news/",
            "archive": f"{p}/archive/", "tags": f"{p}/tags/",
            "search": f"{p}/search/"}


def _entry(rank, name, score, watch, stars, lang, reason, tags=None):
    return {"rank": rank, "name": name,
            "url": f"https://github.com/{name}", "stars": stars,
            "lang": lang, "score": score,
            "dims": {"novelty": 20, "utility": 22, "momentum": 25,
                     "commercial": 16},
            "description": "x64dbg-MCP Server is a native MCP plugin that "
                           "exposes the debugger's full functionality over "
                           "HTTP — set breakpoints, step, read memory.",
            "reason": reason, "tags": tags or ["mcp", "逆向", "安全工具"],
            "is_watch": watch, "high_score": score >= 70}


def _hn_entry(rank, name, score, points, reason):
    """A Show HN card: points, no language, HN badge — the second board."""
    return {"rank": rank, "name": name,
            "url": f"https://news.ycombinator.com/item?id={rank}",
            "stars": points, "lang": None, "score": score,
            "source": "hn", "badge": "HN", "metric": f"▲{points}",
            "dims": {"novelty": 20, "utility": 22, "momentum": 22,
                     "commercial": 15},
            "description": "An open model gateway that routes each call to "
                           "the cheapest model clearing the bar.",
            "reason": reason, "tags": ["llm", "网关"],
            "is_watch": True, "high_score": score >= 70}


def _news_rows(lang):
    n = [("*New model lets you edit video in real time*",
          "https://techcrunch.com/example", "techcrunch", 0, "08-24 09:12",
          "A preview of editing video with a language-model pipeline — the "
          "promptable video editor every tool person wants."),
         ("Show HN: A fast file indexer in Rust",
          "https://news.ycombinator.com/item?id=1", "HN", 214, "08-24 08:45",
          "Cross-platform Windows-first search. 100k files in 0.3s."),
         ("Ars Technica: how AI coding assistants change architecture",
          "https://arstechnica.com/example", "arstechnica", 0, "08-24 07:30",
          "Team topologies for the AI-assisted decade.")]
    if lang == "en":
        return [{"title": t, "url": u, "source": s, "points": p,
                 "summary": d, "published": dte}
                for t, u, s, p, dte, d in n]
    return [{"title": "(en fixture, zh page should not show it visibly)",
             "url": u, "source": s, "points": p, "summary": d,
             "published": dte} for t, u, s, p, dte, d in n]


def _ctx(edition, lang):
    labels = {"zh": {"daily": "开发者工具晨报", "weekly": "开发者工具周报",
                     "monthly": "开发者工具月报"},
              "en": {"daily": "Dev Tools Daily", "weekly": "Dev Tools Weekly",
                     "monthly": "Dev Tools Monthly"}}[lang]
    periods = {"daily": "2026-08-23", "weekly": "2026-W34 · 08-17 → 08-23",
               "monthly": "2026-08"}
    return {
        "edition": edition,
        "edition_label": labels[edition],
        "date": "2026-08-23",
        "period_label": periods[edition],
        "nav": _nav(lang),
        "lang": lang, "p": _p(lang),
        "news_rows": _news_rows(lang) if edition == "daily" else None,
        "lede": {"window": "2026-08-20", "window_from": "2026-08-21",
                 "window_to": "2026-08-23", "fetched": 300,
                 "candidates": 50, "sent": 3, "scored": 50,
                 "model": "deepseek-v4-flash-vision-exp", "cost": "$0.0039",
                 "new_count": 50, "watch_min": 60, "top_score": 80},
        "entries": [
            _entry("01", "duty1g/x64dbg-mcp-server", 80, True, 569, "Zig",
                   "将 MCP 协议引入逆向工程领域，让 AI 助手能直接操控 x64dbg，"
                   "极大提升调试自动化能力。Zig 实现零依赖、单二进制，部署简单。"
                   "商业化路径是成为 AI 辅助逆向工程的标准工具。风险：竞品生态。"),
            _entry("02", "MengTo/threeui", 72, True, 2700, "HTML",
                   "解决 React Three Fiber 生态缺少交互组件库的问题。两天近 3k "
                   "star 说明社区需求强烈。风险：Three.js 生态变化快。"),
            _entry("03", "zhaoxuya520/MeshLAN", 69, True, 147, "Go",
                   "P2P 虚拟局域网，自托管优先，零配置组网。"),
            _entry("04", "josiah-nelson/eidos", 66, True, 164, "Rust",
                   "Windows 优先的文件系统索引与搜索服务。"),
        ],
        "hn_entries": [
            _hn_entry("01", "Show HN: We built an open model gateway", 84, 215,
                      "开源模型网关，通过追踪与模拟评估自动选择最优模型，"
                      "消除供应商锁定并降低推理成本。"),
            _hn_entry("02", "Show HN: SubSmith — videos into study material",
                      66, 71,
                      "把视频/音频转成语言学习材料，本地转写并导出 Anki。"),
        ],
        "hn_min_score": 55,
        "gain_board": {"label": ("今日 star 增速榜" if lang == "zh"
                                 else "Star Gain · daily"),
                       "window_days": 1, "ready": True, "need": 2, "have": 2,
                       "rows": [{"rank": "01", "name": "MengTo/threeui",
                                 "url": "u", "lang": "HTML",
                                 "stars_now": 2700, "gain": 412},
                                {"rank": "02", "name": "duty1g/x64dbg-mcp-server",
                                 "url": "u2", "lang": "Zig",
                                 "stars_now": 569, "gain": 288}]},
        "hot_new": [{"rank": f"{i:02d}", "name": n, "url": "u", "stars": s,
                     "description": d}
                    for i, (n, s, d) in enumerate([
                        ("MengTo/threeui", 2700,
                         "Open-source ThreeUI Community catalog with live "
                         "interactive components and complete source."),
                        ("duty1g/x64dbg-mcp-server", 569,
                         "Native MCP plugin for x64dbg — control the debugger "
                         "programmatically over HTTP."),
                        ("nateherkai/scroll-craft", 412,
                         "Claude Code skill for premium scroll-driven "
                         "websites."),
                        ("Forsy-AI/biosecurity-agent", 337,
                         "AI agent that builds a live biosecurity world "
                         "around any target."),
                    ], 1)],
        "archive_recent": _recent(lang),
    }


def _recent(lang):
    p = _p(lang)
    return {
        "daily": [{"date": d, "url": f"{p}/daily/{d}.html"}
                  for d in ["2026-08-23", "2026-08-22", "2026-08-21"]],
        "weekly": [{"date": "2026-W34", "url": f"{p}/weekly/2026-W34.html"}],
        "monthly": [{"date": "2026-08", "url": f"{p}/monthly/2026-08.html"}],
    }


def main(out: Path | None = None) -> Path:
    """Render every page type of both language trees into `out`.

    Returns the directory so the CI smoke test can assert against it. The
    parameter exists for that test: the harness is the only way to exercise
    the templates without a database, and a harness nothing runs is a harness
    that rots (it did — for a while it raised on a lede key production has
    always supplied, and nobody found out)."""
    if out is None:
        out = Path(os.environ.get("TEMP", "/tmp")) / "opencode" / "site_preview"
    for suffix in ("daily", "weekly", "monthly", "news", "archive", "tags",
                   "search"):
        (out / suffix).mkdir(parents=True, exist_ok=True)
        (out / "zh" / suffix).mkdir(parents=True, exist_ok=True)

    (out / "style.css").write_text(site.style_css(), encoding="utf-8")
    (out / "zh" / "style.css").write_text(site.style_css(), encoding="utf-8")

    for lang in LANGS:
        sub = out / "zh" if lang == "zh" else out
        ctx = _ctx("daily", lang)
        (sub / "index.html").write_text(
            site.render_landing(ctx, _recent(lang)), encoding="utf-8")
        # `page_path` mirrors what deliver.write_site sets: the dated
        # permalink, not the rolling /<edition>/ index. Without it the
        # canonical tag and the language link both fall back to the language
        # root — which is what this harness used to show.
        # A middle-of-the-chain issue, so the preview shows both arrows and
        # the key hint. The neighbours are the fixture's own dates; the real
        # writer takes them from editions.issue_neighbours.
        (sub / "daily" / "index.html").write_text(
            site.render_issue({
                **ctx, "page_path": "/daily/2026-08-23.html",
                "pager": _pager("daily", "2026-08-21", "2026-08-25", lang)}),
            encoding="utf-8")
        (sub / "pager.js").write_text(site.pager_js(), encoding="utf-8")
        # Three dated permalinks so the chain is actually WALKABLE in the
        # preview — ← / → on a page whose neighbours 404 proves the markup
        # and nothing about the behaviour.
        _chain = ["2026-08-21", "2026-08-23", "2026-08-25"]
        for i, day in enumerate(_chain):
            (sub / "daily" / f"{day}.html").write_text(
                site.render_issue({
                    **_ctx("daily", lang), "period_label": day,
                    "date": day, "page_path": f"/daily/{day}.html",
                    "pager": _pager("daily",
                                    _chain[i - 1] if i else None,
                                    _chain[i + 1] if i + 1 < len(_chain)
                                    else None, lang)}),
                encoding="utf-8")

        search_ctx = {
            "lang": lang, "p": _p(lang),
            "edition_label": "Search" if lang == "en" else "搜索",
            "period_label": "search",
            "nav": _nav(lang), "page_path": "/search/",
        }
        (sub / "search" / "index.html").write_text(
            site.render_search_page(search_ctx), encoding="utf-8")
        (sub / "search-index.json").write_text(
            '{"version":"1","refreshed":"2026-08-24 12:00 UTC",'
            '"count":2,"items":[{"id":"github:duty1g/x64dbg-mcp-server",'
            '"name":"duty1g/x64dbg-mcp-server","url":"u","lang":"Zig",'
            '"tags":["mcp","逆向"],"description":"AI 操控调试器",'
            '"tier":"S","when":"2026-08-23"},{"id":"github:MengTo/threeui",'
            '"name":"MengTo/threeui","url":"u","lang":"HTML","tags":["threejs"],'
            '"description":"React Three Fiber 组件库","tier":"A",'
            '"when":"2026-08-23"}]}', encoding="utf-8")
        (sub / "search.js").write_text(
            (Path(__file__).resolve().parent.parent / "intel" / "render"
             / "static" / "search.js").read_text(encoding="utf-8"),
            encoding="utf-8")

        weekly = _ctx("weekly", lang)
        weekly["gain_board"] = {"label": ("七日 star 增速榜" if lang == "zh"
                                          else "Star Gain · 7d"),
                                "window_days": 7, "ready": False,
                                "need": 8, "have": 2, "rows": None}
        (sub / "weekly" / "index.html").write_text(
            site.render_issue({
                **weekly, "page_path": "/weekly/2026-W34.html",
                "pager": _pager("weekly", "2026-W33", None, lang)}),
            encoding="utf-8")
        # The newest month: the far end of the chain, which is how the
        # preview shows the 已是最新一期 cell rather than a collapsed row.
        (sub / "monthly" / "index.html").write_text(
            site.render_issue({**_ctx("monthly", lang),
                               "page_path": "/monthly/2026-08.html",
                               "pager": _pager("monthly", "2026-07", None,
                                               lang)}),
            encoding="utf-8")

        news_ctx = {
            "lang": lang, "p": _p(lang),
            "edition_label": "Live News" if lang == "en" else "实时新闻",
            "period_label": "live", "updated_ts": "09:12",
            "nav": _nav(lang), "page_path": "/news/",
            "news_rows": _news_rows(lang),
        }
        (sub / "news" / "index.html").write_text(
            site.render_news_page(news_ctx), encoding="utf-8")

        cells = [{"day": d, "url": f"/daily/2026-08-{d:02d}.html"
                  if d in (1, 5, 12, 23) else None} for d in range(1, 32)]
        (sub / "archive" / "2026-08.html").write_text(
            site.render_archive_page(
                "2026-08",
                [{"section": s, "cells": cells if s == "daily" else []}
                 for s in ("daily", "weekly", "monthly")],
                lang=lang), encoding="utf-8")

        tags = [{"tag": t, "count": c, "url": f"{_p(lang)}/tags/{t}.html"}
                for t, c in [("mcp", 14), ("ai-agent", 9), ("逆向", 6),
                             ("cli", 4), ("本地优先", 3), ("threejs", 2)]]
        (sub / "tags" / "index.html").write_text(
            site.render_tags_index(tags, lang=lang), encoding="utf-8")
        (sub / "tags" / "mcp.html").write_text(site.render_tag_page(
            "mcp", [{"name": "duty1g/x64dbg-mcp-server", "url": "u",
                     "tier": "S", "date": "2026-08-23",
                     "description": "AI 操控调试器"}],
            lang=lang), encoding="utf-8")

    # One feed per language, plus the root assets the site writer emits.
    for lang in LANGS:
        sub = out / "zh" if lang == "zh" else out
        (sub / "feed.xml").write_text(site.render_feed(
            [{"title": "a/b", "url": "https://github.com/a/b",
              "updated": "2026-08-23T00:00:00Z", "summary": "精选 S 级",
              "tier": "S"}],
            updated="2026-08-23T12:00:00Z",
            site_url="https://tierlist.tech", lang=lang), encoding="utf-8")

    (out / "favicon.svg").write_text(deliver_site.FAVICON_SVG,
                                     encoding="utf-8")
    (out / "robots.txt").write_text(deliver_site.build_robots(),
                                    encoding="utf-8")
    pages = sorted(str(p.relative_to(out).as_posix())
                   for p in out.rglob("*.html"))
    (out / "sitemap.xml").write_text(deliver_site.build_sitemap(
        ["/" + (p[:-len("index.html")] if p.endswith("index.html") else p)
         for p in pages], lastmod="2026-08-23"), encoding="utf-8")

    print(f"preview written: {out}")
    return out


if __name__ == "__main__":
    main()
