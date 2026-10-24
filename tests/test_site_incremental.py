"""Incremental rendering: which pages a re-render is allowed to skip.

`write_history` used to re-render every dated permalink in both languages on
every run, and `_write_chrome_lang` every archive month and every tag page.
This suite pins the half of that behaviour that is dangerous to get wrong:
a page whose inputs changed must be rendered again. Every skip test here has
a twin that changes one digest and demands the page come back.

Offline, like test_site_history: the store reads, the digest query and the
Jinja render are all stubbed, because what is under test is the DECISION,
not the SQL.
"""
from __future__ import annotations

from datetime import date

import pytest

from intel import editions
from intel.deliver import manifest as manifest_mod
from intel.deliver import site
from intel.store import db

ISSUE_DATES = [date(2026, 8, 21),   # 2026-W34, 2026-08
               date(2026, 8, 23),   # 2026-W34, 2026-08  (last of its week)
               date(2026, 8, 25)]   # 2026-W35, 2026-08  (last of week+month)

# 6 slugs (3 daily + 2 weekly + 1 monthly) x 2 languages.
ALL_PAGES = 12


def _digests(dates, marker: str = "v1") -> dict:
    return {kind: {d: f"{kind}-{d}-{marker}" for d in dates}
            for kind in ("scores", "items", "runs", "snaps")}


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A stubbed store + renderer whose digests a test can edit in place.

    `world.digests` is what `db.page_input_digests` returns and
    `world.dates` what `db.issue_dates` returns; both are read fresh on
    every call, so a test mutates them between runs the way the store would.
    """
    monkeypatch.setenv("TIERLIST_WEB_DIR", str(tmp_path))
    monkeypatch.delenv("TIERLIST_RENDER_FORCE", raising=False)

    class World:
        root = tmp_path
        dates = list(ISSUE_DATES)
        digests = _digests(ISSUE_DATES)
        body = "v1"
        rendered: list[tuple[str, str, date]] = []

        async def run(self, **kw):
            self.rendered.clear()
            stats: dict = {}
            written = await site.write_history(None, "dev_tools",
                                               stats=stats, **kw)
            return stats, written

    w = World()

    async def issue_dates(_conn):
        return list(w.dates)

    async def page_input_digests(_conn):
        return {k: dict(v) for k, v in w.digests.items()}

    async def daily_db_context(_conn, vertical, *, run_date, lang,
                               watch_min=None):
        w.rendered.append(("daily", lang, run_date))
        return {"edition": "daily", "lang": lang, "anchor": run_date}

    async def aggregate_context(_conn, edition, vertical, *, lang, now=None):
        w.rendered.append((edition, lang, now.date()))
        return {"edition": edition, "lang": lang, "anchor": now.date()}

    monkeypatch.setattr(db, "issue_dates", issue_dates)
    monkeypatch.setattr(db, "page_input_digests", page_input_digests)
    monkeypatch.setattr(editions, "daily_db_context", daily_db_context)
    monkeypatch.setattr(editions, "aggregate_context", aggregate_context)

    from intel.render import site as render

    def _issue(ctx):
        return (f"<html>{w.body}|{ctx['edition']}|{ctx['lang']}|"
                f"{ctx['anchor']}|{ctx['page_path']}</html>")

    monkeypatch.setattr(render, "render_issue", _issue)
    return w


def _pages(world) -> set[tuple[str, date]]:
    """The (edition, anchor) pairs rendered in the en tree, the site root."""
    return {(edition, anchor) for edition, lang, anchor in world.rendered
            if lang == "en"}


# ------------------------------------------------------------------ the skip

@pytest.mark.anyio
async def test_a_second_run_over_an_unchanged_store_renders_nothing(
        world, anyio_backend):
    first, _ = await world.run()
    assert first == {"rendered": ALL_PAGES, "skipped": 0, "pages": ALL_PAGES}

    second, written = await world.run()

    assert second == {"rendered": 0, "skipped": ALL_PAGES,
                      "pages": ALL_PAGES}
    assert written == []
    assert world.rendered == []


@pytest.mark.anyio
async def test_the_pages_are_still_on_disk_after_a_skipping_run(
        world, anyio_backend):
    await world.run()
    await world.run()
    for day in ISSUE_DATES:
        body = (world.root / "zh" / "daily" / f"{day}.html").read_text(
            encoding="utf-8")
        assert f"daily|zh|{day}" in body


# ------------------------------------------- what counts as changed: the data

@pytest.mark.parametrize("kind", ["scores", "items", "runs"])
@pytest.mark.anyio
async def test_a_changed_row_in_a_days_window_re_renders_that_day(
        world, kind, anyio_backend):
    """Scores (and the item columns beside them), items first seen that day,
    and the run that produced the lede: each is an input of the day's page,
    and each must bring it back."""
    await world.run()
    world.digests[kind][date(2026, 8, 21)] = "changed"
    world.body = "v2"

    stats, _ = await world.run()

    assert ("daily", date(2026, 8, 21)) in _pages(world)
    # …and the aggregates whose window covers 08-21, but not the other dailies.
    assert ("daily", date(2026, 8, 23)) not in _pages(world)
    assert ("daily", date(2026, 8, 25)) not in _pages(world)
    assert stats["skipped"] == ALL_PAGES - stats["rendered"]
    assert "v2" in (world.root / "daily" / "2026-08-21.html").read_text(
        encoding="utf-8")


@pytest.mark.anyio
async def test_a_changed_day_re_renders_every_aggregate_whose_window_holds_it(
        world, anyio_backend):
    """08-21 sits in W34 (anchor 08-23), in W35's 7-day window (anchor
    08-25) and in the month. A weekly page that kept its old bytes would
    publish a week built from a row that has since changed."""
    await world.run()
    world.digests["scores"][date(2026, 8, 21)] = "changed"

    await world.run()

    assert _pages(world) == {("daily", date(2026, 8, 21)),
                             ("weekly", date(2026, 8, 23)),
                             ("weekly", date(2026, 8, 25)),
                             ("monthly", date(2026, 8, 25))}


@pytest.mark.anyio
async def test_a_backfilled_star_snapshot_re_renders_every_later_issue(
        world, anyio_backend):
    """The gain board's readiness is measured over each item's WHOLE series
    up to the issue's date, so a snapshot written for an old date changes
    every board after it — not just that date's page."""
    await world.run()
    world.digests["snaps"][date(2026, 8, 21)] = "resnapshotted"

    await world.run()

    assert _pages(world) == {("daily", date(2026, 8, 21)),
                             ("daily", date(2026, 8, 23)),
                             ("daily", date(2026, 8, 25)),
                             ("weekly", date(2026, 8, 23)),
                             ("weekly", date(2026, 8, 25)),
                             ("monthly", date(2026, 8, 25))}


