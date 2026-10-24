"""Write the static intel site under TIERLIST_WEB_DIR (default <root>/data/web).

Rendering lives in `intel.render.site` (owned by the site build) — imported
LAZILY inside the write path, never at module top, so this module stays
importable before that package exists. File names for tags are raw UTF-8 on
disk; hrefs are percent-encoded.

Bilingual layout: en = site root (/daily/, /archive/...), zh = /zh/ tree.
Every page of both trees is rendered and written per language, and so is
feed.xml (a Chinese reader must not land in the English channel). Truly
language-free assets — favicon.svg, robots.txt, sitemap.xml — go out once at
the root. The daily sweep owns the whole tree incl.
archive/tags; the news cron writes only news + landing, reusing `write_web`.
"""
from __future__ import annotations

import functools
import json as _json
import os
import urllib.parse
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from intel import editions, features, tiers
from intel.deliver import api
from intel.deliver import funding as funding_mod
from intel.deliver import manifest as manifest_mod
from intel.i18n import reason_in
from intel.i18n import t as _t
from intel.render.site import _chrome  # noqa: F401  (re-export for B1 layout)
from intel.store import db
from intel.tiers import tier

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

SECTIONS = ("daily", "weekly", "monthly")

LANGS = ("zh", "en")


def web_root() -> Path:
    return Path(os.environ.get("TIERLIST_WEB_DIR")
                or (_PROJECT_ROOT / "data" / "web"))


