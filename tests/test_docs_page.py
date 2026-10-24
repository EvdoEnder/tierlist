"""/docs/ states the pipeline's real numbers, in both languages."""
from __future__ import annotations

import re

import pytest

from intel.deliver.site import _docs_ctx, docs_facts
from intel.render import site
from intel.rubrics import load_rubric
from intel.tiers import lines


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_docs_prints_the_rubric_weights_and_tier_definitions(lang):
    html = site.render_docs_page(_docs_ctx(lang))
    for weight in load_rubric("dev_tools").weights.values():
        assert f"×{weight}" in html
    for definition in docs_facts(lang)["definitions"].values():
        assert definition.replace("'", "&#39;") in html
    assert 'class="tm tm-s' in html


def test_docs_facts_follow_the_rubric_and_config():
    facts = docs_facts("zh")
    assert [d["key"] for d in facts["dimensions"]] == \
        list(load_rubric("dev_tools").dimensions)
    assert [(x["min"], x["tier"]) for x in facts["lines"]] == list(lines())
    assert facts["max_candidates"] > 0 and facts["lookback_hours"] > 0


def test_docs_never_prints_a_raw_score_line():
    """The page explains the tiers by percentile, not by cut score."""
    html = site.render_docs_page(_docs_ctx("en"))
    for n, _ in lines():
        assert not re.search(rf"[≥>]=?\s*{n}\b", html)
