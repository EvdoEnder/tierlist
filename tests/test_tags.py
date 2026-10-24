"""The controlled tag vocabulary.

These run against the REAL intel/rubrics/tags.yaml, not a fixture. The file
is the contract — a slug renamed in it silently changes every tag URL on the
site and orphans the pages already published under the old name — so the
tests have to break when it does.
"""
from __future__ import annotations

import pytest

from intel.tags import (
    MAX_ENTITIES,
    MAX_TOPICS,
    label,
    normalize,
    slugify_entity,
    vocabulary,
)

# ------------------------------------------------------------ the file itself


def test_vocabulary_is_internally_consistent():
    v = vocabulary()
    assert len(v.topics) == len(set(v.topics)), "duplicate slug"
    for slug in v.topics:
        assert slug.isascii() and slug == slug.lower(), slug
        assert v.labels[slug]["zh"] and v.labels[slug]["en"], slug


def test_every_topic_has_a_group():
    """The tag index lists topics only under their group headings, so a
    topic above the first group row would vanish from it."""
    v = vocabulary()
    assert set(v.topic_group) == set(v.topics)
    for group, labels in v.groups.items():
        assert labels["zh"] and labels["en"], group


def test_every_alias_lands_somewhere_real():
    """An alias pointing at a slug that does not exist silently deletes the
    tag: _resolve returns it, is_topic says no, and slugify_entity then
    rejects it for being non-ASCII. A target that is not a topic must
    therefore be a valid entity slug already (gta5 -> gta-v)."""
    v = vocabulary()
    for raw, target in v.aliases.items():
        assert (target is None or target in v.labels
                or slugify_entity(target) == target), f"{raw} -> {target}"


def test_no_alias_shadows_a_slug():
    """Aliases are consulted BEFORE the topic list, so an alias keyed on a
    live slug would redirect that slug away from itself."""
    v = vocabulary()
    assert [k for k in v.aliases if k in v.labels] == []


def test_programming_languages_are_not_topics():
    """Every entry already prints the repo's language beside its stars, so a
    language tag is a second copy of a field the reader is looking at."""
    v = vocabulary()
    for lang in ("rust", "python", "java", "ruby", "swift", "typescript",
                 "nodejs", "c++"):
        assert lang not in v.labels, lang


# ------------------------------------------------------------------ normalize


def test_a_bilingual_pair_collapses_to_one_slug():
    """All 17 `crack` items also carried `破解`, so one concept owned two tag
    pages and burned two of the six slots."""
    assert normalize(["crack", "破解"]) == ["crack"]


def test_synonyms_across_both_languages_collapse():
    assert normalize(["游戏外挂", "cheat", "hack", "作弊器", "trainer"]) \
        == ["game-cheat"]


def test_spacing_and_punctuation_variants_collapse():
    assert normalize(["agent skill", "agent-skill", "agent技能"]) \
        == ["agent-skill"]


def test_signal_free_tags_are_dropped():
    """A null alias target means "this says nothing a reader could search
    for". Dropping must not be confused with failing to resolve."""
    assert normalize(["开源", "源码", "meta", "top100"]) == []


def test_a_leading_hash_is_stripped():
    assert normalize(["#mcp", "# crack"]) == ["mcp", "crack"]


def test_an_invented_concept_does_not_become_a_topic():
    """The whole point of a closed list: 蜂窝网关 and 薪资透明 each named
    exactly one project. Chinese that is not in the alias table cannot
    become an entity either, because entities must be ASCII."""
    assert normalize(["蜂窝网关", "决策留痕"]) == ["iot", "collaboration"]
    assert normalize(["随便编的一个词"]) == []


def test_a_proper_noun_survives_as_an_entity():
    """/tags/valorant.html is MEANT to hold one project — that singleton is
    the lookup readers actually perform, and closing this tier would break
    it."""
    assert normalize(["valorant"]) == ["valorant"]
    assert normalize(["game", "Valorant"]) == ["game", "valorant"]


def test_topics_sort_ahead_of_entities():
    assert normalize(["ffmpeg", "video"]) == ["video", "ffmpeg"]


def test_topics_are_capped_and_keep_the_models_order():
    picked = normalize(["video", "audio", "image", "3d", "animation", "game"])
    assert picked == ["video", "audio", "image", "3d", "animation"]
    assert len(picked) == MAX_TOPICS


def test_entities_are_capped():
    picked = normalize(["ffmpeg", "x264", "x265", "yt-dlp"])
    assert picked == ["ffmpeg", "x264"]
    assert len(picked) == MAX_ENTITIES


def test_malformed_input_yields_no_tags_rather_than_a_guess():
    assert normalize("crack") == []
    assert normalize(None) == []
    assert normalize([None, 3, {}, ""]) == []


# ------------------------------------------------------------------- entities


@pytest.mark.parametrize("raw,expect", [
    ("FFmpeg", "ffmpeg"),
    ("agent skill", "agent-skill"),
    ("yt_dlp", "yt-dlp"),
    ("c++", "c++"),
    ("next.js", "next.js"),
    ("  #x64dbg  ", "x64dbg"),
])
def test_entity_slugs_normalize(raw, expect):
    assert slugify_entity(raw) == expect


@pytest.mark.parametrize("raw", ["破解", "游戏", "", "-", "#", "a" * 40])
def test_entity_slugs_reject_what_cannot_be_one(raw):
    """Non-ASCII is refused on purpose: a Chinese entity would reopen the
    bilingual split, with 破解 and `crack` each getting a page."""
    assert slugify_entity(raw) is None


# --------------------------------------------------------------------- labels


def test_a_topic_reads_in_the_pages_own_language():
    assert label("crack", "zh") == "破解"
    assert label("crack", "en") == "Crack"


def test_an_entity_is_shown_as_itself_in_both_languages():
    """Proper nouns do not translate; inventing a Chinese label for ffmpeg
    would be worse than leaving it alone."""
    assert label("ffmpeg", "zh") == "ffmpeg"
    assert label("ffmpeg", "en") == "ffmpeg"


def test_generic_entities_and_spellings_fold_together():
    # Seen in stored tags: a generic word in the entity slot, the same game
    # under two spellings, and a word that carries nothing.
    assert normalize(["audio", "music", "gta5", "open-source"]) == [
        "audio", "gta-v"]
    assert normalize(["game", "lol"]) == ["game", "league-of-legends"]