def _write(path: Path, html: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == html:
                return path  # identical content — rewriting would churn mtimes
        except OSError:
            pass
    # newline="\n": the same bytes on every OS. Windows would otherwise
    # write CRLF, and a page built there would differ from the server's.
    path.write_text(html, encoding="utf-8", newline="\n")
    return path


def write_web(rel: str, html: str) -> Path:
    """Write a page at web_root()/<rel> (e.g. "en/news/index.html"). Public
    so the news cron can write pages with the same skip-if-identical path
    the daily site writer uses."""
    return _write(web_root() / rel.lstrip("/"), html)


def issue_slug(edition: str, ctx: dict) -> str:
    """Filename stem of an issue page: daily/<date>, weekly/<ISO week>,
    monthly/<YYYY-MM>."""
    if edition == "weekly":
        return ctx["period_label"].split(" · ")[0]
    return ctx["period_label"]


def site_url() -> str:
    """Absolute origin of the published site, used by anything that has to
    name a page from outside the page: feed links, canonical/OG URLs and the
    sitemap. Trailing slash stripped so callers concatenate paths freely."""
    return os.environ.get("TIERLIST_SITE_URL",
                          "https://tierlist.tech").rstrip("/")


async def build_feed(conn, render, lang: str = "zh") -> str:
    """Atom feed = the recent-window Top entries, same selection as the site
    (store.feed_rows), so RSS never disagrees with the pages. Best-effort:
    DB trouble yields an empty-but-valid feed, never a failed site write.

    One feed per language: the entries are the same picks (repo names and
    URLs are payload, not chrome), but the channel title, subtitle and entry
    summaries are translated and the feed's own URLs carry the prefix."""
    from datetime import UTC, datetime

    try:
        rows = await db.feed_rows(conn)
    except Exception:
        rows = []
    entries = []
    for r in rows:
        title = r["name"] or r["item_id"]
        updated = r["snap_date"].isoformat() + "T00:00:00Z"
        tr = tier(r["score"], dims=r["dims"], version=r["model_version"])
        summary = _t("feed.entry_summary", lang, tier=tr, title=title)
        reason = reason_in(lang, r["reason"], r.get("reason_en"))
        if reason:
            summary = f"{summary} — {reason[:120]}"
        entries.append({
            "title": title,
            "url": r["url"],
            "updated": updated,
            "summary": summary,
            "tier": tr,
        })
    return render.render_feed(
        entries,
        updated=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        site_url=site_url(),
        lang=lang,
    )


_STATIC = Path(__file__).resolve().parent.parent / "render" / "static"

# The 16 px cut of the Stepped vein mark, coloured for the reader's theme
# (the file switches rock and vein under prefers-color-scheme, which browsers
# honour inside an SVG favicon). Inline geometry, no font dependency.
FAVICON_SVG = (_STATIC / "favicon.svg").read_text(encoding="utf-8")

# The rest of the icon kit, copied to the web root byte for byte: the .ico
# for browsers and feed readers without SVG icons, the home-screen icons and
# the manifest that names them. The images are built from the brand source
# and committed as files; nothing here renders them.
ICON_FILES = ("favicon.ico", "apple-touch-icon.png", "icon-192.png",
              "icon-512.png", "site.webmanifest")


def _copy(path: Path, src: Path) -> Path:
    """Copy `src` to `path` unless the bytes already match."""
    data = src.read_bytes()
    if not (path.exists() and path.read_bytes() == data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path


def build_robots() -> str:
    """robots.txt: the whole site is open to crawlers, and the sitemap is
    named so a crawler finds every issue without walking the archive."""
    return (
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {site_url()}/sitemap.xml\n"
    )


def build_sitemap(paths: list[str], *, lastmod: str) -> str:
    """sitemap.xml over the site-relative page paths the run just wrote.

    Derived from the write list rather than from a hand-kept inventory, so a
    page that stops being generated also stops being advertised — the failure
    mode of a static list is a sitemap full of 404s, which costs crawl budget
    silently. Duplicate paths (an issue is written twice, once as the rolling
    index) collapse; only .html/directory pages are listed, never assets."""
    seen: list[str] = []
    for p in paths:
        if p not in seen:
            seen.append(p)
    body = "\n".join(
        f"  <url><loc>{site_url()}{urllib.parse.quote(p)}</loc>"
        f"<lastmod>{lastmod}</lastmod></url>"
        for p in sorted(seen)
    )
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{body}\n</urlset>\n")


def _sitemap_paths(root: Path, written: list[Path]) -> list[str]:
    """Site-relative URLs for every HTML page under `root`. `index.html`
    collapses to its directory (that is the URL readers and the canonical
    tags use).

    Derived from the tree on disk, not from `written`: a sweep writes only
    the issue it produced, so a sitemap built from one run's output listed
    exactly one dated permalink and dropped every back issue. `written` is
    still taken so a caller's pages are covered even if a later reader
    cannot see them, but everything in it has already been flushed by the
    time this runs."""
    seen = {p.resolve() for p in written if p.suffix == ".html"}
    seen.update(p.resolve() for p in root.rglob("*.html"))
    out: list[str] = []
    for path in sorted(seen):
        try:
            rel = path.relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        out.append("/" + rel[: -len("index.html")] if rel.endswith("index.html")
                   else "/" + rel)
    return out


def archive_neighbour(months: list[str], i: int,
                      lang: str = "zh") -> str | None:
    """The archive page for `months[i]`, or None when `i` falls outside the
    list — the calendar's prev/next link.

    Neighbours come from the months that EXIST, never from date arithmetic.
    The renderer used to fall back to the adjacent calendar month, which put
    a 404 on the earliest page (/archive/2026-08.html linked to
    /archive/2026-07.html, a month that never had an issue), while the
    newest page's "next" was built from a filesystem path and published the
    server's absolute deploy directory as an href.

    The bounds test is explicit rather than a try/IndexError: `i` is −1 at
    the earliest month, and a negative index would quietly wrap to the
    NEWEST month instead of ending the chain.
    """
    if not 0 <= i < len(months):
        return None
    return f"{editions.lang_prefix(lang)}/archive/{months[i]}.html"


async def tag_cloud(conn) -> list[dict]:
    """[{tag, count, url}] — every tag in items.tags, most-used first. URLs
    are prefix-relative: the caller adds the language prefix."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT tag, COUNT(*) AS n
            FROM intel.items, LATERAL jsonb_array_elements_text(tags) AS t(tag)
            WHERE tags IS NOT NULL
            GROUP BY tag ORDER BY n DESC, tag COLLATE "C" ASC
            """
        )
        rows = await cur.fetchall()
    return [{"tag": r[0], "count": int(r[1]),
             "url": f"/tags/{urllib.parse.quote(r[0])}.html"} for r in rows]


def tag_cloud_lang(cloud: list[dict], lang: str) -> list[dict]:
    """tag_cloud rows with the language prefix baked into their URLs."""
    p = editions.lang_prefix(lang)
    return [{**t, "url": f"{p}{t['url']}"} for t in cloud]


async def tag_items(conn, tag: str, limit: int = 200) -> list[dict]:
    """One tag's projects: best score ever, score-desc, first_seen as date."""
    return (await tag_items_many(conn, limit)).get(tag, [])


async def tag_items_many(conn, limit: int = 200) -> dict[str, list[dict]]:
    """Every tag's projects in ONE scan: {tag: [rows]}. Each row keeps the
    tag-page shape (name, url, score, day, description). Rows come from the
    lateral join ordered score-desc; per-tag truncation happens here, so a
    tag page never grows past `limit`. The O(N-per-tag) write_site loop used
    to issue one query per tag — with hundreds of tags every daily run paid
    hundreds of round-trips for data this single scan already contains.

    The trailing i.id breaks ties between equal scores on the same day, and
    COLLATE "C" sorts text by code point: without both, the order depends
    on the database's locale and two machines build different pages from
    the same rows. Every text ORDER BY that reaches a page does the same."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT t.tag, i.name, i.url, x.score,
                   i.first_seen::date AS day, i.description, x.dims,
                   x.model_version
            FROM intel.items i
            CROSS JOIN LATERAL jsonb_array_elements_text(i.tags) AS t(tag)
            LEFT JOIN LATERAL (
                SELECT score, dims, model_version FROM intel.scores s
                WHERE s.item_id = i.id AND score IS NOT NULL
                ORDER BY score DESC LIMIT 1
            ) x ON true
            WHERE i.tags IS NOT NULL
            ORDER BY tag COLLATE "C", x.score DESC NULLS LAST, i.first_seen DESC,
                     i.id COLLATE "C"
            """
        )
        rows = await cur.fetchall()
    by_tag: dict[str, list[dict]] = {}
    for r in rows:
        bucket = by_tag.setdefault(r[0], [])
        if len(bucket) < limit:
            bucket.append({"name": r[1], "url": r[2],
                           "tier": tier(r[3], dims=r[6], version=r[7]),
                           "date": r[4], "description": r[5]})
    return by_tag


async def _use_descriptions_en(render, conn) -> None:
    """Hand the renderer the English descriptions. A store that cannot be
    read for them leaves every page on the author's original."""
    try:
        table = await db.english_descriptions(conn)
    except Exception:  # noqa: BLE001 — the original is a correct fallback
        table = {}
    render.use_descriptions_en(table)