@pytest.mark.anyio
async def test_a_backfill_reaches_a_later_issue_with_no_snapshots_of_its_own(
        world, anyio_backend):
    """08-25 has scores but no snapshot row of its own (the store currently
    has 9 scored days and 8 snapshot days). Looking the running digest up
    by exact date would treat that gap as empty history, so a resnapshot
    of 08-23 would not bring 08-25 back even though its gain board is
    computed over every snapshot ≤ 08-25."""
    world.digests["snaps"].pop(date(2026, 8, 25), None)
    await world.run()
    world.digests["snaps"][date(2026, 8, 23)] = "resnapshotted"

    await world.run()

    assert ("daily", date(2026, 8, 25)) in _pages(world)
    assert ("daily", date(2026, 8, 21)) not in _pages(world)


@pytest.mark.anyio
async def test_a_snapshot_for_a_new_day_leaves_earlier_issues_alone(
        world, anyio_backend):
    """The other half of the cumulative rule: today's snapshots are not an
    input of a page anchored before today, so the fold must not drag them
    in. Without this the daily refresh would re-render the whole archive."""
    await world.run()
    world.digests["snaps"][date(2026, 8, 26)] = "todays-refresh"

    stats, _ = await world.run()

    assert _pages(world) == set()
    assert stats["skipped"] == ALL_PAGES


# ------------------------------------ what counts as changed: the page itself

@pytest.mark.anyio
async def test_a_new_issue_date_re_renders_the_neighbour_it_links_to(
        world, anyio_backend):
    """The pager is part of the page. 08-25 was the newest issue and its
    'next' was empty; once 08-27 exists that page is wrong until it is
    rendered again."""
    await world.run()
    world.dates.append(date(2026, 8, 27))
    world.digests = _digests(world.dates)

    await world.run()

    assert ("daily", date(2026, 8, 25)) in _pages(world)
    assert ("daily", date(2026, 8, 27)) in _pages(world)
    # 08-21's neighbours are untouched and no window of its own moved.
    assert ("daily", date(2026, 8, 21)) not in _pages(world)


@pytest.mark.anyio
async def test_a_missing_output_file_is_rendered_even_when_nothing_changed(
        world, anyio_backend):
    """A fingerprint match is a claim about a file. Delete the file and the
    claim is void — this is what keeps a manifest that outlived its tree
    from skipping a site that is not there."""
    await world.run()
    (world.root / "daily" / "2026-08-23.html").unlink()

    await world.run()

    assert ("daily", date(2026, 8, 23)) in _pages(world)


