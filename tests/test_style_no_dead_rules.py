"""Every class in style.css must be reachable from some template or from
search.js.

Four rules had outlived their markup — `.hrow` (renamed to `.hitem`),
`.xcard .more`, `.rss-note` and `.nsi-meta+.nsi-meta`. None of them cost
anything at runtime; the cost is to the reader, who cannot tell a selector
that styles nothing from one that styles something rare, and so edits the
stylesheet defensively forever.

The check is deliberately crude — a class name appearing ANYWHERE in a
template or in the JS counts as used, including inside a comment. It cannot
prove a rule is live; it only catches the name that no longer exists at all,
which is the case every one of those four was.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent / "intel"
_CSS = _ROOT / "render" / "static" / "style.css"

# The tag cloud's size tiers are BUILT, not written: render_tags_index emits
# f"t{n}", so the literal "t2" appears nowhere. Exempting them is safe only
# because test_tag_tiers_really_are_emitted below proves they still reach the
# markup — an exemption list nobody checks is the same rot in a new place.
_EXEMPT = {"t0", "t1", "t2", "t3"}
# Built as `tm-{{ tr|lower }}` / `band-{{ tr|lower }}` in _parts.html.j2, so
# never spelled out.
_TIER_BADGES = {"band-s", "tm-c", "tm-d"}


def _declared_classes() -> set[str]:
    """Class names appearing in a selector position. Selectors only: the
    declaration blocks are stripped first so a value like `.5em` or a colour
    name cannot be mistaken for a class."""
    css = _CSS.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)      # comments
    css = re.sub(r"\{[^{}]*\}", " ", css)                 # declaration blocks
    return set(re.findall(r"\.(-?[A-Za-z_][\w-]*)", css))


def _used_names() -> str:
    """Everything a class could be written in: the templates and the one
    script that builds DOM nodes."""
    parts = [p.read_text(encoding="utf-8")
             for p in sorted((_ROOT / "templates").glob("*.j2"))]
    parts.append((_ROOT / "render" / "static" / "search.js")
                 .read_text(encoding="utf-8"))
    return "\n".join(parts)


@pytest.mark.parametrize("cls", sorted(_declared_classes()))
def test_class_is_reachable_from_markup(cls):
    if cls in _EXEMPT | _TIER_BADGES:
        pytest.skip("applied dynamically, not spelled out in source")
    used = _used_names()
    assert re.search(rf"\b{re.escape(cls)}\b", used), (
        f".{cls} is styled in style.css but no template or script ever "
        f"applies it — delete the rule or the class it was renamed from."
    )


def test_the_check_can_actually_fail():
    """A parametrized test over a computed list passes vacuously when the
    list is empty or the matcher is too loose. This pins both: the scan finds
    a real number of classes, and a name that is not in the markup is
    reported as unused."""
    declared = _declared_classes()
    assert len(declared) > 40, "selector scan collapsed — it found almost nothing"
    used = _used_names()
    assert not re.search(r"\bhrow\b", used), (
        "the .hrow markup came back; update this test rather than the rule"
    )


def test_tag_tiers_really_are_emitted():
    """Pins the exemption above: every t0..t3 class the stylesheet defines is
    still produced by the tag cloud for some tag count."""
    from intel.render import site
    from intel.tags import vocabulary

    # Only topics are sized; names (entities) print as plain links.
    counts = [0, 1, 3, 6, 12, 99]
    topics = vocabulary().topics
    html = site.render_tags_index(
        [{"tag": slug, "count": n, "url": f"/tags/{slug}.html"}
         for slug, n in zip(topics, counts, strict=False)])
    for tier in sorted(_EXEMPT):
        assert f'class="{tier}"' in html, (
            f"{tier} is exempted from the dead-rule scan but the tag cloud "
            f"no longer emits it — drop the CSS rule and the exemption."
        )


def test_tier_badges_really_are_emitted():
    """Pins the exemption: an S, a C and a D entry carry their classes."""
    from intel.render import site

    entries = [{"rank": f"0{i}", "name": f"o/r{i}", "url": "u", "stars": 1,
                "lang": "Go", "score": score, "description": "", "reason": "",
                "tags": []}
               for i, score in enumerate((80, 45, 20), 1)]
    html = site.render_issue({
        "edition": "weekly", "edition_label": "w", "date": "2026-10-22",
        "period_label": "W43", "lang": "zh", "p": "",
        "nav": {k: f"/{k}/" for k in ("daily", "weekly", "monthly", "news",
                                       "tags", "archive", "search")},
        "lede": {"window_from": "2026-10-16", "window_to": "2026-10-22",
                 "new_count": None, "fetched": None, "candidates": None,
                 "sent": None, "scored": None, "model": None, "cost": None,
                 "watch_min": None, "top_score": 80},
        "entries": entries, "hn_entries": [], "gain_board": {
            "label": "g", "window_days": 7, "ready": False, "need": 7,
            "have": 0, "rows": []},
        "hot_new": None, "trending": None, "archive_recent": {}})
    for cls in sorted(_TIER_BADGES):
        assert re.search(rf"\b{cls}\b", html), cls