def _lang_dir(root: Path, lang: str) -> Path:
    return root / editions.lang_prefix(lang).lstrip("/")


async def write_site(conn, contexts: dict[str, dict], *,
                    force: bool | None = None) -> list[Path]:
    """Persist everything one run refreshes: each edition's issue + index
    pages, landing, archive, tags — for BOTH languages.

    `contexts` maps section → {lang: ctx} (the daily sweep builds both
    languages; weekly/monthly runs may build one and get mirrored chrome
    only). A `section → ctx` plain dict (pre-i18n shape) is accepted and
    treated as zh, so older callers keep working."""
    from intel.i18n import LANGS
    from intel.render import site as render  # lazy — see module docstring

    await _use_descriptions_en(render, conn)
    root = web_root()
    written: list[Path] = []
    man = manifest_mod.Manifest.load(root, force=force)
    # Shared chrome: style.css AND the /zh/ copy it is referenced as — the
    # templates link `{{ p }}/style.css`, so each language tree needs its
    # own under its prefix (en at root, zh under /zh/). feed.xml is per
    # language for the same reason: a Chinese reader subscribing from a
    # Chinese page must not land in the English channel.
    for lang in LANGS:
        written.append(_write(_lang_dir(root, lang) / "style.css",
                              render.style_css()))
        written.append(_write(_lang_dir(root, lang) / "feed.xml",
                              await build_feed(conn, render, lang)))

    idx = await search_index(conn)
    json_index = _json.dumps(idx, ensure_ascii=False)
    search_js = _js_asset(render)
    # pager.js goes in both trees for the same reason style.css does: pages
    # link their assets as `{{ ctx.p }}/pager.js`, so /zh/ needs its own copy.
    pager_js = render.pager_js()
    since_js = render.since_js()
    intro_js = render.intro_js()
    for lang in LANGS:
        sub = _lang_dir(root, lang)
        written.append(_write(sub / "search-index.json", json_index))
        written.append(_write(sub / "search.js", search_js))
        written.append(_write(sub / "pager.js", pager_js))
        written.append(_write(sub / "since.js", since_js))
        written.append(_write(sub / "intro.js", intro_js))

    # The pager's chain, read once: which issues EXIST per section. The
    # sweep's own issue may not be in `issue_dates` yet (its scores and this
    # write are not one transaction), and issue_neighbours places it in sort
    # order rather than dropping its links.
    issue_dates = await db.issue_dates(conn)
    slugs = {section: editions.issue_slugs(section, issue_dates)
             for section in SECTIONS}

    norm: dict[str, dict[str, dict]] = {}
    for edition, ctx in contexts.items():
        norm[edition] = (ctx if all(isinstance(v, dict) and "lang" in v
                                    for v in ctx.values())
                         else {"zh": ctx})
    chain = {section: list(slugs.get(section, [])) for section in SECTIONS}
    for edition, by_lang in norm.items():
        for page_ctx in by_lang.values():
            slug = issue_slug(edition, page_ctx)
            if slug not in chain.setdefault(edition, []):
                chain[edition] = sorted([*chain[edition], slug])
    funding = await funding_facts(conn)
    for lang in LANGS:
        sub = _lang_dir(root, lang)
        for edition, by_lang in norm.items():
            page_ctx = by_lang.get(lang)
            if page_ctx is None:
                continue
            slug = issue_slug(edition, page_ctx)
            if lang == API_LANG and features.enabled("json_api"):
                written.append(_write(
                    root / api.API_DIR / api.issue_rel(edition, slug),
                    api.dumps(api.issue_doc(page_ctx, edition=edition,
                                            slug=slug, site_url=site_url()))))
            # Both files hold the same bytes, so both must declare the SAME
            # canonical URL — the dated permalink, since /<edition>/ is a
            # rolling copy of whichever issue is newest. Pointing each at
            # itself would tell a crawler the two are distinct pages with
            # identical content, which is the duplicate it must not see.
            html = render.render_issue(
                {**page_ctx, "page_path": f"/{edition}/{slug}.html",
                 "pager": editions.issue_neighbours(
                     edition, slug, slugs.get(edition, []), lang)})
            for target in (sub / edition / "index.html",
                           sub / edition / f"{slug}.html"):
                written.append(_write(target, html))
                # Rendered from a live RunResult, not from the store, so no
                # store fingerprint describes what is now on disk: forget
                # the entry rather than let write_history conclude from an
                # unchanged input that this file needs nothing.
                man.invalidate(target.relative_to(root).as_posix())

        daily_ctx = norm.get("daily", {}).get(lang)
        if daily_ctx is not None:
            written.append(_write(
                sub / "index.html",
                render.render_landing({**daily_ctx, "page_path": "/"},
                                      daily_ctx["archive_recent"])))

        written.append(_write(sub / "search" / "index.html",
                              render.render_search_page(_search_ctx(lang))))
        written.append(_write(sub / "docs" / "index.html",
                              render.render_docs_page(_docs_ctx(lang))))
        if features.enabled("funding_page"):
            written.append(_write(sub / "funding" / "index.html",
                                  render.render_funding_page(
                                      _funding_ctx(lang, funding))))
        written.append(_write(sub / "connect" / "index.html",
                              render.render_connect_page(_connect_ctx(lang))))

        await _write_chrome_lang(conn, render, root, lang, written, man)

    written += await write_api(conn, root, chain, funding)

    # Root-level, language-free assets. The sitemap goes LAST because it is
    # built from everything written above; robots.txt points at it.
    written.append(_write(root / "favicon.svg", FAVICON_SVG))
    written += [_copy(root / name, _STATIC / name) for name in ICON_FILES]
    written.append(_write(root / "robots.txt", build_robots()))
    written.append(_write(
        root / "sitemap.xml",
        build_sitemap(_sitemap_paths(root, written),
                      lastmod=date.today().isoformat())))
    man.save()
    return written