@pytest.mark.anyio
async def test_changed_render_code_re_renders_everything(
        world, monkeypatch, anyio_backend):
    """A redesign changes no row in the store. The code fingerprint is what
    makes `--rebuild-site` still reach every dated page after it."""
    await world.run()
    monkeypatch.setattr(manifest_mod, "code_fingerprint", lambda: "redesign")

    stats, _ = await world.run()

    assert stats == {"rendered": ALL_PAGES, "skipped": 0, "pages": ALL_PAGES}


@pytest.mark.anyio
async def test_a_changed_yaml_re_renders_everything(
        world, monkeypatch, anyio_backend):
    """watch_min lives in intel/rubrics/*.yaml; a threshold change is a
    redesign of every daily page's below-the-line annotation, so yaml is
    in the code fingerprint rather than a second, narrower one."""
    await world.run()
    monkeypatch.setattr(manifest_mod, "code_fingerprint",
                        lambda: "yaml-edit")

    stats, _ = await world.run()

    assert stats["rendered"] == ALL_PAGES


@pytest.mark.anyio
async def test_a_changed_site_url_re_renders_everything(
        world, monkeypatch, anyio_backend):
    """TIERLIST_SITE_URL is baked into every canonical/OG/hreflang tag."""
    await world.run()
    monkeypatch.setenv("TIERLIST_SITE_URL", "https://example.test")

    stats, _ = await world.run()

    assert stats["rendered"] == ALL_PAGES


# ------------------------------------------------------------ the escape hatch

@pytest.mark.anyio
async def test_force_renders_every_page(world, anyio_backend):
    await world.run()

    stats, _ = await world.run(force=True)

    assert stats == {"rendered": ALL_PAGES, "skipped": 0, "pages": ALL_PAGES}


@pytest.mark.anyio
async def test_the_env_escape_hatch_renders_every_page(
        world, monkeypatch, anyio_backend):
    await world.run()
    monkeypatch.setenv("TIERLIST_RENDER_FORCE", "1")

    stats, _ = await world.run()

    assert stats["rendered"] == ALL_PAGES


@pytest.mark.anyio
async def test_a_store_that_cannot_be_digested_renders_every_page(
        world, monkeypatch, anyio_backend):
    """Fail open: no digests, no skipping — and not just on the first run
    after the failure. A run that fingerprints pages from an empty digest
    map would agree with itself the second time and skip the whole site
    while the store is exactly as unreadable as it was."""

    async def boom(_conn):
        raise RuntimeError("no such function")

    monkeypatch.setattr(db, "page_input_digests", boom)

    first, _ = await world.run()
    second, _ = await world.run()

    assert first["rendered"] == ALL_PAGES
    assert second["rendered"] == ALL_PAGES


@pytest.mark.anyio
async def test_a_manifest_from_an_older_format_is_not_trusted(
        world, anyio_backend):
    await world.run()
    path = world.root / manifest_mod.MANIFEST_NAME
    path.write_text('{"version": 0, "pages": {}}', encoding="utf-8")

    stats, _ = await world.run()

    assert stats["rendered"] == ALL_PAGES


@pytest.mark.anyio
async def test_a_corrupt_manifest_is_not_fatal(world, anyio_backend):
    await world.run()
    (world.root / manifest_mod.MANIFEST_NAME).write_text(
        "{not json", encoding="utf-8")

    stats, _ = await world.run()

    assert stats["rendered"] == ALL_PAGES


# ------------------------------------------------------- the two writers meet

@pytest.mark.anyio
async def test_a_page_the_sweep_overwrote_is_rendered_again(
        world, anyio_backend):
    """The daily sweep renders its issue from a live RunResult, not from the
    store, so the file it leaves behind is not what write_history would have
    produced. The manifest entry goes with it, or the next rebuild would
    call an unchanged input digest proof that the file is current."""
    await world.run()
    man = manifest_mod.Manifest.load(world.root)
    man.invalidate("daily/2026-08-23.html")
    man.save()

    await world.run()

    assert ("daily", date(2026, 8, 23)) in _pages(world)


@pytest.mark.anyio
async def test_the_rolling_index_is_current_only_on_its_own_evidence(
        world, anyio_backend):
    """`/daily/index.html` holds the newest issue's bytes but is rewritten
    by other writers; it is keyed separately, so losing it brings the pair
    back even though the permalink is intact."""
    await world.run()
    (world.root / "daily" / "index.html").unlink()

    await world.run()

    assert ("daily", date(2026, 8, 25)) in _pages(world)
    assert (world.root / "daily" / "index.html").exists()


