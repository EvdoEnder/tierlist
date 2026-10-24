"""The render API the delivery layer codes against.

Thin template-picking only — every piece of markup lives in
intel/templates/*.j2. The environment is built once per process; the one
custom filters: `tagurl` percent-encodes a tag for use in hrefs while the
file on disk keeps the raw UTF-8 name, and `slashbreak` lets owner/repo wrap
at the slash. `style_css()` hands over the shared
stylesheet (intel/render/static/style.css) so the site writer can emit it
once per web root instead of inlining 10 kB into every page.

Every page receives `t` — i18n.strictly bound to the context's `lang`
(editions sets ctx["lang"]; zh by default) — so a page never renders a
translation from the wrong language. `lang` comes along as a bare variable
because <html lang> needs it too.
"""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup, escape

from intel import features as _features
from intel.i18n import lang_prefix, other_lang
from intel.i18n import switch_url as _switch_url
from intel.i18n import t as _t
from intel.tags import label as _tag_label
from intel.tags import vocabulary as _vocabulary
from intel.tiers import tier as _tier

_STYLE_PATH = Path(__file__).resolve().parent / "static" / "style.css"
_PAGER_JS_PATH = Path(__file__).resolve().parent / "static" / "pager.js"
_SEARCH_JS_PATH = Path(__file__).resolve().parent / "static" / "search.js"
_SINCE_JS_PATH = Path(__file__).resolve().parent / "static" / "since.js"
_INTRO_JS_PATH = Path(__file__).resolve().parent / "static" / "intro.js"


@lru_cache(maxsize=1)
def style_css() -> str:
    """The site-wide stylesheet, cached. Cached content alone is safe — stale
    CSS across a site refresh is a cosmetic risk, not a data one."""
    return _STYLE_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def asset_versions() -> dict[str, str]:
    """Content hashes the pages append to their stylesheet and search script
    (`style.css?v=…`). Pages are served no-cache, but a copy of an asset held
    by a CDN or the browser can still be answered without reaching the
    origin, and a page then pairs new markup with an old stylesheet. A URL
    that changes with the file's content is never in any cache."""
    def h(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return {"style": h(style_css()),
            "search": h(_SEARCH_JS_PATH.read_text(encoding="utf-8")),
            "since": h(since_js()),
            "intro": h(intro_js())}


@lru_cache(maxsize=1)
def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("intel", "templates"),
        autoescape=select_autoescape(("html", "j2")),
    )
    env.filters["tagurl"] = lambda tag: quote(tag)
    env.filters["tier"] = _tier
    env.filters["slashbreak"] = _slashbreak
    return env


def _slashbreak(name: str) -> Markup:
    """owner/repo with a line-break opportunity after the slash, so a long
    name in a narrow board wraps between owner and repository rather than
    in the middle of a word."""
    return Markup(str(escape(name)).replace("/", "/<wbr>"))


def _active(ctx: dict) -> str:
    return ctx.get("edition", "")


def site_url() -> str:
    """Absolute origin of the published site. Every page needs it for its
    canonical/OG/hreflang tags, which name the page from outside the page and
    so cannot be relative. Read per call rather than cached: tests and the
    preview harness set TIERLIST_SITE_URL after this module is imported."""
    return os.environ.get("TIERLIST_SITE_URL",
                          "https://tierlist.tech").rstrip("/")


def repo_url() -> str | None:
    """The public source repository (`repo_url` in config.yaml)."""
    import yaml

    raw = yaml.safe_load((Path(__file__).resolve().parent.parent.parent
                          / "config.yaml").read_text(encoding="utf-8")) or {}
    return raw.get("repo_url")


# {url: English description}, set by the writer before it renders. The
# English pages show it in place of a description in a foreign script.
_DESCRIPTIONS_EN: dict[str, str] = {}


def use_descriptions_en(table: dict[str, str]) -> None:
    _DESCRIPTIONS_EN.clear()
    _DESCRIPTIONS_EN.update(table)