def _issue_fingerprint(*, edition: str, slug: str, lang: str, vertical: str,
                       watch_min: int | None, anchor: date, digests: dict,
                       snaps_running: dict, pager: dict) -> str:
    """One issue page's inputs, hashed. `deliver.manifest` states the rule
    this implements; this function is only its arithmetic.

    The window is `[anchor - WINDOW_DAYS, anchor]` for every edition,
    daily included — `daily_db_context` reads a single day
    (`window_score_rows(0)`), so daily's extra day is slack the page cannot
    need. Slack in this direction re-renders a page needlessly; slack in the
    other direction publishes a stale one.

    Star snapshots enter through `snaps_running`, the fold over every date
    at or before the anchor, because `gain._coverage` measures each item's
    whole series up to `as_of`: a snapshot written for an old date changes
    the gain board of every issue at or after it.
    """
    span = editions.WINDOW_DAYS[edition]
    start = anchor - timedelta(days=span)
    return manifest_mod.digest(
        manifest_mod.code_fingerprint(),
        site_url(),
        [edition, slug, lang, vertical, watch_min, str(anchor),
         tiers.fingerprint(vertical)],
        pager,
        manifest_mod.window_digest(digests.get("scores", {}), start, anchor),
        manifest_mod.window_digest(digests.get("items", {}), start, anchor),
        manifest_mod.window_digest(digests.get("runs", {}), start, anchor),
        manifest_mod.at_or_before(snaps_running, anchor),
    )


