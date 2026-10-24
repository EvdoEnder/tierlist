"""Render-layer tests: landmarks per page type, zero-JS, autoescape."""
from __future__ import annotations

from intel.render import site


def _entry(rank="01", name="owner/repo", score=62, watch=True, tags=None):
    return {
        "rank": rank, "name": name,
        "url": f"https://github.com/{name}", "stars": 473, "lang": "Zig",
        "score": score,
        "dims": {"novelty": 20, "utility": 22, "momentum": 18,
                 "commercial": 16},
        "description": "A native MCP plugin for x64dbg.",
        "reason": "解决逆向工程与 AI 结合的痛点，部署简单。",
        "tags": tags if tags is not None else ["mcp", "逆向"],
        "is_watch": watch, "high_score": score >= 70,
    }


def _ctx(edition="daily", name="owner/repo"):
    ready_rows = [{"rank": "01", "name": "a/b", "url": "u", "lang": "Rust",
                   "stars_now": 900, "gain": 120}]
    daily_only = {"fetched": 300, "candidates": 50, "sent": 3, "scored": 50,
                  "model": "vision-exp", "cost": "$0.0039"}
    lede = {"window": "2026-08-20", "window_from": "2026-08-21",
                 "window_to": "2026-08-23", "new_count": 50,
            "watch_min": 60, "top_score": 62,
            **(daily_only if edition == "daily"
               else dict.fromkeys(daily_only))}
    return {
        "edition": edition,
        "edition_label": {"daily": "开发者工具晨报", "weekly": "开发者工具周报",
                          "monthly": "开发者工具月报"}[edition],
        "date": "2026-08-23",
        "period_label": {"daily": "2026-08-23",
                         "weekly": "2026-W34 · 08-17 → 08-23",
                         "monthly": "2026-08"}[edition],
        "nav": _nav(), "archive_recent": {},
        "lang": "zh", "p": "", "news_rows": None,
        "lede": lede,
        "entries": [_entry(name=name)],
        "gain_board": {"label": "今日 star 增速榜", "window_days": 1,
                       "ready": True, "need": 2, "have": 3,
                       "rows": ready_rows},
        "hot_new": [{"rank": "01", "name": "MengTo/threeui",
                     "url": "u", "stars": 2700,
                     "description": "完整描述不截断 " * 5}],
    }


def _nav():
    return {"daily": "/daily/", "weekly": "/weekly/", "monthly": "/monthly/",
            "news": "/news/", "archive": "/archive/", "tags": "/tags/",
            "search": "/search/"}


def test_issue_daily_landmarks():
    html = site.render_issue(_ctx())
    # `e-verdict` and the tier badge stand in for the piece of the row that
    # is pure presentation and so has no other landmark. The raw score must
    # not reach the page at all: readers see the tier only.
    assert '>62<' not in html and '>62</span>' not in html
    for needle in ('class="e-mark"', 'class="tm tm-a"',
                   '+120', '完整描述不截断',
                   '#mcp', '/daily/"', "今日精选"):
        assert needle in html, needle


def test_weekly_omits_daily_only_bits():
    html = site.render_issue(_ctx("weekly"))
    assert "开发者工具周报" in html and "本周精选" in html
    assert "抓取" not in html.split("本周精选")[0]  # lede omits fetched


def test_gain_board_pending_state():
    ctx = _ctx()
    ctx["gain_board"] = {"label": "七日 star 增速榜", "window_days": 7,
                         "ready": False, "need": 8, "have": 3, "rows": None}
    html = site.render_issue(ctx)
    assert "数据积累中" in html and "现有 3 天" in html


def test_landing_cross_section():
    recent = {"daily": [{"date": "2026-08-23", "url": "/daily/2026-08-23.html"}],
              "weekly": [], "monthly": []}
    html = site.render_landing(_ctx(), recent)
    assert "三个专区" in html and "/daily/2026-08-23.html" in html


def _sections(cells):
    return [{"section": s, "cells": cells if s == "daily" else []}
            for s in ("daily", "weekly", "monthly")]


