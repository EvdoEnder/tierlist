"""The YAML loaders must reproduce the frozen decisions exactly."""
from __future__ import annotations

from pathlib import Path

from intel.pipeline import load_config
from intel.rubrics import load_rubric

_ROOT = Path(__file__).resolve().parent.parent


def test_dev_tools_rubric_matches_the_frozen_decision():
    rubric = load_rubric("dev_tools")
    assert set(rubric.dimensions) == {"novelty", "utility", "craft",
                                      "momentum"}
    # The public taste: no commercial dimension, momentum weighted down so
    # the board is not a star count.
    assert rubric.weights == {"novelty": 1.2, "utility": 1.2,
                              "craft": 1.0, "momentum": 0.8}
    assert rubric.watch_min == 60


def test_every_dimension_carries_a_weight():
    rubric = load_rubric("dev_tools")
    assert set(rubric.weights) == set(rubric.dimensions)


def test_config_loads_with_milestone_one_shape():
    cfg = load_config(_ROOT / "config.yaml")
    assert cfg.lookback_hours == 72
    assert 0.0 < cfg.temperature <= 1.0
    # Every new repository is rated, in requests of SCORE_BATCH; each request
    # must stay under the pinned provider's 131072-token output limit.
    from intel.score.llm import SCORE_BATCH, score_max_tokens
    assert SCORE_BATCH <= 50
    assert score_max_tokens(SCORE_BATCH, cfg.max_tokens) <= 131072
    assert cfg.top_n == 10