async def write_history(conn, vertical: str, *,
                        watch_min: int | None = None,
                        force: bool | None = None,
                        stats: dict | None = None,
                        only: set[tuple[str, str]] | None = None,
                        pending: dict[str, str] | None = None) -> list[Path]:
    """Re-render every dated issue permalink WHOSE INPUTS CHANGED, from
    Postgres with the current templates, both languages.

    `only`, when given, narrows the plan to those (edition, slug) pairs; the
    pager chain is still built from every issue. `pending` maps an edition
    to a slug the caller has just published but `issue_dates` may not show
    yet, so a neighbour rendered here still links forward to it.

    Every page is still planned; a page is skipped only when a fingerprint
    of everything it is built from matches the one recorded when it was last
    written, and the output file is still on disk. What counts as an input
    is spelled out in `deliver.manifest`: the score rows and item columns of
    the page's own window, the items and runs behind its lede, every star
    snapshot up to its anchor, its identity and pager links, and the code
    that renders it. All of it comes from ONE `db.page_input_digests` round
    trip; a store that cannot answer it makes the whole run fall back to the
    unconditional re-render this function used to be.

    `force=True` (`TIERLIST_RENDER_FORCE=1`, or `--rebuild-site
    --force-render`) renders everything — the escape hatch for a change no
    fingerprint can see, and the belt to the code fingerprint's braces.
    `stats`, when passed, is filled with {rendered, skipped, pages}.

    `write_site` only ever writes the issue the running sweep produced, so a
    dated page is frozen at whatever the templates looked like the day it
    was generated while `style.css` is overwritten in full on every run. The
    two drift apart silently and the result is not a missing page but a
    mangled one: /daily/2026-08-24.html shipped `.e-head` markup into a
    stylesheet that had moved on to `.e-main`, and the 12-column grid dropped
    every unrecognised child into its own column. A redesign has to be able
    to reach backwards, and a day the sweep scored but never published
    (2026-08-23) has to be reachable at all — the /archive/ calendar links
    every date in `issue_dates`, so a date with scores and no file is a 404
    the site links to itself.

    The rolling `<edition>/index.html` copies are written too, but ONLY for
    the newest slug of each section — that is what they are, byte for byte,
    when the sweep writes them. They used to be skipped, on the reasoning
    that the daily context carries a 新仓库热榜 the store could not
    reproduce. That reasoning has been retired at the root (the board is
    persisted with the run since 2026-08-27) and it never applied to weekly
    or monthly at all, whose contexts set `hot_new` to None by construction.

    Skipping them cost more than it saved. `/weekly/` and `/monthly/` are
    only rewritten by their own Sunday / first-of-month cron, so between
    those they drift: on 2026-08-27 all four of them (both languages) were
    two days stale and carried **18 links each to tag pages that the
    vocabulary change had deleted** — 72 dead links the site published to
    itself, which is the failure `prune_tag_pages` and `archive_neighbour`
    were each written to prevent, arriving through the one door left open.
    """
    from intel.render import site as render  # lazy — see module docstring

    await _use_descriptions_en(render, conn)
    watch_min = _watch_min() if watch_min is None else watch_min
    root = web_root()
    written: list[Path] = []
    rendered = skipped = 0

    def _stats() -> list[Path]:
        if stats is not None:
            stats.update({"rendered": rendered, "skipped": skipped,
                          "pages": rendered + skipped})
        return written

    dates = await db.issue_dates(conn)
    if not dates:
        return _stats()

    # One anchor date per period, and it is the period's LAST issue date, not
    # its calendar end: anchoring 2026-W34 at Sunday 08-23 when the only
    # scored day in it is 08-23 gives the same window the live Sunday cron
    # would have used, and a still-running period anchors at its newest day
    # instead of a future date with no rows behind it.
    weekly: dict[str, date] = {}
    monthly: dict[str, date] = {}
    for d in sorted(dates):
        weekly[editions.iso_week_key(d)] = d
        monthly[editions.month_key(d)] = d

    # The pager chain for the backfill: exactly the pages this plan writes,
    # which is also what /archive/ links. Derived from the same `dates` the
    # plan is, so a re-render can never publish a neighbour it did not write.
    chain = {"daily": [d.isoformat() for d in sorted(dates)],
             "weekly": list(weekly),
             "monthly": list(monthly)}
    for edition, slug in (pending or {}).items():
        if slug not in chain[edition]:
            chain[edition] = sorted([*chain[edition], slug])

    plan: list[tuple[str, str, date]] = (
        [("daily", d.isoformat(), d) for d in sorted(dates)]
        + [("weekly", key, anchor) for key, anchor in weekly.items()]
        + [("monthly", key, anchor) for key, anchor in monthly.items()]
    )
    if only is not None:
        plan = [step for step in plan if (step[0], step[1]) in only]

    # The newest slug per section: its page is also the rolling index.
    newest = {edition: max(slugs) for edition, slugs in chain.items() if slugs}

    try:
        digests = await db.page_input_digests(conn)
    except Exception:
        # Fail open. A store that cannot say what changed is answered with
        # the full re-render, because the alternative — trusting a manifest
        # no query backs - is how a permalink freezes in place.
        digests = None
    snaps_running = (manifest_mod.running_digests(digests.get("snaps", {}))
                     if digests is not None else {})
    man = manifest_mod.Manifest.load(root, force=force)

    for edition, slug, anchor in plan:
        for lang in LANGS:
            pager = editions.issue_neighbours(edition, slug, chain[edition],
                                              lang)
            # Both files this pair writes are keyed separately: the rolling
            # index holds the same bytes but is overwritten by other
            # writers, so its being current is a separate question.
            targets = [_lang_dir(root, lang) / edition / f"{slug}.html"]
            if slug == newest.get(edition):
                targets.append(_lang_dir(root, lang) / edition / "index.html")
            # The issue's API document is built from the same context, so
            # it is one more output of this page and is skipped with it.
            json_target = (root / api.API_DIR / api.issue_rel(edition, slug)
                           if lang == API_LANG and features.enabled("json_api")
                           else None)
            keys = [t.relative_to(root).as_posix()
                    for t in [*targets, *filter(None, [json_target])]]
            fp = None if digests is None else _issue_fingerprint(
                edition=edition, slug=slug, lang=lang, vertical=vertical,
                watch_min=watch_min, anchor=anchor, digests=digests,
                snaps_running=snaps_running, pager=pager)
            if all(man.current(k, fp) for k in keys):
                skipped += 1
                for k in keys:
                    man.keep(k)
                continue
            rendered += 1
            if edition == "daily":
                ctx = await editions.daily_db_context(
                    conn, vertical, run_date=anchor, lang=lang,
                    watch_min=watch_min)
            else:
                ctx = await editions.aggregate_context(
                    conn, edition, vertical, lang=lang,
                    now=datetime.combine(anchor, time(12, 0), tzinfo=UTC))
            html = render.render_issue(
                {**ctx, "page_path": f"/{edition}/{slug}.html",
                 "pager": pager})
            # The rolling <edition>/index.html is in `targets` already, with
            # the same bytes and the same canonical — exactly what
            # write_site does when the sweep writes the pair.
            for target, key in zip(targets, keys, strict=False):
                written.append(_write(target, html))
                man.record(key, fp)
            if json_target is not None:
                written.append(_write(json_target, api.dumps(api.issue_doc(
                    ctx, edition=edition, slug=slug, site_url=site_url()))))
                man.record(keys[-1], fp)
    man.save()
    return _stats()


async def refresh_previous(conn, vertical: str,
                           contexts: dict[str, dict]) -> list[Path]:
    """Re-render the issue just before each one a run published.

    `write_site` writes only the run's own issue, so the page before it keeps
    the pager it was born with: its "next" side still says 已是最新一期 and
    the reader cannot step forward. Every daily page from 2026-10-07 to
    2026-10-09 was left that way until the Sunday rebuild. Rendering that one
    neighbour per section closes the chain the day the new issue appears.
    """
    dates = await db.issue_dates(conn)
    pending: dict[str, str] = {}
    only: set[tuple[str, str]] = set()
    for edition, by_lang in contexts.items():
        ctx = next(iter(by_lang.values()), None) if by_lang else None
        if not isinstance(ctx, dict) or "period_label" not in ctx:
            continue
        slug = issue_slug(edition, ctx)
        prev = editions.issue_neighbours(
            edition, slug, editions.issue_slugs(edition, dates))["prev"]
        if prev is not None:
            pending[edition] = slug
            only.add((edition, prev["label"]))
    if not only:
        return []
    return await write_history(conn, vertical, only=only, pending=pending)


