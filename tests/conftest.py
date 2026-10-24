"""Shared offline fixtures. No test in this suite touches the network or a
paid API — GitHub rows are recorded-shape dicts, DeepSeek is exercised only
through `parse_response` on literal strings."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from intel.rubrics import Rubric
from intel.sources.base import Item

NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _offline_model_version(monkeypatch, request):
    """Every run asks the endpoint which model version it serves and checks
    that version's tier lines. Both reach outside the suite (the network,
    the store), so by default a test sees one fixed version with lines in
    place and no drift. Tests of the calibration itself opt out with the
    `real_calibration` marker."""
    if request.node.get_closest_marker("real_calibration"):
        return
    from intel import calibrate
    from intel.score import llm

    async def model_info(client=None):
        return llm.ModelInfo("test-model", None)

    async def ensure_lines(*args, **kwargs):
        return "known"

    async def drift(*args, **kwargs):
        return None

    async def load_lines(conn):
        return None

    monkeypatch.setattr(llm, "model_info", model_info)
    monkeypatch.setattr(calibrate, "ensure_lines", ensure_lines)
    monkeypatch.setattr(calibrate, "drift", drift)
    monkeypatch.setattr(calibrate, "load_lines", load_lines)



@pytest.fixture(autouse=True)
def _offline_translation(monkeypatch, request):
    """A run fills in missing English reasons with one more model call. By
    default a test sees that call fail, which the run treats as "no English
    this time" and goes on; tests of the call itself opt out with the
    `real_translate` marker."""
    if request.node.get_closest_marker("real_translate"):
        return
    from intel.score import llm

    async def translate_to_english(texts, *, client=None):
        raise RuntimeError("offline: no translation in tests")

    monkeypatch.setattr(llm, "translate_to_english", translate_to_english)


def repo_row(full_name="owner/repo", stars=50, description="A real tool",
             created_at="2026-08-22T00:00:00Z", fork=False,
             language="Rust", archived=False):
    """One GitHub search row, field-for-field the shape the API returns."""
    return {
        "id": 1,
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
        "description": description,
        "language": language,
        "stargazers_count": stars,
        "created_at": created_at,
        "fork": fork,
        "archived": archived,
    }


def item(item_id="github:owner/repo", stars=50, description="A real tool",
         created=None, fork=False):
    return Item(
        id=item_id,
        source="github",
        url=f"https://github.com/{item_id.removeprefix('github:')}",
        name=item_id.removeprefix("github:"),
        description=description,
        lang="Rust",
        stars=stars,
        created_at=created if created is not None
        else datetime(2026, 8, 22, tzinfo=UTC),
        is_fork=fork,
    )


@pytest.fixture
def rubric() -> Rubric:
    return Rubric(
        vertical="dev_tools",
        dimensions={
            "novelty": "解决的问题有多新?",
            "utility": "对开发者的实用价值",
            "momentum": "早期动能",
            "commercial": "商业化潜力",
        },
        weights={"novelty": 0.8, "utility": 1.0,
                 "momentum": 1.2, "commercial": 1.5},
        prompt_head="你是资深开发者工具投资分析师。",
        watch_min=60,
    )