def test_archive_page_grid():
    html = site.render_archive_page(
        "2026-08", _sections([{"day": 23, "url": "/d.html"}]),
        prev_url="/archive/2026-07.html", next_url="/archive/2026-09.html")
    assert 'class="d has" href="/d.html">23' in html
    # 4 leading pads per section (Aug 2026 starts Saturday) x 3 sections.
    assert html.count('class="pad"') >= 12
    assert "上个月" in html and "下个月" in html


def test_archive_page_omits_a_neighbour_that_does_not_exist():
    """The renderer used to invent the adjacent calendar month, so the
    earliest archive page linked to a month that never had an issue —
    /archive/2026-08.html shipped a 404 to /archive/2026-07.html."""
    html = site.render_archive_page("2026-08", _sections([]))

    assert "上个月" not in html and "下个月" not in html
    assert "2026-07" not in html and "2026-09" not in html


def test_archive_page_links_only_the_neighbour_it_was_given():
    html = site.render_archive_page("2026-08", _sections([]),
                                    prev_url="/archive/2026-06.html")
    assert 'href="/archive/2026-06.html"' in html
    assert "下个月" not in html


def test_archive_page_is_one_document():
    """The three sections used to be three concatenated <!DOCTYPE html>
    pages, so the masthead, nav and footer each appeared three times."""
    html = site.render_archive_page("2026-08", _sections([]))
    assert html.lower().count("<!doctype html>") == 1
    assert html.count("<nav") == 1 and html.count("</html>") == 1
    assert html.count('class="cal"') == 3


def test_tags_index_tiers():
    tags = [{"tag": "mcp", "count": 12, "url": "/tags/mcp.html"},
            {"tag": "cli", "count": 2, "url": "/tags/cli.html"}]
    html = site.render_tags_index(tags)
    assert 'class="t3"' in html and 'class="t1"' in html
    assert "#mcp" in html


def test_tags_index_puts_topics_under_their_groups():
    """Topics sit under their vocabulary group, most used first; a group
    with no used topic is left out."""
    tags = [{"tag": "cli", "count": 3, "url": "/tags/cli.html"},
            {"tag": "mcp", "count": 9, "url": "/tags/mcp.html"},
            {"tag": "llm", "count": 20, "url": "/tags/llm.html"}]
    html = site.render_tags_index(tags, lang="en")
    assert html.count('class="tgroup-h"') == 2
    ai, dev = html.index(">AI</h3>"), html.index(">Developer tools</h3>")
    assert ai < html.index("#LLMs") < html.index("#MCP") < dev < html.index("#CLI")
    assert ">Games</h3>" not in html


def test_tags_index_lists_names_a_to_z_under_letters():
    """Entities are not in any group: they go A to Z under letter headings,
    digits after the letters, each heading reachable from the jump bar."""
    tags = [{"tag": "valorant", "count": 15, "url": "/tags/valorant.html"},
            {"tag": "3ds", "count": 2, "url": "/tags/3ds.html"},
            {"tag": "ffmpeg", "count": 7, "url": "/tags/ffmpeg.html"},
            {"tag": "fortnite", "count": 6, "url": "/tags/fortnite.html"}]
    html = site.render_tags_index(tags)
    assert 'class="tgroups"' not in html
    assert html.index("#ffmpeg") < html.index("#fortnite") < html.index("#valorant") \
        < html.index("#3ds")
    for anchor in ("n-f", "n-v", "n-digits"):
        assert f'href="#{anchor}"' in html and f'id="{anchor}"' in html


def test_tag_page_unrated_row_has_no_ring():
    """A tagged item that was never rated carries tier None and no score.
    It gets no tier ring; the word is there for screen readers only."""
    items = [{"name": "a/b", "url": "u", "tier": None,
              "date": "2026-08-23", "description": "x"}]
    html = site.render_tag_page("mcp", items)
    assert '<span class="vh">未评级</span>' in html
    assert 'class="sc' not in html


def test_tag_page_rows():
    items = [{"name": "a/b", "url": "u", "score": 80,
              "date": "2026-08-23", "description": "一句话"}]
    html = site.render_tag_page("mcp", items)
    assert "#mcp" in html and 'class="sc hi">S<' in html and "一句话" in html