def _search_ctx(lang: str) -> dict:
    """The /search/ page's chrome context — nav paths + the switch, nothing
    else (the results come from the JSON index via JS)."""
    p = editions.lang_prefix(lang)
    return {
        "lang": lang,
        "p": p,
        "edition_label": _t("nav.search", lang),
        "period_label": "search",
        "nav": {k: f"{p}{v}" for k, v in NAV_ITEMS.items()},
        "page_path": "/search/",
    }


NAV_ITEMS = {"daily": "/daily/", "weekly": "/weekly/",
             "monthly": "/monthly/", "news": "/news/",
             "archive": "/archive/", "tags": "/tags/",
             "search": "/search/", "docs": "/docs/"}


def docs_facts(lang: str, vertical: str = "dev_tools") -> dict:
    """The numbers /docs/ prints, read from where the pipeline reads them:
    the rubric (dimensions, weights, tier lines and definitions) and
    config.yaml (fetch window, prefilter lines, batch and board sizes)."""
    import yaml

    from intel.pipeline import load_config
    from intel.rubrics import load_rubric

    rubric = load_rubric(vertical)
    raw = yaml.safe_load((_PROJECT_ROOT / "intel" / "rubrics"
                          / f"{vertical}.yaml").read_text(encoding="utf-8"))
    names = raw["public_dimensions"][lang]
    cfg_path = _PROJECT_ROOT / "config.yaml"
    cfg = load_config(cfg_path)
    gh = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))["sources"]["github"]
    return {
        "dimensions": [{"key": k, "name": names[k][0], "text": names[k][1],
                        "weight": rubric.weights[k]}
                       for k in rubric.dimensions],
        "lines": [{"tier": t, "min": n} for n, t in tiers.lines(vertical)],
        "definitions": raw["tier_definitions"][lang],
        "lookback_hours": cfg.lookback_hours,
        "fetch_ceiling": int(gh["max_pages"]) * 100,
        "min_description_chars": int(gh["min_description_chars"]),
        "min_star_velocity": gh["min_star_velocity_per_day"],
        "farm_cluster": int(gh["star_farm_cluster"]),
        "farm_window": int(gh["star_farm_window_minutes"]),
        "max_candidates": cfg.max_candidates,
        "top_n": cfg.top_n,
    }


def _docs_ctx(lang: str) -> dict:
    """The /docs/ page's context: the chrome plus `docs_facts`."""
    return {**_search_ctx(lang), "edition_label": _t("nav.docs", lang),
            "period_label": "docs", "page_path": "/docs/",
            "docs": docs_facts(lang)}


# The language the API's issue documents are built from. Entries carry the
# same names, reasons and tags in both trees; only the chrome differs.
API_LANG = "en"


def funding_config() -> dict:
    import yaml

    raw = yaml.safe_load((_PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))
    return dict(raw.get("funding") or {})


async def funding_facts(conn) -> dict:
    """deliver.funding.costs over the store's spend rows and the donations
    file, plus the funding config the page links from."""
    run_rows = await db.run_cost_rows(conn) if conn is not None else []
    cfg = funding_config()
    return {"costs": funding_mod.costs(run_rows, funding_mod.load_donations()),
            "average_days": funding_mod.AVERAGE_DAYS,
            "github_sponsors": cfg.get("github_sponsors")}


def _connect_ctx(lang: str) -> dict:
    return {**_search_ctx(lang), "edition_label": _t("nav.connect", lang),
            "period_label": "connect", "page_path": "/connect/"}


def _funding_ctx(lang: str, funding: dict) -> dict:
    return {**_search_ctx(lang), "edition_label": _t("nav.funding", lang),
            "period_label": "funding", "page_path": "/funding/",
            "funding": funding}


def _write_if_changed(path: Path, text: str) -> bool:
    """`_write` that says whether the file changed, so a run's output list
    names the item documents it rewrote and not the thousands it kept."""
    before = path.read_text(encoding="utf-8") if path.exists() else None
    _write(path, text)
    return before != text


async def write_api(conn, root: Path, chain: dict[str, list[str]],
                    funding: dict) -> list[Path]:
    """The language-free API documents: latest.json, the tag index and one
    document per tag, one per rated item, and costs.json. Issue documents
    are written beside their pages (write_site, write_history)."""
    base = root / api.API_DIR
    url = site_url()
    out = []
    if features.enabled("funding_page"):
        out.append(_write(base / "costs.json", api.dumps(funding_mod.costs_doc(
            funding["costs"], api_version=api.API_VERSION))))
    if not features.enabled("json_api"):
        return out
    out.append(_write(base / "latest.json",
                      api.dumps(api.index_doc(chain, site_url=url))))
    if conn is None:
        return out
    from intel.tags import label as tag_label

    cloud = await tag_cloud(conn)
    labels = {c["tag"]: {"zh": tag_label(c["tag"], "zh"),
                         "en": tag_label(c["tag"], "en")} for c in cloud}
    out.append(_write(base / "tags" / "index.json",
                      api.dumps(api.tags_index_doc(cloud, labels, site_url=url))))
    by_tag = await tag_items_many(conn)
    for c in cloud:
        path = base / "tags" / f"{c['tag']}.json"
        if _write_if_changed(path, api.dumps(api.tag_doc(
                c["tag"], labels[c["tag"]], by_tag.get(c["tag"]) or [],
                site_url=url))):
            out.append(path)
    for row in await db.rated_item_rows(conn):
        rel = api.item_path(row["id"])
        if rel is None:
            continue
        doc = api.item_doc(row, row["ratings"],
                           tier_of=lambda score, dims, version: tier(
                               score, dims=dims, version=version))
        if _write_if_changed(base / rel, api.dumps(doc)):
            out.append(base / rel)
    return out