def _desc(url: str | None, text: str | None, lang: str,
          limit: int = 200) -> str:
    """A description in the page's language: the English line on the English
    pages when there is one, cut to the length the original was cut to."""
    if lang == "en" and url in _DESCRIPTIONS_EN:
        en = _DESCRIPTIONS_EN[url]
        return en if len(en) <= limit else en[:limit] + "…"
    return text or ""


def _page_vars(ctx: dict) -> dict:
    """t + lang derived from the context; templates get t() bound to THE
    page's language, so zh and en never swap chrome by accident.

    `page_path` is the page's path WITHOUT the language prefix (the chrome
    adds it per hreflang), defaulting to the language root — a caller that
    does not set it still emits valid absolute URLs, just coarser ones."""
    lang = ctx.get("lang", "zh")
    page_path = ctx.get("page_path", "/")
    return {"ctx": ctx, "lang": lang,
            "asset_v": asset_versions(),
            "site_url": site_url(),
            "page_path": page_path,
            "features": _features.all_enabled(),
            "repo_url": repo_url(),
            # Derived here, never passed in: the language link is a pure
            # function of (lang, page_path) and had drifted into six
            # hand-written copies.
            "switch_url": _switch_url(lang, page_path),
            # Bound to the page's language for the same reason `t` is: a tag
            # is stored as an ASCII slug and displayed per language, so the
            # Chinese site reads 破解 and the English one Crack off the one
            # /tags/crack.html. Before the vocabulary there was no label
            # layer and the English tree printed Chinese tags.
            "tag_label": lambda slug: _tag_label(slug, lang),
            "desc": lambda url, text, limit=200: _desc(url, text, lang, limit),
            "t": lambda key, **fmt: _t(key, lang, **fmt)}


def _chrome(lang: str, *, edition_label: str, page_path: str = "/") -> dict:
    """The minimal ctx a standalone chrome page needs (archive/tags pages
    don't go through editions, so nav is assembled here). The language link
    is NOT part of it — _page_vars derives it from `page_path`."""
    p = lang_prefix(lang)
    return {
        "lang": lang,
        "p": p,
        "edition_label": edition_label,
        "page_path": page_path,
        "nav": {k: f"{p}{v}" for k, v in _NAV_PATHS.items()},
    }


_NAV_PATHS = {"daily": "/daily/", "weekly": "/weekly/",
              "monthly": "/monthly/", "news": "/news/",
              "archive": "/archive/", "tags": "/tags/",
              "search": "/search/"}


def render_landing(ctx_daily: dict, archive_recent: dict) -> str:
    tpl = _env().get_template("landing.html.j2")
    return tpl.render(recent=archive_recent,
                      active=_active(ctx_daily),
                      **_page_vars(ctx_daily))


def render_issue(ctx: dict) -> str:
    """One issue page. `ctx["pager"]`, when present, is
    {"prev": {"url", "label"} | None, "next": ...} from
    `editions.issue_neighbours`; prev_url/next_url are lifted out of it into
    template variables because <link rel="prev"> in the shared chrome is what
    the keyboard handler reads, and the archive pages spell them the same
    way."""
    pager = ctx.get("pager") or {}
    return _env().get_template("issue.html.j2").render(
        active=_active(ctx),
        pager=pager,
        prev_url=(pager.get("prev") or {}).get("url"),
        next_url=(pager.get("next") or {}).get("url"),
        **_page_vars(ctx))


def render_news_page(ctx: dict) -> str:
    """The /news/ page: live timeline of the news feed, newest first."""
    tpl = _env().get_template("news.html.j2")
    return tpl.render(active="news", **_page_vars(ctx))


def since_js() -> str:
    """The returning-reader marker on daily pages, from the static dir."""
    return _SINCE_JS_PATH.read_text(encoding="utf-8")


def intro_js() -> str:
    """The opening splash: when to play it and how it closes."""
    return _INTRO_JS_PATH.read_text(encoding="utf-8")


