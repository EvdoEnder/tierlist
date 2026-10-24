"""Smoke test for the preview harness — the one path that renders EVERY page
type without a database.

Why this exists: `tests/preview_site.py` is the only way to look at the site
while developing, and being a manual script with no `test_` prefix, nothing
collected it. It sat broken (a lede key that production always supplies was
missing from the fixture) and no run ever said so. A tool used to verify the
front end has to be verified itself, or a green suite means the templates are
fine while the only thing that can show them is dead.

The assertions are deliberately shallow — page renders, chrome is present,
per-language wiring is not crossed. Deep template behaviour is covered by
test_render_site.py; the point here is that the harness still works and that
every page type still has a code path that reaches it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import preview_site  # noqa: E402

# Every page the harness is supposed to produce, relative to the web root.
# A page dropped from the harness fails here rather than quietly ceasing to
# be previewable.
EXPECTED = (
    "index.html", "daily/index.html", "weekly/index.html",
    "monthly/index.html", "news/index.html", "search/index.html",
    "archive/2026-08.html", "tags/index.html", "tags/mcp.html",
    "feed.xml", "style.css",
)


@pytest.fixture(scope="module")
def preview(tmp_path_factory) -> Path:
    return preview_site.main(tmp_path_factory.mktemp("site"))


@pytest.mark.parametrize("rel", EXPECTED)
@pytest.mark.parametrize("prefix", ["", "zh/"])
def test_every_page_renders_in_both_languages(preview, prefix, rel):
    page = preview / prefix / rel
    assert page.exists(), f"harness did not write {prefix}{rel}"
    # 500 bytes is well under the smallest real page and well over the size
    # of a truncated write or a stray error string.
    assert len(page.read_text(encoding="utf-8")) > 500


@pytest.mark.parametrize("prefix,lang_attr", [("", "en"), ("zh/", "zh-CN")])
def test_pages_declare_their_own_language(preview, prefix, lang_attr):
    html = (preview / prefix / "index.html").read_text(encoding="utf-8")
    assert f'<html lang="{lang_attr}">' in html


@pytest.mark.parametrize("prefix,feed_path", [("", "/feed.xml"),
                                              ("zh/", "/zh/feed.xml")])
def test_each_tree_links_its_own_feed(preview, prefix, feed_path):
    """The bug this pins: a hardcoded `/feed.xml` in the shared chrome sent
    one tree's readers to the other tree's channel."""
    html = (preview / prefix / "index.html").read_text(encoding="utf-8")
    assert f'href="{feed_path}"' in html
    other = "/zh/feed.xml" if not prefix else "/feed.xml"
    assert f'href="{other}"' not in html


def test_feeds_are_translated_and_self_referential(preview):
    zh = (preview / "zh" / "feed.xml").read_text(encoding="utf-8")
    en = (preview / "feed.xml").read_text(encoding="utf-8")
    assert "tierlist.tech 晨报" in zh and "tierlist.tech brief" in en
    assert 'href="https://tierlist.tech/zh/feed.xml" rel="self"' in zh
    assert 'href="https://tierlist.tech/feed.xml" rel="self"' in en


@pytest.mark.parametrize("prefix", ["", "zh/"])
def test_pages_carry_discovery_metadata(preview, prefix):
    html = (preview / prefix / "index.html").read_text(encoding="utf-8")
    for needle in ('<meta name="description"', 'rel="canonical"',
                   'property="og:title"', 'name="twitter:card"',
                   'hreflang="x-default"', 'rel="icon"'):
        assert needle in html, f"{prefix}index.html is missing {needle}"


def test_canonical_and_hreflang_point_at_the_right_trees(preview):
    """hreflang must name the OTHER tree; canonical must name THIS one.
    Swapping them is the failure that makes both trees look like one page."""
    zh = (preview / "zh" / "index.html").read_text(encoding="utf-8")
    assert 'rel="canonical" href="https://tierlist.tech/zh/"' in zh
    assert 'hreflang="zh-CN" href="https://tierlist.tech/zh/"' in zh
    assert 'hreflang="en" href="https://tierlist.tech/"' in zh
    assert 'hreflang="x-default" href="https://tierlist.tech/"' in zh


def test_root_assets_are_language_free(preview):
    assert (preview / "favicon.svg").read_text(encoding="utf-8").startswith(
        "<svg")
    assert not (preview / "zh" / "favicon.svg").exists()


def test_robots_allows_crawlers_and_names_the_sitemap(preview):
    robots = (preview / "robots.txt").read_text(encoding="utf-8")

    assert "Allow: /" in robots
    assert "Disallow:" not in robots
    assert "Sitemap: https://tierlist.tech/sitemap.xml" in robots


def test_sitemap_lists_both_trees_and_no_assets(preview):
    xml = (preview / "sitemap.xml").read_text(encoding="utf-8")
    assert "<loc>https://tierlist.tech/</loc>" in xml
    assert "<loc>https://tierlist.tech/zh/</loc>" in xml
    assert "<loc>https://tierlist.tech/tags/</loc>" in xml
    # Assets are not pages: a sitemap advertising them wastes crawl budget
    # on files with no readable content.
    for asset in ("style.css", "search.js", "search-index.json", "feed.xml"):
        assert asset not in xml


@pytest.mark.parametrize("page,expected", [
    ("index.html", "/zh/"),
    ("news/index.html", "/zh/news/"),
    ("search/index.html", "/zh/search/"),
    ("tags/mcp.html", "/zh/tags/mcp.html"),
    ("archive/2026-08.html", "/zh/archive/2026-08.html"),
    ("weekly/index.html", "/zh/weekly/2026-W34.html"),
])
def test_language_link_points_at_the_same_page(preview, page, expected):
    """Not the other tree's root. `weekly/` is here because the harness once
    rendered issues without a page_path and silently fell back to it."""
    html = (preview / page).read_text(encoding="utf-8")
    assert f'class="lang" href="{expected}"' in html