async def _write_chrome_lang(conn, render, root: Path, lang: str,
                             written: list[Path],
                             man: manifest_mod.Manifest | None = None) -> None:
    """The per-language chrome pages: archive calendar + tag cloud + tag
    pages. Owned by write_site (daily sweep) only — the news cron renders
    news + landing, never these, so two writers can't race on them.

    Every page here is skipped when its fingerprint is unchanged. Unlike an
    issue page the fingerprint is not an approximation of the inputs: the
    render call's own arguments are hashed, because assembling them is
    already cheap (one `tag_items_many` scan feeds all 233 tag pages of a
    language) and it is the Jinja pass, ~470 pages per language per run,
    that this saves. A page whose arguments hash the same cannot render
    different bytes.
    """
    sub = _lang_dir(root, lang)
    # `write_site` passes its own manifest and saves it once for the whole
    # run; a direct caller gets one of its own and it is saved here, so the
    # skip is never silently a no-op for want of a save.
    own_manifest = man is None
    man = manifest_mod.Manifest.load(root) if own_manifest else man

    def _once(path: Path, fingerprint: str, build) -> None:
        """Render `build()` into `path` unless the fingerprint says the file
        already holds exactly that. A skipped page is left out of `written`
        so a run's own output says how much work it did; the sitemap reads
        the tree rather than this list, so nothing drops off the site."""
        key = path.relative_to(root).as_posix()
        if man.current(key, fingerprint):
            man.keep(key)
            return
        written.append(_write(path, build()))
        man.record(key, fingerprint)

    def _chrome_fp(*parts: object) -> str:
        return manifest_mod.digest(manifest_mod.code_fingerprint(),
                                   site_url(), lang, *parts)

    dates = await db.issue_dates(conn)
    today = date.today()
    cells = editions.archive_cells(dates, today, lang=lang)
    months = sorted(cells["daily"])

    def _sections(month: str) -> list[dict]:
        return [{"section": section,
                 "cells": [{"day": int(c["date"][-2:]), "url": c["url"]}
                           for c in cells[section][month]]}
                for section in SECTIONS]

    for i, month in enumerate(months):
        cells_of, prev_url, next_url = (_sections(month),
                                        archive_neighbour(months, i - 1, lang),
                                        archive_neighbour(months, i + 1, lang))
        _once(sub / "archive" / f"{month}.html",
              _chrome_fp("archive", month, cells_of, prev_url, next_url),
              lambda month=month, cells_of=cells_of, prev_url=prev_url,
              next_url=next_url: render.render_archive_page(
                  month, cells_of, lang=lang,
                  prev_url=prev_url, next_url=next_url))

    if months:
        # /archive/ is the nav target — newest month first, so
        # readers who follow the nav land on the freshest calendar. Same
        # bytes as the newest month's page, so it carries the same links.
        newest_cells = _sections(months[-1])
        prev_url = archive_neighbour(months, len(months) - 2, lang)
        _once(sub / "archive" / "index.html",
              _chrome_fp("archive-index", months[-1], newest_cells, prev_url),
              lambda: render.render_archive_page(
                  months[-1], newest_cells, lang=lang,
                  prev_url=prev_url, next_url=None))

    all_items = await tag_items_many(conn)
    cloud = tag_cloud_lang(await tag_cloud(conn), lang)
    _once(sub / "tags" / "index.html",
          _chrome_fp("tags-index", cloud),
          lambda: render.render_tags_index(cloud, lang=lang))
    for entry in cloud:
        items = all_items.get(entry["tag"]) or []
        _once(sub / "tags" / f"{entry['tag']}.html",  # raw UTF-8 filename
              _chrome_fp("tag", entry["tag"], items),
              lambda tag=entry["tag"], items=items: render.render_tag_page(
                  tag, items, lang=lang))
    prune_tag_pages(sub / "tags", {e["tag"] for e in cloud})

    # Tier history: one page per tier per month, the newest month also as
    # the tier's index, and an overview. Split by month because the lower
    # tiers hold thousands of repositories: D alone is ~80 a day.
    history = editions.tier_history(await db.tier_history_rows(conn), lang)
    p = editions.lang_prefix(lang)
    months_all = sorted({m for by in history.values() for m in by},
                        reverse=True)
    tiers = []
    for tr, by_month in history.items():
        newest = max(by_month) if by_month else None
        tiers.append({"tier": tr, "total": sum(map(len, by_month.values())),
                      "counts": {m: len(v) for m, v in by_month.items()},
                      "url": (f"{p}/tiers/{tr.lower()}/" if newest
                              else f"{p}/tiers/")})
    _once(sub / "tiers" / "index.html", _chrome_fp("tiers", tiers, months_all),
          lambda: render.render_tiers_index(tiers, months_all, lang=lang))
    switch = [{k: t[k] for k in ("tier", "total", "url")} for t in tiers]
    for tr, by_month in history.items():
        if not by_month:
            continue
        bar = [{"month": m, "n": len(by_month[m]),
                "url": f"{p}/tiers/{tr.lower()}/{m}.html"}
               for m in sorted(by_month, reverse=True)]
        for m, entries in by_month.items():
            def build(tr=tr, m=m, entries=entries, bar=bar):
                return render.render_tier_page(tr, m, entries, months=bar,
                                               tiers=switch, lang=lang)
            fp = _chrome_fp("tier", tr, m, entries, bar, switch)
            _once(sub / "tiers" / tr.lower() / f"{m}.html", fp, build)
            if m == bar[0]["month"]:
                _once(sub / "tiers" / tr.lower() / "index.html", fp, build)
    if own_manifest:
        man.save()