def pager_js() -> str:
    """The ← / → keyboard handler, shared by issue and archive pages."""
    return _PAGER_JS_PATH.read_text(encoding="utf-8")



def render_docs_page(ctx: dict) -> str:
    """The /docs/ page: how a repository becomes a tier. Every number on it
    comes from the rubric and config.yaml through `ctx.docs`, so the page
    cannot describe a pipeline the code no longer runs."""
    tpl = _env().get_template("docs.html.j2")
    return tpl.render(active="docs", **_page_vars(ctx))


def render_funding_page(ctx: dict) -> str:
    """The /funding/ page: the LLM spend, what is left and the donations,
    all from `ctx.funding` (deliver.funding)."""
    tpl = _env().get_template("funding.html.j2")
    return tpl.render(active="funding", **_page_vars(ctx))


def render_connect_page(ctx: dict) -> str:
    """The /connect/ page: what can be followed today and what comes later."""
    tpl = _env().get_template("connect.html.j2")
    return tpl.render(active="connect", **_page_vars(ctx))


def render_search_page(ctx: dict) -> str:
    """The /search/ page: client-side index filter (search.js + the
    search-index.json shipped by write_site). Only chrome + a search box;
    the payload is fetched by the browser."""
    tpl = _env().get_template("search.html.j2")
    return tpl.render(active="search", **_page_vars(ctx))


def render_archive_page(month: str, sections: list[dict],
                        *, prev_url: str | None = None,
                        next_url: str | None = None,
                        lang: str = "zh") -> str:
    """One month, all three sections, as a SINGLE html document.

    `sections` is [{"section": "daily", "cells": [{day, url|None}, ...]}, ...];
    the Monday-first leading blanks are derived here. `prev_url`/`next_url`
    are the neighbouring months' pages, and None means there is no neighbour
    — the template then omits that link rather than rendering a dead one.

    The neighbours are NOT derived here. This function used to fall back to
    "the previous calendar month", which invented pages: the earliest month
    linked to a month that had never had an issue, and /archive/2026-08.html
    shipped a 404 to /archive/2026-07.html. Only the caller knows which
    months exist.

    One document, not three: the caller used to render this template once per
    section and concatenate the results, so /archive/ shipped three complete
    <!DOCTYPE html> pages in one file.
    """
    import calendar as _cal
    from datetime import date

    from intel.i18n import month_label

    year, mon = (int(p) for p in month.split("-"))
    first_weekday = date(year, mon, 1).weekday()          # Monday == 0
    days_in_month = _cal.monthrange(year, mon)[1]

    built = []
    for sec in sections:
        by_day = {c["day"]: c for c in sec["cells"]}
        grid: list[dict] = [{} for _ in range(first_weekday)]
        for day in range(1, days_in_month + 1):
            grid.append(by_day.get(day, {"day": day, "url": None}))
        built.append({"section": sec["section"], "grid": grid})

    ctx = _chrome(lang, edition_label=_t("nav.archive", lang),
                  page_path=f"/archive/{month}.html")
    tpl = _env().get_template("archive_page.html.j2")
    return tpl.render(
        month=month,
        title=month_label(year, mon, lang),
        sections=built,
        prev_url=prev_url,
        next_url=next_url,
        active="archive",
        **_page_vars(ctx),
    )


_TAG_TIERS = (1, 3, 6, 12)   # count thresholds → tier class t0..t3