# ------------------------------------------------------------- chrome pages

class _Render:
    """Just enough of intel.render.site for _write_chrome_lang."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def render_archive_page(self, month, sections, *, lang, prev_url,
                            next_url):
        self.calls.append(f"archive:{month}:{lang}")
        return f"<archive {month} {lang} {sections} {prev_url} {next_url}>"

    def render_tags_index(self, cloud, *, lang):
        self.calls.append(f"tags-index:{lang}")
        return f"<cloud {lang} {cloud}>"

    def render_tag_page(self, tag, items, *, lang):
        self.calls.append(f"tag:{tag}:{lang}")
        return f"<tag {tag} {lang} {items}>"

    def render_tiers_index(self, tiers, months, *, lang):
        self.calls.append(f"tiers-index:{lang}")
        return f"<tiers {lang} {tiers} {months}>"

    def render_tier_page(self, tier, month, entries, *, months, tiers, lang):
        self.calls.append(f"tier:{tier}:{month}:{lang}")
        return f"<tier {tier} {month} {lang} {entries} {months}>"


class _FrozenDate(date):
    """`date`, with today pinned inside the month ISSUE_DATES covers."""

    @classmethod
    def today(cls) -> date:
        return date(2026, 8, 26)


@pytest.fixture
def chrome(monkeypatch, tmp_path):
    monkeypatch.setenv("TIERLIST_WEB_DIR", str(tmp_path))
    monkeypatch.delenv("TIERLIST_RENDER_FORCE", raising=False)

    # `_write_chrome_lang` reads `date.today()` and `archive_cells` always
    # includes the current month, so an unfrozen clock adds a month to the
    # archive as soon as the wall calendar leaves the one ISSUE_DATES sits in.
    # That is a test about the month it runs in rather than about the code: it
    # passed through August 2026 and failed from 2026-09-01.
    monkeypatch.setattr(site, "date", _FrozenDate)

    state = {"items": {"rust": [{"name": "a", "score": 70}],
                       "mcp": [{"name": "b", "score": 60}]},
             "dates": list(ISSUE_DATES), "rated": []}

    async def issue_dates(_conn):
        return list(state["dates"])

    async def tag_items_many(_conn, limit=200):
        return {k: list(v) for k, v in state["items"].items()}

    async def tag_cloud(_conn):
        return [{"tag": t, "count": len(v), "url": f"/tags/{t}.html"}
                for t, v in state["items"].items()]

    async def tier_history_rows(_conn):
        return list(state["rated"])

    monkeypatch.setattr(db, "issue_dates", issue_dates)
    monkeypatch.setattr(db, "tier_history_rows", tier_history_rows)
    monkeypatch.setattr(site, "tag_items_many", tag_items_many)
    monkeypatch.setattr(site, "tag_cloud", tag_cloud)
    return state


async def _chrome_run(root, render) -> list:
    written: list = []
    await site._write_chrome_lang(None, render, root, "en", written)
    return written


@pytest.mark.anyio
async def test_chrome_pages_are_rendered_once_and_then_skipped(
        chrome, tmp_path, anyio_backend):
    render = _Render()
    first = await _chrome_run(tmp_path, render)
    assert set(render.calls) == {"archive:2026-08:en", "tags-index:en",
                                 "tag:rust:en", "tag:mcp:en", "tiers-index:en"}
    assert len(first) == 6   # + /archive/index.html, the newest month again

    render.calls.clear()
    second = await _chrome_run(tmp_path, render)

    assert render.calls == []
    assert second == []
    assert (tmp_path / "tags" / "rust.html").exists()


@pytest.mark.anyio
async def test_a_tag_whose_projects_changed_is_rendered_again(
        chrome, tmp_path, anyio_backend):
    render = _Render()
    await _chrome_run(tmp_path, render)
    chrome["items"]["rust"] = [{"name": "a", "score": 88}]
    render.calls.clear()

    await _chrome_run(tmp_path, render)

    assert "tag:rust:en" in render.calls
    assert "tag:mcp:en" not in render.calls   # its own rows did not move
    assert "88" in (tmp_path / "tags" / "rust.html").read_text(
        encoding="utf-8")


@pytest.mark.anyio
async def test_a_new_tag_re_renders_the_cloud_but_not_the_other_pages(
        chrome, tmp_path, anyio_backend):
    render = _Render()
    await _chrome_run(tmp_path, render)
    chrome["items"]["zig"] = [{"name": "c", "score": 50}]
    render.calls.clear()

    await _chrome_run(tmp_path, render)

    assert "tags-index:en" in render.calls   # the cloud gained an entry
    assert "tag:zig:en" in render.calls
    assert "tag:rust:en" not in render.calls


@pytest.mark.anyio
async def test_a_new_issue_date_re_renders_its_archive_month(
        chrome, tmp_path, anyio_backend):
    render = _Render()
    await _chrome_run(tmp_path, render)
    chrome["dates"].append(date(2026, 8, 27))
    render.calls.clear()

    await _chrome_run(tmp_path, render)

    # Asserted on the page, not on the call: /archive/index.html is the
    # newest month rendered a second time, so a call named after the month
    # alone cannot tell the two apart.
    assert "2026-08-27" in (tmp_path / "archive" / "2026-08.html").read_text(
        encoding="utf-8")
    assert "tag:rust:en" not in render.calls


# ------------------------------------------------------------------- helpers

def test_running_digests_folds_in_date_order():
    """The fold must depend on the ORDER of the dates, not only on the set:
    two days' snapshots swapping dates is a different history."""
    a = manifest_mod.running_digests({date(2026, 8, 1): "x",
                                      date(2026, 8, 2): "y"})
    b = manifest_mod.running_digests({date(2026, 8, 1): "y",
                                      date(2026, 8, 2): "x"})
    assert a[date(2026, 8, 1)] != b[date(2026, 8, 1)]
    assert a[date(2026, 8, 2)] != b[date(2026, 8, 2)]