def test_no_script_tag_on_a_page_with_nothing_to_drive():
    """A page carries script only when it has behaviour: no pager chain (no
    neighbours passed) and no search box here, so beyond the opening splash
    every page carries, only a daily page's since.js may appear."""
    pages = [site.render_issue(_ctx()),
             site.render_landing(_ctx(), {}),
             site.render_archive_page("2026-08", _sections([])),
             site.render_tags_index([]),
             site.render_tag_page("x", [])]
    for page in pages:
        # A daily page drives one behaviour, the returning-reader marker.
        scripts = page.lower().count("<script")
        assert "/intro.js?v=" in page
        assert scripts == (2 if "since.js" in page else 1)


def _paged(edition="daily"):
    return {**_ctx(edition),
            "pager": {"prev": {"url": "/daily/2026-08-21.html",
                               "label": "2026-08-21"},
                      "next": {"url": "/daily/2026-08-25.html",
                               "label": "2026-08-25"}}}


def test_issue_pager_links_both_neighbours():
    html = site.render_issue(_paged())
    assert 'href="/daily/2026-08-21.html" rel="prev"' in html
    assert 'href="/daily/2026-08-25.html" rel="next"' in html
    assert "2026-08-21" in html and "2026-08-25" in html


def test_issue_pager_declares_neighbours_in_the_head():
    """<link rel="prev|next"> is what static/pager.js reads — the visible
    anchors are for the mouse, these are the keyboard's copy."""
    html = site.render_issue(_paged())
    assert '<link rel="prev" href="/daily/2026-08-21.html">' in html
    assert '<link rel="next" href="/daily/2026-08-25.html">' in html


def test_issue_pager_ships_the_key_handler_as_a_file_not_inline():
    """`default-src 'self'` with no `unsafe-inline`: an inline handler is
    dropped by the browser silently, so the arrows would simply do nothing."""
    html = site.render_issue(_paged())
    assert '<script src="/pager.js" defer></script>' in html
    assert "addEventListener" not in html      # no inline copy of the script


def test_issue_pager_zh_tree_loads_its_own_copy_of_the_script():
    ctx = {**_paged(), "lang": "zh", "p": "/zh",
           "nav": {k: "/zh" + v for k, v in _nav().items()}}
    html = site.render_issue(ctx)
    assert '<script src="/zh/pager.js" defer></script>' in html


def test_issue_without_neighbours_says_so_instead_of_collapsing():
    """Both ends keep their cell: a missing link reads as 'no link' rather
    than 'no such issue', and the row would reflow between pages."""
    html = site.render_issue({**_ctx(), "pager": {"prev": None, "next": None}})
    assert "已是最早一期" in html and "已是最新一期" in html
    assert 'rel="prev"' not in html and 'rel="next"' not in html
    assert "pager.js" not in html              # nothing for the keys to do
    # ...and no key hint either: a shortcut advertised on a page where it
    # does nothing is worse than no hint.
    assert "键翻页" not in html


def test_a_lone_month_advertises_no_shortcut():
    html = site.render_archive_page("2026-08", _sections([]))
    assert "键翻页" not in html


def test_archive_page_joins_the_key_chain_too():
    html = site.render_archive_page("2026-08", _sections([]),
                                    prev_url="/archive/2026-07.html",
                                    next_url=None)
    assert '<link rel="prev" href="/archive/2026-07.html">' in html
    assert '<link rel="next"' not in html
    assert '<script src="/zh/pager.js" defer></script>' in html


def test_autoescape_survives_hostile_name():
    html = site.render_issue(_ctx(name="<script>x</script>"))
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_search_page_ships_i18n_and_index_hook():
    ctx = _ctx()
    html = site.render_search_page(ctx)
    assert "sr-q" in html and "sr-list" in html
    assert "search-index.json" in html and "search.js" in html
    assert "data-index" in html             # i18n + index URL ride body attrs
    assert "window.SR_I18N" not in html     # inline script would die on CSP
    assert "搜索" in html                     # zh chrome


def test_search_page_en_variant():
    en = {**_ctx(), "lang": "en", "p": "",
          "edition_label": "Search", "page_path": "/search/"}
    html = site.render_search_page(en)
    assert '"/search-index.json' in html and '"/search.js' in html
    assert "project / tag / owner" in html       # en placeholder


