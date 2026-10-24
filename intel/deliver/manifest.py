"""What a re-render may skip, and the evidence it is allowed to.

`write_history` re-renders every dated permalink in both languages, and
`_write_chrome_lang` every archive month and every tag page. Both are
O(history), and history only grows: the rebuild that cost 28 issue pages and
796 chrome files in August costs ten times that after a year, every Sunday,
to reproduce bytes that are already on disk.

A page is skipped only when a fingerprint of EVERY input that page is built
from is unchanged since the run that wrote it. The direction of every doubt
here is fixed: rendering a page that did not need it wastes a second, while
skipping one that did leaves the site silently serving last month's numbers
under today's URL. So the fingerprint is deliberately over-broad — it covers
inputs a page merely *might* read — and anything that cannot be fingerprinted
(a digest query that failed, a missing manifest, an output file that is not
there) renders.

**What counts as changed**, for an issue page anchored at date `a`:

1. any row of `intel.scores` (with the `intel.items` display columns it is
   joined to) in the page's own window — `[a − window, a]`;
2. any `intel.items` row first seen in that window (the lede's 新增 count),
   or any `intel.runs` row started in it (the lede's provenance and cost);
3. any `intel.star_snapshots` row on ANY date at or before `a` — the gain
   board's readiness is computed per item over every snapshot ≤ `a`, so a
   backfilled snapshot from three weeks ago changes today's board. This is
   why the snapshot digest is a running fold over dates rather than a
   per-date lookup;
4. the page's own identity and neighbours: edition, slug, language,
   vertical, the rubric's `watch_min`, `TIERLIST_SITE_URL` (baked into the
   canonical/OG tags), and the prev/next pager links, which change when a
   NEW issue date lands beside an old one;
5. any byte of the code that renders it — every `.py`, `.j2` and
   `.yaml` under `intel/`. A redesign therefore invalidates the whole
   site by itself, which is the case `--rebuild-site` exists for.

Chrome pages (archive months, the tag cloud, tag pages) are fingerprinted
directly over the arguments passed to their render call, because assembling
those arguments is already cheap — one `tag_items_many` scan for all of
them. There the fingerprint is not an approximation of the inputs; it is
the inputs.

The escape hatch is `TIERLIST_RENDER_FORCE=1` (or `--force-render`): every page
is rendered and the manifest is rewritten from scratch.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
from pathlib import Path

MANIFEST_NAME = ".render-manifest.json"

# Bumped when the meaning of a stored fingerprint changes; an older file is
# discarded rather than trusted, which costs one full render.
FORMAT_VERSION = 1

_TIERLIST_DIR = Path(__file__).resolve().parent.parent

_TRUE = {"1", "true", "yes", "on"}


def force_render() -> bool:
    """`TIERLIST_RENDER_FORCE` — render everything regardless of fingerprints."""
    return os.environ.get("TIERLIST_RENDER_FORCE", "").strip().lower() in _TRUE


@functools.lru_cache(maxsize=1)
def code_fingerprint() -> str:
    """One hash over every `.py`, `.j2`, `.yaml`, `.css` and `.js` under
    `intel/`. The stylesheet and scripts count because every page carries
    their content hash in its asset URLs (`style.css?v=…`).

    Coarse on purpose. A finer dependency map (this template feeds that page)
    would skip more pages and would be wrong the first time a macro moved
    between files — and the failure it would produce is the one this whole
    module must never produce: a permalink frozen at markup the stylesheet
    has moved on from, which is what `--rebuild-site` was written to repair.
    Paths are hashed alongside contents so a rename registers.

    Cached per process: the files cannot change under a running render, and
    a rebuild would otherwise read the tree once per page.
    """
    h = hashlib.sha256()
    files = sorted(
        p for p in (
            list(_TIERLIST_DIR.rglob("*.py"))
            + list(_TIERLIST_DIR.rglob("*.j2"))
            + list(_TIERLIST_DIR.rglob("*.yaml"))
            + list(_TIERLIST_DIR.rglob("*.css"))
            + list(_TIERLIST_DIR.rglob("*.js"))
        ) if "__pycache__" not in p.parts
    )
    for path in files:
        h.update(path.relative_to(_TIERLIST_DIR).as_posix().encode("utf-8"))
        h.update(b"\x00")
        h.update(path.read_bytes())
        h.update(b"\x00")
    return h.hexdigest()[:16]


def digest(*parts: object) -> str:
    """A stable hash of arbitrary context data.

    JSON with sorted keys, so a dict that changes iteration order does not
    change the fingerprint, and `default=str` so dates and Decimals hash as
    what they print as. Parts are separated, so ("ab", "c") and ("a", "bc")
    cannot collide."""
    h = hashlib.sha256()
    for part in parts:
        h.update(json.dumps(part, sort_keys=True, default=str,
                            ensure_ascii=False).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()[:32]


def running_digests(per_date: dict) -> dict:
    """{date: digest} → {date: digest of every entry at or before that date}.

    For inputs whose effect is cumulative — star snapshots, whose per-item
    coverage decides whether the gain board is ready at all. Folded in one
    pass in date order, so an entry inserted for an old date changes that
    date and every date after it, and nothing before."""
    out: dict = {}
    acc = ""
    for day in sorted(per_date):
        acc = digest(acc, str(day), per_date[day])
        out[day] = acc
    return out


def at_or_before(running: dict, day):
    """The running digest as of `day`, even when `day` itself has no entry.

    A date with no snapshots of its own still inherits every earlier one:
    `gain._coverage` measures each item's series up to `as_of`, not only
    that day's rows. Looking the date up directly would treat a gap as
    empty history and a later backfill of the previous day would not
    re-render it."""
    keys = [d for d in running if d <= day]
    return running[max(keys)] if keys else ""


def window_digest(per_date: dict, start, end) -> str:
    """The digest of every dated entry in the closed window [start, end].

    Absent dates contribute nothing rather than a placeholder: a date with
    no rows and a date that was never in the map are the same page."""
    return digest([[str(day), per_date[day]]
                   for day in sorted(per_date) if start <= day <= end])


class Manifest:
    """`<web root>/.render-manifest.json`: page path → input fingerprint.

    It lives INSIDE the web tree on purpose. The manifest is a claim about
    files in that tree, so wiping the tree must wipe the claim; a manifest
    kept beside it would survive a `rm -rf data/web` and skip every page of
    a site that no longer exists. It is a dotfile and the sitemap lists only
    HTML, so no page links to it.

    `current()` still stats the output file. A fingerprint match on a page
    that is not on disk is a manifest that outlived its tree, and the answer
    to it is to render.
    """

    def __init__(self, root: Path, entries: dict[str, str], *,
                 force: bool = False) -> None:
        self.root = root
        self.entries = entries
        self.force = force
        self._touched: set[str] = set()

    @classmethod
    def load(cls, root: Path, *, force: bool | None = None) -> Manifest:
        force = force_render() if force is None else force
        entries: dict[str, str] = {}
        path = root / MANIFEST_NAME
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            if int(blob.get("version", 0)) == FORMAT_VERSION:
                entries = {str(k): str(v)
                           for k, v in (blob.get("pages") or {}).items()}
        except (OSError, ValueError, TypeError):
            entries = {}
        return cls(root, entries, force=force)

    def current(self, rel: str, fingerprint: str | None) -> bool:
        """True when `rel` may be skipped: fingerprints known and equal, and
        the file is actually there. `None` means the caller could not
        fingerprint the inputs — always a render."""
        if self.force or fingerprint is None:
            return False
        if self.entries.get(rel) != fingerprint:
            return False
        return (self.root / rel).exists()

    def record(self, rel: str, fingerprint: str | None) -> None:
        self._touched.add(rel)
        if fingerprint is None:
            self.entries.pop(rel, None)
        else:
            self.entries[rel] = fingerprint

    def invalidate(self, rel: str) -> None:
        """Forget a page — another writer has just overwritten it with bytes
        this manifest cannot vouch for. The daily sweep renders its own issue
        from a live RunResult, not from the store, so the next rebuild must
        not conclude from an unchanged input fingerprint that the file on
        disk is what the store would produce."""
        self._touched.add(rel)
        self.entries.pop(rel, None)

    def keep(self, rel: str) -> None:
        """Mark an untouched entry as still wanted (the page was skipped)."""
        self._touched.add(rel)

    def save(self) -> Path:
        """Write the manifest, dropping entries whose file has gone.

        Only pruned by existence, never by "this run did not mention it":
        `write_site` and `write_history` each own a different slice of the
        tree and run in the same process one after the other, and a run that
        wrote only chrome must not forget every issue page."""
        entries = {k: v for k, v in sorted(self.entries.items())
                   if (self.root / k).exists()}
        self.entries = entries
        path = self.root / MANIFEST_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": FORMAT_VERSION, "pages": entries},
                       ensure_ascii=False, indent=0, sort_keys=True),
            encoding="utf-8", newline="\n")
        return path
