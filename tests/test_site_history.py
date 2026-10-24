"""write_history — the backfill that re-renders every dated permalink.

Offline: the store reads and the Jinja render are stubbed, because what
this function decides is *which* pages exist and *what date each one is
anchored to*. Both were wrong in production before it existed — 2026-08-23
had 50 scores and no page, and 2026-08-24 kept its old markup under a
rewritten stylesheet.
"""
from __future__ import annotations

from datetime import date

import pytest

from intel import editions
from intel.deliver import site
from intel.store import db

ISSUE_DATES = [date(2026, 8, 21),   # 2026-W34, 2026-08
               date(2026, 8, 23),   # 2026-W34, 2026-08  (last of its week)
               date(2026, 8, 25)]   # 2026-W35, 2026-08  (last of week+month)


@pytest.fixture
def stub(monkeypatch, tmp_path):
    """Records every (edition, lang, anchor) the writer asks a context for,
    and writes a page whose body names them, so a test can assert on the
    plan without a database."""
    calls: list[tuple[str, str, date]] = []
    monkeypatch.setenv("TIERLIST_WEB_DIR", str(tmp_path))

    async def issue_dates(_conn):
        return list(ISSUE_DATES)

    async def daily_db_context(_conn, vertical, *, run_date, lang,
                               watch_min=None):
        calls.append(("daily", lang, run_date))
        return {"edition": "daily", "lang": lang, "anchor": run_date,
                "watch_min": watch_min}

    async def aggregate_context(_conn, edition, vertical, *, lang, now=None):
        calls.append((edition, lang, now.date()))
        return {"edition": edition, "lang": lang, "anchor": now.date()}

    monkeypatch.setattr(db, "issue_dates", issue_dates)
    monkeypatch.setattr(editions, "daily_db_context", daily_db_context)
    monkeypatch.setattr(editions, "aggregate_context", aggregate_context)

    from intel.render import site as render

    def _issue(ctx):
        pager = ctx.get("pager") or {}
        return (f"<html>{ctx['edition']}|{ctx['lang']}|"
                f"{ctx['anchor']}|{ctx['page_path']}"
                f"|prev={(pager.get('prev') or {}).get('url')}"
                f"|next={(pager.get('next') or {}).get('url')}</html>")

    monkeypatch.setattr(render, "render_issue", _issue)
    return calls


def _rel(paths, root):
    return sorted(p.relative_to(root).as_posix() for p in paths)


@pytest.mark.anyio
async def test_writes_a_permalink_for_every_issue_date_in_both_languages(
        stub, tmp_path, anyio_backend):
    written = await site.write_history(None, "dev_tools")

    assert _rel(written, tmp_path) == sorted([
        "zh/daily/2026-08-21.html", "zh/daily/2026-08-23.html",
        "zh/daily/2026-08-25.html",
        "zh/weekly/2026-W34.html", "zh/weekly/2026-W35.html",
        "zh/monthly/2026-08.html",
        "daily/2026-08-21.html", "daily/2026-08-23.html",
        "daily/2026-08-25.html",
        "weekly/2026-W34.html", "weekly/2026-W35.html",
        "monthly/2026-08.html",
        # …plus the rolling copy of each section's newest issue.
        "zh/daily/index.html", "zh/weekly/index.html", "zh/monthly/index.html",
        "daily/index.html", "weekly/index.html",
        "monthly/index.html",
    ])


@pytest.mark.anyio
async def test_the_json_api_is_written_only_when_switched_on(
        stub, tmp_path, monkeypatch, anyio_backend):
    """Off by default (features.json_api): the public site carries no API
    until it is opened. On, each issue gets its document beside its page."""
    await site.write_history(None, "dev_tools", force=True)
    assert not (tmp_path / "api").exists()

    monkeypatch.setenv("TIERLIST_FEATURES", "json_api")
    written = await site.write_history(None, "dev_tools", force=True)
    assert "api/v1/daily/2026-08-21.json" in _rel(written, tmp_path)
    assert "api/v1/monthly/2026-08.json" in _rel(written, tmp_path)


@pytest.mark.anyio
async def test_a_scored_day_that_never_published_gets_its_page(
        stub, tmp_path, anyio_backend):
    """The /archive/ calendar links every date in issue_dates, so a date
    with scores and no file is a 404 the site links to itself."""
    await site.write_history(None, "dev_tools")
    assert (tmp_path / "daily" / "2026-08-23.html").exists()