def prune_tag_pages(tags_dir: Path, keep: set[str]) -> list[Path]:
    """Delete tag pages for tags that no longer exist; returns what went.

    `/tags/` is the one directory that is wholly derived from the store —
    every page in it is regenerated from `tag_cloud` on each run — so a file
    the run did not write is a tag nobody uses any more. `_write` only ever
    creates, and 2026-08-25's vocabulary change proved what that costs: the
    re-tag left **230 orphans**, 118 of them under the old Chinese
    filenames, unlinked from the cloud but still served, and still listed in
    a sitemap that reads the tree from disk.

    Only this directory is pruned. Issue permalinks look similar and are the
    opposite case: /daily/2026-08-23.html is not regenerated by a normal
    sweep, and deleting what the run did not write would delete the archive.

    Matched on `.html` stem so a stray file of another type is left alone,
    and `index.html` is kept explicitly — it is the cloud itself, not a tag.
    """
    if not tags_dir.is_dir():
        return []
    removed: list[Path] = []
    for path in sorted(tags_dir.iterdir()):
        if not path.is_file() or path.suffix != ".html":
            continue
        if path.name == "index.html" or path.stem in keep:
            continue
        path.unlink()
        removed.append(path)
    return removed


async def render_news(conn, render, lang: str, *, limit: int,
                      ticker: int) -> dict:
    """News page + landing contexts for one language, from the store.
    Shared by the news cron (which writes them) and the daily sweep (which
    mirrors the same two pages so the site never has a stale news panel)."""
    await _use_descriptions_en(render, conn)
    rows = await db.news_recent(conn, limit=limit)
    display = [_display(r) for r in rows]
    p = editions.lang_prefix(lang)
    news_ctx = {
        "lang": lang,
        "p": p,
        "edition_label": "Live News" if lang == "en" else "实时新闻",
        "period_label": "live",
        "updated_ts": _now(),
        "nav": {k: f"{p}{v}" for k, v in editions.NAV.items()},
        "page_path": "/news/",
        "news_rows": display,
    }
    # The newest finished issue, not the clock: see db.latest_run_date.
    today = date.today()
    issue = await db.latest_run_date(conn, "dev_tools", today) or today
    landing = await editions.daily_db_context(
        conn, "dev_tools", run_date=issue,
        lang=lang, news_rows=display[:ticker],
        watch_min=_watch_min())
    return {"news": news_ctx, "landing": landing}


def _watch_min() -> int | None:
    """The rubric's threshold for the below-the-line annotation on the
    landing page. Best-effort: config missing → None (no annotation)."""
    try:
        from intel.rubrics import load_rubric

        return int(load_rubric("dev_tools").watch_min)
    except Exception:
        return None


def _display(row) -> dict:
    pub = row["published_at"]
    return {
        "title": row["title"],
        "url": row["url"],
        "source": row["source"] or "rss",
        "points": int(row["points"] or 0),
        "summary": row["summary"] or "",
        "published": pub.strftime("%m-%d %H:%M") if pub else "",
    }


async def search_index(conn) -> dict:
    """The client-side search payload: {version, refreshed, items:[…]}.
    `items` is one row per scored repo — tags list normalized to plain
    strings (JSON array in PG becomes a Python list here); `when` is the
    best-score snapshot date for display."""
    from intel.tags import label as tag_label

    rows = await db.search_rows(conn)
    items = []
    for r in rows:
        slugs = list(r["tags"] or [])
        items.append({
            "id": r["id"],
            "name": r["name"] or r["id"],
            "url": r["url"],
            "lang": r["lang"],
            "tags": slugs,
            # Both display names ride along so /search/ can match what the
            # reader SEES. Tags are stored as ASCII slugs; a Chinese reader
            # looking at #破解 who types 破解 must not be told there is no
            # such tag.
            "tag_labels": {"zh": [tag_label(s, "zh") for s in slugs],
                           "en": [tag_label(s, "en") for s in slugs]},
            "description": r["description"] or "",
            "tier": tier(r["score"], dims=r["dims"], version=r["model_version"]),
            "when": r["snap_date"].isoformat() if r["snap_date"] else "",
        })
    return {
        "version": "1",
        "refreshed": _now(),
        "count": len(items),
        "items": items,
    }


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _js_asset(render) -> str:
    """The search page's client-side JS, read once from the render package's
    static dir — same source of truth as the templates."""
    return _read_js()


@functools.lru_cache(maxsize=1)
def _read_js() -> str:
    path = (Path(__file__).resolve().parent.parent / "render" / "static"
            / "search.js")
    return path.read_text(encoding="utf-8")
