"""The bulk tagger — specifically, what it does when the model says nothing.

A miss is written as an empty array, which is what takes an item out of the
`tags IS NULL` queue. That same write, applied on a re-tag, erases tags the
item already had. The two queues therefore cannot share one rule.
"""
from __future__ import annotations

import pytest

from intel import backfill_tags


class FakeConn:
    """Records set_tags_for_item calls; the queue functions are stubbed out,
    so no cursor is ever opened."""

    def __init__(self):
        self.writes: list[tuple[str, list[str]]] = []
        self.commits = 0

    async def commit(self):
        self.commits += 1


@pytest.fixture
def harness(monkeypatch):
    rows = [("github:a/b", "a/b", "desc", ["crack", "game"]),
            ("github:c/d", "c/d", "desc", ["mcp"])]
    state = {"served": False, "answer": {}, "writes": [], "readmes": {},
             "asked": []}

    async def queue(_conn, _batch, _cursor):
        if state["served"]:
            return [], None
        state["served"] = True
        return rows, ("2026-08-23", "github:c/d")

    async def tag_candidates(_candidates, _readmes=None):
        state["asked"].append([c.id for c in _candidates])
        return dict(state["answer"]), {"prompt_tokens": 1,
                                       "completion_tokens": 1}

    async def set_tags_for_item(_conn, item_id, tags):
        state["writes"].append((item_id, list(tags)))

    monkeypatch.setattr(backfill_tags, "_all_queue", queue)
    monkeypatch.setattr(backfill_tags, "_null_queue", queue)
    monkeypatch.setattr(backfill_tags, "_short_queue", queue)
    monkeypatch.setattr(backfill_tags.llm, "tag_candidates", tag_candidates)

    async def no_readmes(_items):
        return dict(state["readmes"])

    monkeypatch.setattr(backfill_tags.github_readme, "fetch_excerpts",
                        no_readmes)
    monkeypatch.setattr(backfill_tags.db, "set_tags_for_item",
                        set_tags_for_item)
    return state


@pytest.mark.anyio
async def test_retag_leaves_an_unanswered_item_alone(harness, anyio_backend):
    """The model answering for one item and not the other is not evidence
    that the other has no tags. Writing [] here erases real data — the exact
    outcome a --dry-run caught before it reached the corpus."""
    harness["answer"] = {"github:a/b": ["crack"]}

    await backfill_tags.backfill(FakeConn(), 50, retag_all=True)

    assert harness["writes"] == [("github:a/b", ["crack"])]


@pytest.mark.anyio
async def test_the_null_queue_still_empty_arrays_a_miss(harness,
                                                        anyio_backend):
    """An empty array is what takes an item OUT of the NULL queue. Skip the
    write here and the same batch repeats forever."""
    harness["answer"] = {"github:a/b": ["crack"]}

    await backfill_tags.backfill(FakeConn(), 50, retag_all=False)

    assert ("github:c/d", []) in harness["writes"]


@pytest.mark.anyio
async def test_dry_run_writes_nothing(harness, anyio_backend):
    harness["answer"] = {"github:a/b": ["mcp"], "github:c/d": ["cli"]}

    await backfill_tags.backfill(FakeConn(), 50, retag_all=True,
                                 dry_run=True)

    assert harness["writes"] == []


@pytest.mark.anyio
async def test_short_retag_leaves_rows_without_a_readme_alone(harness,
                                                              anyio_backend):
    """Most rows with no readable README are deleted repositories. Asked
    again on the same input, the model could only churn their tags."""
    harness["readmes"] = {"github:a/b": "An OCR tool"}
    harness["answer"] = {"github:a/b": ["ocr"]}

    await backfill_tags.backfill(FakeConn(), 50, short_only=True)

    assert harness["asked"] == [["github:a/b"]]
    assert harness["writes"] == [("github:a/b", ["ocr"])]


@pytest.mark.anyio
async def test_the_queue_read_is_committed_before_the_llm_call(
        harness, monkeypatch, anyio_backend):
    conn = FakeConn()
    seen: list[int] = []

    async def tag_candidates(_candidates, _readmes=None):
        seen.append(conn.commits)
        return {}, {}

    monkeypatch.setattr(backfill_tags.llm, "tag_candidates", tag_candidates)
    await backfill_tags.backfill(conn, 50, retag_all=True)

    assert seen == [1]