def test_at_or_before_inherits_the_previous_days_fold():
    """A date with no entry of its own still sees every earlier one."""
    running = manifest_mod.running_digests({date(2026, 8, 21): "a",
                                            date(2026, 8, 23): "b"})
    assert manifest_mod.at_or_before(running, date(2026, 8, 22)) == running[
        date(2026, 8, 21)]
    assert manifest_mod.at_or_before(running, date(2026, 8, 25)) == running[
        date(2026, 8, 23)]
    assert manifest_mod.at_or_before(running, date(2026, 8, 1)) == ""


def test_window_digest_ignores_dates_outside_the_window():
    per_date = {date(2026, 8, 1): "a", date(2026, 8, 5): "b"}
    inside = manifest_mod.window_digest(per_date, date(2026, 8, 4),
                                        date(2026, 8, 6))
    assert inside == manifest_mod.window_digest({date(2026, 8, 5): "b"},
                                                date(2026, 8, 4),
                                                date(2026, 8, 6))


def test_digest_parts_cannot_run_together():
    assert manifest_mod.digest("ab", "c") != manifest_mod.digest("a", "bc")


def test_a_stale_entry_is_dropped_when_its_page_is_gone(tmp_path):
    """The manifest is pruned by existence only, so it cannot grow without
    bound as slugs come and go."""
    (tmp_path / "kept.html").write_text("x", encoding="utf-8")
    man = manifest_mod.Manifest(tmp_path, {"kept.html": "fp",
                                           "gone.html": "fp"})

    man.save()

    assert set(manifest_mod.Manifest.load(tmp_path).entries) == {"kept.html"}


def _rated(item_id, day, score):
    return {"item_id": item_id, "snap_date": date.fromisoformat(day),
            "score": score, "dims": None, "model_version": None,
            "reason": "理由", "reason_en": "A reason", "verdict": None,
            "name": f"o/{item_id}", "url": f"https://github.com/o/{item_id}",
            "source": "github", "lang": "Go", "stars": 5,
            "description": "d", "description_en": None, "tags": []}


@pytest.mark.anyio
async def test_tier_pages_are_written_per_month_and_skipped_when_unchanged(
        chrome, tmp_path, anyio_backend):
    chrome["rated"] = [_rated("a", "2026-08-25", 99),
                       _rated("b", "2026-08-24", 99),
                       _rated("c", "2026-07-30", 99)]
    render = _Render()
    await _chrome_run(tmp_path, render)
    tiers = {c for c in render.calls if c.startswith("tier:")}
    assert tiers == {"tier:S:2026-08:en", "tier:S:2026-07:en"}
    s_dir = tmp_path / "tiers" / "s"     # English sits at the root
    newest = (s_dir / "2026-08.html").read_text(encoding="utf-8")
    assert (s_dir / "index.html").read_text(encoding="utf-8") == newest

    render.calls.clear()
    await _chrome_run(tmp_path, render)
    assert render.calls == []