def test_language_switch_lands_on_the_same_page_not_the_root():
    """The masthead's language link used to be a hand-written constant in six
    places, all of them the other tree's ROOT — clicking it from a tag page
    threw the reader back to the landing page. It is now derived from the
    page's path, so it lands on that page's counterpart."""
    zh = site.render_tag_page("mcp", [], lang="zh")
    en = site.render_tag_page("mcp", [], lang="en")
    assert 'class="lang" href="/tags/mcp.html"' in zh
    assert 'class="lang" href="/zh/tags/mcp.html"' in en

    # The landing page's counterpart IS the other root, and still resolves.
    ctx = _ctx()
    assert 'class="lang" href="/"' in site.render_landing(ctx, {})


def test_headline_renders_on_a_real_aggregate_context():
    """The lede's one sentence must render on the contexts the site actually
    builds, not only on the one a live sweep produces.

    It first keyed off `lede.fetched`, which build_aggregate_context sets to
    None unconditionally — so the sentence appeared on no page of the live
    site: not the landing (built by the news cron from an aggregate), not
    weekly, not monthly. A feature that renders nowhere fails the same way a
    deleted one does, except it still looks present in the template.
    """
    from datetime import date

    from intel.editions import build_aggregate_context

    rows = [{"item_id": "a/b", "score": 80, "dims": {"novelty": 20},
             "reason": "why", "verdict": "watch", "name": "a/b",
             "url": "https://github.com/a/b", "lang": "Rust",
             "description": "一段描述", "stars": 100, "tags": []}]
    ctx = build_aggregate_context(
        "weekly", "dev_tools", date(2026, 8, 23), rows=rows,
        stats={"new_count": 33, "scored_count": 21},
        gain_board={"label": "七日", "window_days": 7, "ready": True,
                    "rows": []},
        archive_recent={"daily": [], "weekly": [], "monthly": []})
    ctx["nav"] = _nav()
    assert ctx["lede"]["fetched"] is None, "the shape this guards against"

    html = site.render_issue(ctx)
    assert 'class="ed-lede"' in html
    assert "33" in html and "1 个进了榜" in html


def test_lede_prints_the_window_as_an_inclusive_range():
    """An exclusive bound ("创建 > 09-14" on a 09-17 page) reads as the
    issue's date. The lede states both ends of what the board covers."""
    html = site.render_issue(_ctx())
    assert "&amp;" not in html
    assert '收录新建 <span class="num">2026-08-21 – 2026-08-23</span>' in html
    weekly = site.render_issue(_ctx("weekly"))
    assert '统计窗口 <span class="num">2026-08-21 – 2026-08-23</span>' in weekly


# ------------------------------------------------- tags read per language

def test_a_tag_reads_in_the_pages_own_language_from_one_url():
    """The site has been bilingual since 2026-08-24 while its tags were a
    single set, so the English tree printed Chinese tags. The slug is the
    URL and the label is per language, so both trees share /tags/crack.html
    and neither shows the other's words."""
    cloud = [{"tag": "crack", "count": 17, "url": "/tags/crack.html"}]

    zh = site.render_tags_index(cloud, lang="zh")
    en = site.render_tags_index(cloud, lang="en")

    assert "#破解" in zh and "#Crack" not in zh
    assert "#Crack" in en and "#破解" not in en
    assert 'href="/tags/crack.html"' in zh
    assert 'href="/tags/crack.html"' in en


def test_an_entity_tag_is_not_translated():
    """Proper nouns have no label in the vocabulary and must fall back to
    the slug — inventing a Chinese name for ffmpeg would be worse than
    leaving it alone."""
    cloud = [{"tag": "ffmpeg", "count": 1, "url": "/tags/ffmpeg.html"}]

    for lang in ("zh", "en"):
        assert "#ffmpeg" in site.render_tags_index(cloud, lang=lang)


def test_the_tag_page_masthead_is_labelled_too():
    html = site.render_tag_page("crack", [], lang="zh")
    assert "#破解" in html
    # The footer still names the file on disk, which is the slug.
    assert "/tags/crack.html" in html