def render_tags_index(tags: list[dict], *, lang: str = "zh") -> str:
    """Two sections. Topics (the closed vocabulary) under their group
    headings, most used first within a group, as size-tiered pills. Names
    (the open entity tier: products, games, tools) A to Z under letter
    headings with a jump bar, folded, since there are hundreds of them and
    a reader looking for one already knows its name."""
    vocab = _vocabulary()
    by_group: dict[str, list[dict]] = {g: [] for g in vocab.groups}
    names: list[dict] = []
    for t in sorted(tags, key=lambda t: (-t.get("count", 0), t["tag"])):
        group = vocab.group_of(t["tag"])
        if group is None:
            names.append(t)
            continue
        n = t.get("count", 0)
        tier = sum(1 for th in _TAG_TIERS if n >= th)
        by_group[group].append({**t, "tier": f"t{min(tier, 3)}"})
    groups = [{"label": vocab.group_label(g, lang), "tags": ts}
              for g, ts in by_group.items() if ts]

    letters: list[dict] = []
    for t in sorted(names, key=lambda t: t["tag"]):
        first = t["tag"][:1].upper()
        key = first if "A" <= first <= "Z" else (
            "0-9" if first.isdigit() else "#")
        if not letters or letters[-1]["key"] != key:
            letters.append({"key": key, "tags": []})
        letters[-1]["tags"].append(t)
    # Letters first, then digits, then anything else (a legacy tag that
    # predates the ASCII entity rule).
    letters.sort(key=lambda b: (b["key"] == "#", b["key"] == "0-9", b["key"]))
    special = {"0-9": ("n-digits", "0–9"), "#": ("n-other", "#")}
    for b in letters:
        b["id"], b["label"] = special.get(b["key"], (f"n-{b['key'].lower()}", b["key"]))

    ctx = _chrome(lang, edition_label=_t("tags.title", lang),
                  page_path="/tags/")
    tpl = _env().get_template("tags_index.html.j2")
    return tpl.render(groups=groups, letters=letters, total=len(tags),
                      n_topics=sum(len(g["tags"]) for g in groups),
                      n_names=len(names), active="tags", **_page_vars(ctx))


def render_tag_page(tag: str, items: list[dict], *,
                    lang: str = "zh") -> str:
    ctx = _chrome(lang, edition_label=f"#{_tag_label(tag, lang)}",
                  page_path=f"/tags/{quote(tag)}.html")
    tpl = _env().get_template("tag_page.html.j2")
    return tpl.render(tag=tag, items=items, active="tags", **_page_vars(ctx))


def render_tiers_index(tiers: list[dict], months: list[str], *,
                       lang: str = "zh") -> str:
    """The tier history overview: each tier's total, and a month-by-tier
    grid of counts whose cells open that tier's month. `tiers` is
    [{tier, total, counts: {month: n}}], S first; `months` newest first."""
    ctx = _chrome(lang, edition_label=_t("tiers.title", lang),
                  page_path="/tiers/")
    tpl = _env().get_template("tiers_index.html.j2")
    return tpl.render(tiers=tiers, months=months, active="tiers",
                      **_page_vars(ctx))


def render_tier_page(tier: str, month: str, entries: list[dict], *,
                     months: list[dict], tiers: list[dict],
                     lang: str = "zh") -> str:
    """One tier's repositories rated in one month, newest first. `months`
    is [{month, n, url}] for this tier, newest first; `tiers` is
    [{tier, total, url}] for the switcher."""
    ctx = _chrome(lang, edition_label=_t("tiers.title", lang),
                  page_path=f"/tiers/{tier.lower()}/{month}.html")
    tpl = _env().get_template("tier_page.html.j2")
    return tpl.render(tier=tier, month=month, entries=entries, months=months,
                      tiers=tiers, active="tiers", **_page_vars(ctx))


def render_feed(entries: list[dict], *, updated: str, site_url: str,
                lang: str = "zh") -> str:
    """Atom 1.0 feed for ONE language. `entries` is [{title, url, updated,
    summary, tier}], newest first; `updated` is the RFC 3339 timestamp for
    the whole feed. The channel strings are translated and every feed-level
    URL carries the language prefix, so /zh/feed.xml is a feed in its own
    right instead of a Chinese page pointing at the English channel."""
    p = lang_prefix(lang)
    tpl = _env().get_template("feed.xml.j2")
    return tpl.render(entries=entries, updated=updated, site_url=site_url,
                      lang=lang, p=p, alt_p=lang_prefix(other_lang(lang)),
                      t=lambda key, **fmt: _t(key, lang, **fmt))
