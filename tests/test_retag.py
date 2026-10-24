"""Re-normalizing stored tags after the vocabulary changes."""
from __future__ import annotations

import asyncio

from intel import retag
from intel import tags as T


def test_only_items_whose_tags_change_are_planned():
    rows = [("a", ["ai", "threejs"]),          # alias → entity spelling
            ("b", ["audio", "three.js"]),      # already clean
            ("c", ["developer-tools"]),        # every tag dropped
            ("d", ["homeassistant", "iot"])]   # entity before topic → reorder
    changes = {i: (old, new) for i, old, new in retag.plan(rows)}
    assert set(changes) == {"a", "c", "d"}
    assert changes["a"][1] == ["ai", "three.js"]
    assert changes["c"][1] == []
    assert changes["d"][1] == ["iot", "home-assistant"]


def test_a_second_pass_finds_nothing():
    rows = [("a", ["llamacpp", "llm", "switch", "office"])]
    once = [(i, new) for i, _old, new in retag.plan(rows)]
    assert retag.plan(once) == []


def test_the_report_counts_moves_and_emptied_items():
    text = retag.report([("a", ["threejs"], ["three.js"]),
                         ("c", ["cloud"], [])], total=5)
    assert "2 of 5 tagged items would change, 1 left with no tag" in text
    assert "threejs -> three.js" in text
    assert "cloud -> -" in text


class _Conn:
    def __init__(self, rows):
        self.rows, self.written = rows, None


def _patch(monkeypatch, conn):
    async def tagged_items(c):
        return c.rows

    async def save_tags_many(c, rows):
        c.written = rows

    monkeypatch.setattr(retag.db, "tagged_items", tagged_items)
    monkeypatch.setattr(retag.db, "save_tags_many", save_tags_many)


def test_a_dry_run_writes_nothing(monkeypatch, capsys):
    conn = _Conn([("a", ["threejs"])])
    _patch(monkeypatch, conn)
    assert asyncio.run(retag.run(conn)) == 1
    assert conn.written is None
    assert "dry run" in capsys.readouterr().out


def test_apply_writes_the_new_arrays(monkeypatch):
    conn = _Conn([("a", ["threejs"]), ("b", ["audio"])])
    _patch(monkeypatch, conn)
    asyncio.run(retag.run(conn, apply=True))
    assert conn.written == [("a", ["three.js"])]


def test_no_two_entity_spellings_differ_only_in_punctuation():
    """Every alias that folds a spelling lands on a slug that no other alias
    target collides with once separators are ignored."""
    vocab = T.vocabulary()
    targets = {t for t in vocab.aliases.values() if t}
    seen: dict[str, str] = {}
    for t in sorted(targets | set(vocab.topics)):
        k = "".join(ch for ch in t.lower() if ch.isalnum())
        assert k not in seen or seen[k] == t, (seen.get(k), t)
        seen[k] = t


def test_spellings_that_differ_in_punctuation_fold_onto_the_common_one():
    rows = [("a", ["game", "marvel-rivals"]), ("b", ["game", "marvel-rivals"]),
            ("c", ["game", "marvelrivals"]), ("d", ["game", "MarvelRivals"])]
    changes = {i: new for i, _old, new in retag.plan(rows)}
    assert changes == {"c": ["game", "marvel-rivals"],
                       "d": ["game", "marvel-rivals"]}


def test_a_tie_keeps_the_spelling_with_a_separator():
    fold = retag.spellings([["star-fox"], ["starfox"]], T.vocabulary())
    assert fold == {"starfox": "star-fox"}


def test_an_entity_spelled_like_a_topic_becomes_the_topic():
    fold = retag.spellings([["game-dev"], ["gamedev"]], T.vocabulary())
    assert fold == {"gamedev": "game-dev"}
