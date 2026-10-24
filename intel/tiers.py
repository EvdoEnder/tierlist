"""Score → public tier (S/A/B/C/D).

The four-dimension score is still what the model produces and what the
store keeps; readers only ever see the tier it falls in. The lines live in
the rubric (`tiers:` in `intel/rubrics/<vertical>.yaml`) because they are
percentiles of that rubric's own score distribution, and a rubric with a
different taste needs lines of its own.

A score made under an earlier rubric keeps that rubric's lines
(`legacy_tiers:`). The stored `dims` say which rubric made it: each taste
judges its own set of dimensions, so the key set identifies the taste
without a date or a version column.

The same rubric judged by another model version scores differently, so a
score also carries the version that made it (`intel.scores.model_version`)
and is read against that version's lines. Those lines are measured by
`intel.calibrate` and kept in `intel.tier_lines`; a process that renders
loads them once with `use_store_lines`. A score with no version predates
the column and keeps the rubric's own `tiers:` lines, which were measured on
the model that made it.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

TIERS = ("S", "A", "B", "C", "D")
_RUBRIC_DIR = Path(__file__).resolve().parent / "rubrics"

Lines = tuple[tuple[int, str], ...]


def _pairs(raw: dict, vertical: str) -> Lines:
    pairs = tuple((int(raw[t]), t) for t in TIERS[:-1])
    if [n for n, _ in pairs] != sorted((n for n, _ in pairs), reverse=True):
        raise ValueError(f"{vertical} tier lines must descend S > A > B > C")
    return pairs


@cache
def _load(vertical: str) -> tuple[Lines, dict[frozenset[str], Lines], frozenset[str]]:
    with open(_RUBRIC_DIR / f"{vertical}.yaml", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    legacy = {frozenset(entry["dims"]): _pairs(entry, vertical)
              for entry in raw.get("legacy_tiers") or ()}
    return _pairs(raw["tiers"], vertical), legacy, frozenset(raw["dimensions"])


# (vertical, dimension set, model version) → lines, from intel.tier_lines;
# and per (vertical, dimension set) the lines of the newest row of all.
_by_version: dict[tuple[str, frozenset[str], str], Lines] = {}
_newest: dict[tuple[str, frozenset[str]], Lines] = {}


def use_store_lines(rows) -> None:
    """Load the measured lines (`db.tier_line_rows`, oldest first), replacing
    whatever was loaded before. A later row for the same version wins."""
    _by_version.clear()
    _newest.clear()
    for r in rows:
        dims = frozenset(r["dims"])
        pairs = _pairs({"S": r["s_line"], "A": r["a_line"], "B": r["b_line"],
                        "C": r["c_line"]}, r["vertical"])
        _by_version[(r["vertical"], dims, r["model_version"])] = pairs
        _newest[(r["vertical"], dims)] = pairs


def has_store_lines(vertical: str, version: str) -> bool:
    """Whether `version` has measured lines for the current rubric."""
    return (vertical, _load(vertical)[2], version) in _by_version


def lines(vertical: str = "dev_tools", dims=None, version: str | None = None) -> Lines:
    """(minimum score, tier) pairs, highest line first; D has no line.

    `dims` is the stored per-dimension breakdown (or just its keys). When it
    matches an earlier rubric's dimensions, that rubric's lines apply.
    Otherwise `version` decides: no version means a score from before
    versions were recorded, read against the rubric's own lines; a version
    with measured lines gets them; a version still waiting for its
    calibration borrows the newest measured lines, which belong to the
    version it replaced."""
    current, legacy, dimset = _load(vertical)
    if dims:
        old = legacy.get(frozenset(dims))
        if old is not None:
            return old
    if version is None:
        return current
    measured = _by_version.get((vertical, dimset, version))
    if measured is not None:
        return measured
    return _newest.get((vertical, dimset), current)


def fingerprint(vertical: str = "dev_tools") -> str:
    """Every line a page of `vertical` can be read against, as one string, so
    a page fingerprint changes when a calibration moves a line."""
    current, legacy, _ = _load(vertical)
    measured = sorted((sorted(k[1]), k[2], v) for k, v in _by_version.items()
                      if k[0] == vertical)
    return repr((current, sorted((sorted(k), v) for k, v in legacy.items()), measured))


def tier(score: int | None, vertical: str = "dev_tools",
         dims=None, version: str | None = None) -> str | None:
    """The tier `score` falls in; None for an item that was never scored."""
    if score is None:
        return None
    for line, name in lines(vertical, dims, version):
        if score >= line:
            return name
    return "D"


def rank(name: str | None) -> int:
    """Sort key, higher is better: S=5 … D=1, unrated 0."""
    return len(TIERS) - TIERS.index(name) if name in TIERS else 0
