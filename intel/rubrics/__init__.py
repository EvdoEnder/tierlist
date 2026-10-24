"""Per-vertical judgment standards: YAML files, one per vertical.

The loader is the only code that knows the file shape; everything else takes
a `Rubric`. The weights carry the rubric's taste, so the four dimensions are
NOT averaged flat.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class Rubric:
    vertical: str
    dimensions: dict[str, str]   # name → what the model should judge, 0–25 each
    weights: dict[str, float]    # frozen-decision weighting for the total
    prompt_head: str             # role line the scorer opens with
    watch_min: int               # weighted total ≥ this enters the Top 榜


_RUBRIC_DIR = Path(__file__).resolve().parent


def load_rubric(vertical: str) -> Rubric:
    path = _RUBRIC_DIR / f"{vertical}.yaml"
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    dimensions = dict(raw["dimensions"])
    weights = {name: float(raw["weights"][name]) for name in dimensions}
    return Rubric(
        vertical=vertical,
        dimensions=dimensions,
        weights=weights,
        prompt_head=str(raw["prompt_head"]).strip(),
        watch_min=int(raw["thresholds"]["watch_min"]),
    )