@pytest.mark.anyio
async def test_each_period_anchors_at_its_last_issue_date(
        stub, tmp_path, anyio_backend):
    await site.write_history(None, "dev_tools")

    anchors = {(edition, anchor) for edition, lang, anchor in stub
               if lang == "zh"}
    # W34 holds 08-21 and 08-23: the anchor is the LAST of them, which is
    # the window the live Sunday cron would have seen. Anchoring at the
    # first would build the week out of one day's rows.
    assert ("weekly", date(2026, 8, 23)) in anchors
    assert ("weekly", date(2026, 8, 21)) not in anchors
    assert ("weekly", date(2026, 8, 25)) in anchors
    # One month, anchored at its newest issue — not at a month-end date
    # that has no rows behind it yet.
    assert [a for e, a in anchors if e == "monthly"] == [date(2026, 8, 25)]


@pytest.mark.anyio
async def test_each_daily_page_is_anchored_at_its_own_date(
        stub, tmp_path, anyio_backend):
    """The permalink and the data behind it must name the same day; the
    page is titled 今日精选 and a drifting anchor silently republishes a
    neighbouring day's picks under an older URL."""
    await site.write_history(None, "dev_tools")
    for day in ISSUE_DATES:
        body = (tmp_path / "daily" / f"{day}.html").read_text(encoding="utf-8")
        assert f"daily|en|{day}|/daily/{day}.html" in body


@pytest.mark.anyio
async def test_rolling_indexes_are_refreshed_from_the_newest_issue(
        stub, tmp_path, anyio_backend):
    """<edition>/index.html IS the newest issue, byte for byte — that is how
    the sweep writes the pair. Leaving it behind is what let /weekly/ and
    /monthly/ drift two days and publish 18 links each to tag pages the
    vocabulary change had deleted."""
    for edition in ("daily", "weekly", "monthly"):
        d = tmp_path / edition
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text("STALE", encoding="utf-8")

    await site.write_history(None, "dev_tools")

    pairs = {"daily": "2026-08-25", "weekly": "2026-W35",
             "monthly": "2026-08"}
    for edition, newest in pairs.items():
        for sub in (tmp_path, tmp_path / "zh"):
            rolling = (sub / edition / "index.html").read_text(
                encoding="utf-8")
            assert rolling != "STALE"
            assert rolling == (sub / edition / f"{newest}.html").read_text(
                encoding="utf-8")


@pytest.mark.anyio
async def test_only_the_newest_issue_becomes_the_rolling_index(
        stub, tmp_path, anyio_backend):
    """An older issue must not overwrite it — the plan walks dates in order
    and the last one to write would otherwise win by accident."""
    await site.write_history(None, "dev_tools")
    rolling = (tmp_path / "daily" / "index.html").read_text(encoding="utf-8")
    assert "2026-08-25" in rolling
    assert "2026-08-21" not in rolling.split("prev=")[0]


@pytest.mark.anyio
async def test_no_issue_dates_writes_nothing(monkeypatch, tmp_path,
                                             anyio_backend):
    monkeypatch.setenv("TIERLIST_WEB_DIR", str(tmp_path))

    async def none(_conn):
        return []

    monkeypatch.setattr(db, "issue_dates", none)
    assert await site.write_history(None, "dev_tools") == []


