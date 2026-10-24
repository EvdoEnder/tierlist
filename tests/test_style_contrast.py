"""Both palettes must clear WCAG AA (4.5:1) for every text-on-background pair.

A colour token is one character away from failing, and nothing about the
stylesheet says which pairs actually meet as text. `--ink-3` sat at 2.6:1
against the paper for the life of the project — it carries timestamps, the
nav's RSS tail, kicker labels and the footer, so it is text, not decoration,
and nobody noticed because reading a hex value tells you nothing.

The pairs below are the ones the markup actually creates. Adding a palette
means adding its block to _PALETTES; adding a token that lands on a
background means adding the pair.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_CSS = (Path(__file__).resolve().parent.parent / "intel" / "render" / "static"
        / "style.css")

AA = 4.5

# (foreground token, background token) — every combination the templates put
# text on. Both backgrounds appear because cards sit on the page: a token can
# pass on one and fail on the other.
PAIRS = [
    ("--ink", "--paper"), ("--ink", "--card"),
    ("--ink-2", "--paper"), ("--ink-2", "--card"),
    ("--ink-3", "--paper"), ("--ink-3", "--card"),
    ("--accent", "--paper"), ("--accent", "--card"),
    # Filled text: tag chips, the active nav pill and archive days sit on
    # --tint; the top-ranked badge, the logo and a hovered archive day carry
    # --on-accent on --accent.
    ("--tint-ink", "--tint"),
    ("--on-accent", "--accent"),
    # Star gains (▲n) are the one text use of the warm colour; the panels
    # they sit in are cards.
    ("--warm-ink", "--card"), ("--warm-ink", "--paper"),
    # Folded band summaries turn --tint on hover, carrying their name and count.
    ("--ink-2", "--tint"), ("--ink-3", "--tint"),
]


def _palettes() -> dict[str, dict[str, str]]:
    """{palette name: {token: value}} — the bare :root block is "light", the
    one inside the prefers-color-scheme query is "dark"."""
    css = _CSS.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    blocks = re.findall(r":root\s*\{([^}]*)\}", css)
    assert len(blocks) == 2, f"expected light + dark :root blocks, got {len(blocks)}"
    out = {}
    for name, block in zip(("light", "dark"), blocks, strict=True):
        out[name] = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block))
    # Dark redefines only what changes; anything it omits it inherits.
    out["dark"] = {**out["light"], **out["dark"]}
    return out


def _rgb(value: str) -> tuple[float, float, float, float]:
    value = value.strip()
    if value.startswith("#"):
        h = value[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    nums = [float(n) for n in re.findall(r"[\d.]+", value)]
    return (nums[0], nums[1], nums[2], nums[3] if len(nums) > 3 else 1.0)


def _luminance(c) -> float:
    def chan(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * chan(c[0]) + 0.7152 * chan(c[1]) + 0.0722 * chan(c[2])


def _contrast(fg: str, bg: str) -> float:
    f, b = _rgb(fg), _rgb(bg)
    # A translucent foreground is composited over its background first;
    # comparing the raw colour would overstate the ratio.
    f = tuple(f[i] * f[3] + b[i] * (1 - f[3]) for i in range(3)) + (1.0,)
    lf, lb = _luminance(f), _luminance(b)
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("palette", ["light", "dark"])
@pytest.mark.parametrize("fg,bg", PAIRS)
def test_text_pair_meets_wcag_aa(palette, fg, bg):
    tokens = _palettes()[palette]
    for token in (fg, bg):
        assert token in tokens, f"{palette} palette has no {token}"
    ratio = _contrast(tokens[fg], tokens[bg])
    assert ratio >= AA, (
        f"{palette}: {fg} ({tokens[fg]}) on {bg} ({tokens[bg]}) is "
        f"{ratio:.2f}:1, below WCAG AA {AA}:1"
    )


def test_both_palettes_define_every_token():
    """The dark block overrides a subset; a token it forgets silently keeps
    its light value, which is the one failure mode a per-pair check cannot
    see (the pair still passes — against the wrong background)."""
    light = _palettes()["light"]
    css = re.sub(r"/\*.*?\*/", " ", _CSS.read_text(encoding="utf-8"), flags=re.S)
    dark_block = re.findall(r":root\s*\{([^}]*)\}", css)[1]
    declared_dark = set(re.findall(r"(--[\w-]+)\s*:", dark_block))
    # Colour tokens are identified by their VALUE, not by an exclusion list:
    # the light block also carries the font stack and radii, which are not
    # something the dark palette should be overriding. A name-based skip list
    # would have to be extended by hand every time a non-colour token is
    # added, and the cost of forgetting is a test that stops checking.
    colour_tokens = {t for t, v in light.items() if v.strip().startswith("#")}
    missing = colour_tokens - declared_dark
    assert not missing, f"dark palette never overrides: {sorted(missing)}"


def test_the_math_is_right():
    """Anchors against values WCAG publishes, so a broken formula cannot make
    every pair pass."""
    assert _contrast("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.01)
    assert _contrast("#FFFFFF", "#FFFFFF") == pytest.approx(1.0, abs=0.01)
    assert _contrast("#767676", "#FFFFFF") == pytest.approx(4.54, abs=0.02)
