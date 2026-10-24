import httpx
import pytest

from intel.score.llm import build_tag_user_prompt
from intel.sources import github_readme
from intel.sources.base import Item


def item(item_id="github:a/b", description="", source="github"):
    return Item(id=item_id, source=source, url="", name=item_id,
                description=description, lang=None, stars=0,
                created_at=None)


def test_only_thin_github_descriptions_need_a_readme():
    assert github_readme.needs_readme(item(description=""))
    assert github_readme.needs_readme(item(description="jackie-fmri-sim"))
    assert not github_readme.needs_readme(
        item(description="Fast CPU OCR: line detection, CTC recognition"))
    assert not github_readme.needs_readme(
        item(item_id="hn:1", source="hn", description=""))


def test_excerpt_keeps_prose_and_drops_badges_code_and_markup():
    md = (
        "# Indra\n"
        "[![CI](https://x/badge.svg)](https://x/actions) "
        "![logo](logo.png)\n"
        "<p align=\"center\"><img src=\"a.png\"></p>\n"
        "<!-- hidden -->\n"
        "Fast **CPU** OCR for [documents](https://d).\n"
        "```bash\npip install indra\n```\n"
        "Run `indra scan` on a folder.\n"
        "[ref]: https://example.com\n"
    )
    text = github_readme.excerpt(md)
    assert text == "Indra Fast CPU OCR for documents. Run on a folder."


def test_excerpt_is_capped():
    assert len(github_readme.excerpt("word " * 2000, limit=100)) == 100


def test_the_tag_prompt_carries_an_excerpt_only_where_one_exists():
    prompt = build_tag_user_prompt(
        [item("github:a/b", "x"), item("github:c/d", "a real description")],
        {"github:a/b": "An OCR tool"})
    assert "- github:a/b\n  x\n  README 摘录: An OCR tool" in prompt
    assert prompt.count("README") == 1


@pytest.mark.anyio
async def test_fetch_asks_only_for_thin_rows_and_skips_failures(monkeypatch,
                                                                anyio_backend):
    asked = []

    def handler(request):
        asked.append(request.url.path)
        if request.url.path == "/repos/a/b/readme":
            return httpx.Response(200, text="An OCR tool ![b](b.svg)")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(github_readme.httpx, "AsyncClient",
                        lambda **kw: real(transport=transport, **kw))
    found = await github_readme.fetch_excerpts([
        item("github:a/b", ""), item("github:c/d", "short"),
        item("github:e/f", "a description that is long enough to tag from"),
    ])
    assert found == {"github:a/b": "An OCR tool"}
    assert sorted(asked) == ["/repos/a/b/readme", "/repos/c/d/readme"]