def test_stylesheet_and_search_script_urls_carry_a_content_hash():
    """A cached copy of an asset can be answered without reaching the origin
    (a CDN or the browser), which paired the 2026-09-29 markup with the
    previous stylesheet. The link carries a hash of the file's content, so a
    changed file is a URL no cache holds."""
    import hashlib

    want = hashlib.sha256(site.style_css().encode("utf-8")).hexdigest()[:10]
    html = site.render_issue(_ctx())
    assert f'href="/style.css?v={want}"' in html
    search = site.render_search_page(_ctx())
    assert f'/search.js?v={site.asset_versions()["search"]}"' in search


def _trending():
    return {"daily": [{"rank": "01", "name": "morluto/rea",
                       "url": "https://github.com/morluto/rea",
                       "stars": 62573, "gain": 25784, "tags": ["mcp"]}],
            "weekly": [{"rank": "01", "name": "boykopovar/AnyPS5",
                        "url": "https://github.com/boykopovar/AnyPS5",
                        "stars": 24867, "gain": 15539, "tags": []}]}


def test_trending_boards_render_on_the_issue_and_the_landing():
    ctx = {**_ctx(), "trending": _trending()}
    recent = {"daily": [], "weekly": [], "monthly": []}
    for html in (site.render_issue(ctx), site.render_landing(ctx, recent)):
        assert "GitHub 今日热门" in html and "GitHub 本周热门" in html
        assert "不按涨星" in html     # the order is GitHub's, and says so
        assert "GitHub 本月热门" not in html      # no monthly rows, no panel
        assert "morluto/rea" in html and "25784" in html
        assert 'href="/tags/mcp.html"' in html


def test_no_trending_renders_no_trending_panels():
    html = site.render_issue(_ctx())
    assert "GitHub 今日热门" not in html and "boards trending" not in html


def test_every_page_links_the_icon_kit():
    """The .ico for clients without SVG icons, the SVG for everyone else, the
    home-screen icon and the manifest, all at the root."""
    html = site.render_tags_index([])
    for link in ('<link rel="icon" href="/favicon.ico"',
                 '<link rel="icon" href="/favicon.svg" type="image/svg+xml">',
                 '<link rel="apple-touch-icon" href="/apple-touch-icon.png">',
                 '<link rel="manifest" href="/site.webmanifest">'):
        assert link in html


def test_the_icon_files_are_what_their_names_say():
    """The kit is committed as files, so a broken export would only show as
    a missing icon in someone's browser."""
    import json

    from intel.deliver import site as deliver_site

    static = deliver_site._STATIC
    ico = (static / "favicon.ico").read_bytes()
    assert ico[:4] == b"\x00\x00\x01\x00" and int.from_bytes(ico[4:6], "little") == 3
    for name in ("apple-touch-icon.png", "icon-192.png", "icon-512.png"):
        assert (static / name).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", name
    manifest = json.loads((static / "site.webmanifest").read_text(encoding="utf-8"))
    for icon in manifest["icons"]:
        assert icon["src"].lstrip("/") in deliver_site.ICON_FILES
        assert (static / icon["src"].lstrip("/")).exists()
    # The tab icon is the 16 px cut, drawn on a 16-unit grid.
    assert 'viewBox="0 0 16 16"' in deliver_site.FAVICON_SVG


def _en_ctx():
    ctx = _ctx()
    ctx.update(lang="en", edition_label="Dev Tools Daily")
    ctx["entries"] = [_entry(), {**_entry(name="b/c", score=40), "lang": "语言未知",
                                 "description": "一个中文描述"}]
    ctx["hot_new"] = [{"rank": "01", "name": "x/y", "url": "https://h/x",
                       "stars": 9, "description": "热榜的中文描述"}]
    return ctx


def test_the_english_page_shows_no_chinese_chrome():
    html = site.render_issue(_en_ctx())
    assert "增速榜" not in html and "Star gain" in html
    assert "语言未知" not in html and "Language unknown" in html


def test_the_english_page_shows_english_descriptions_where_there_are_any():
    site.use_descriptions_en({"https://github.com/b/c": "A Chinese description",
                              "https://h/x": "The hot board description"})
    try:
        en = site.render_issue(_en_ctx())
        zh = site.render_issue({**_en_ctx(), "lang": "zh"})
    finally:
        site.use_descriptions_en({})
    assert "A Chinese description" in en and "一个中文描述" not in en
    assert "The hot board description" in en
    assert "一个中文描述" in zh and "A Chinese description" not in zh