def test_sitemap_lists_back_issues_not_only_this_runs_output(tmp_path):
    """A sweep writes one dated permalink; the sitemap must still list the
    ones written by earlier sweeps, which is why it reads the tree rather
    than the caller's list."""
    for rel in ("daily/2026-08-23.html", "daily/2026-08-24.html",
                "daily/index.html", "zh/daily/2026-08-24.html",
                "style.css"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")

    paths = site._sitemap_paths(tmp_path, [tmp_path / "daily/2026-08-24.html"])

    assert "/daily/2026-08-23.html" in paths
    assert "/daily/2026-08-24.html" in paths
    assert "/zh/daily/2026-08-24.html" in paths
    assert "/daily/" in paths            # index.html collapses to its dir
    assert "/style.css" not in paths     # not HTML
    assert len(paths) == len(set(paths))  # written ∪ disk, not written + disk


# ------------------------------------------------- archive calendar neighbours

MONTHS = ["2026-08", "2026-09", "2026-10"]


def test_archive_neighbour_walks_the_months_that_exist():
    assert site.archive_neighbour(MONTHS, 1) == "/zh/archive/2026-09.html"
    assert site.archive_neighbour(MONTHS, 1, "en") == "/archive/2026-09.html"


def test_archive_neighbour_ends_the_chain_at_the_earliest_month():
    """i is −1 here. A bare list index would wrap to the LAST month, so the
    oldest calendar would link 'previous' to the newest one."""
    assert site.archive_neighbour(MONTHS, -1) is None


def test_archive_neighbour_ends_the_chain_at_the_newest_month():
    assert site.archive_neighbour(MONTHS, len(MONTHS)) is None


def test_archive_neighbour_of_a_lone_month_is_nothing_either_way():
    assert site.archive_neighbour(["2026-08"], -1) is None
    assert site.archive_neighbour(["2026-08"], 1) is None


# ------------------------------------------------------ stale tag pages

def _tagdir(root, names):
    d = root / "tags"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_text("x", encoding="utf-8")
    return d


def test_prune_removes_a_tag_page_no_tag_points_at_any_more(tmp_path):
    """`_write` only ever creates. The 2026-08-25 vocabulary change left 230
    orphans — 118 of them under the old Chinese filenames — unlinked from
    the cloud, still served, and still listed in a disk-derived sitemap."""
    d = _tagdir(tmp_path, ["crack.html", "破解.html", "rust.html",
                           "index.html"])

    removed = site.prune_tag_pages(d, {"crack"})

    assert sorted(p.name for p in removed) == ["rust.html", "破解.html"]
    assert (d / "crack.html").exists()


def test_prune_keeps_the_cloud_itself(tmp_path):
    """index.html is the tag cloud, not a tag — no entry in `keep` names
    it, so a stem check alone would delete the page that lists them all."""
    d = _tagdir(tmp_path, ["index.html"])

    assert site.prune_tag_pages(d, set()) == []
    assert (d / "index.html").exists()


def test_prune_leaves_non_html_alone(tmp_path):
    d = _tagdir(tmp_path, ["notes.txt", "gone.html"])

    removed = site.prune_tag_pages(d, set())

    assert [p.name for p in removed] == ["gone.html"]
    assert (d / "notes.txt").exists()


def test_prune_on_a_missing_directory_is_not_an_error(tmp_path):
    assert site.prune_tag_pages(tmp_path / "nope", {"a"}) == []


@pytest.mark.anyio
async def test_backfilled_pages_link_only_to_pages_the_backfill_wrote(
        stub, tmp_path, anyio_backend):
    """The pager chain and the write plan come from the same `dates`. If they
    could diverge, a re-render would publish a link to a permalink that this
    very run decided not to write — the archive calendar's old 404 in a new
    place."""
    await site.write_history(None, "dev_tools")

    body = (tmp_path / "daily" / "2026-08-23.html").read_text(encoding="utf-8")
    assert "prev=/daily/2026-08-21.html" in body
    assert "next=/daily/2026-08-25.html" in body

    oldest = (tmp_path / "daily" / "2026-08-21.html").read_text(
        encoding="utf-8")
    assert "prev=None" in oldest
    newest = (tmp_path / "daily" / "2026-08-25.html").read_text(
        encoding="utf-8")
    assert "next=None" in newest


@pytest.mark.anyio
async def test_weekly_pager_steps_by_week_and_the_zh_tree_stays_in_its_tree(
        stub, tmp_path, anyio_backend):
    await site.write_history(None, "dev_tools")

    en = (tmp_path / "weekly" / "2026-W35.html").read_text(encoding="utf-8")
    assert "prev=/weekly/2026-W34.html" in en
    zh = (tmp_path / "zh" / "weekly" / "2026-W35.html").read_text(
        encoding="utf-8")
    assert "prev=/zh/weekly/2026-W34.html" in zh


@pytest.mark.anyio
async def test_the_only_monthly_issue_has_no_neighbours(
        stub, tmp_path, anyio_backend):
    await site.write_history(None, "dev_tools")
    body = (tmp_path / "monthly" / "2026-08.html").read_text(encoding="utf-8")
    assert "prev=None" in body and "next=None" in body


@pytest.mark.anyio
async def test_refresh_previous_rewrites_only_the_neighbour_with_a_next_link(
        stub, tmp_path, anyio_backend):
    """The sweep publishes 2026-08-26; the page before it must stop saying
    it is the newest issue. 2026-08-26 is not in issue_dates yet, which is
    the transaction-timing case the pending slug covers."""
    contexts = {"daily": {"zh": {"period_label": "2026-08-26"},
                          "en": {"period_label": "2026-08-26"}}}
    written = await site.refresh_previous(None, "dev_tools", contexts)

    assert _rel(written, tmp_path) == ["daily/2026-08-25.html",
                                       "zh/daily/2026-08-25.html"]
    html = (tmp_path / "daily" / "2026-08-25.html").read_text("utf-8")
    assert "next=/daily/2026-08-26.html" in html
    assert "prev=/daily/2026-08-23.html" in html


@pytest.mark.anyio
async def test_refresh_previous_of_the_first_issue_writes_nothing(
        stub, tmp_path, anyio_backend):
    contexts = {"daily": {"zh": {"period_label": "2026-08-20"}}}
    assert await site.refresh_previous(None, "dev_tools", contexts) == []
