"""The tag vocabulary: load it, normalize against it, label it per language.

The vocabulary itself is data (`intel/rubrics/tags.yaml`); this module is the
only code that knows its shape, the same split the rubric loader uses.

Two tiers, because two different failures had to be cured at once:

  * **topics are closed.** The model picks from a fixed list. Told to invent
    its own words it produced 266 tags over 114 items with 229 used exactly
    once — a tag naming one project gathers nothing, which is the entire job
    of a tag.
  * **entities are open, capped at 2.** Proper nouns (valorant, ffmpeg,
    x64dbg) are singletons *correctly*; /tags/valorant.html is meant to hold
    one project, and that is the lookup readers actually perform.

Slugs are ASCII, and a slug is the filename and the URL. Display labels are
per language, so /tags/crack.html reads 破解 on the Chinese site and Crack on
the English one — the site is bilingual but its tags were not, so Chinese
tags were being printed on English pages.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_VOCAB_PATH = Path(__file__).resolve().parent / "rubrics" / "tags.yaml"

MAX_TAGS = 7
MIN_TOPICS = 3
MAX_TOPICS = 5
MAX_ENTITIES = 2

# An entity slug is lowercase ASCII: letters, digits, dot, plus, dash. Dots
# and pluses survive because they are part of real names (`c++`, `next.js`);
# spaces and underscores collapse to dashes so `agent skill` and
# `agent-skill` cannot both exist.
_ENTITY_OK = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,29}$")
_ENTITY_SEP = re.compile(r"[\s_/]+")


@dataclass(frozen=True, slots=True)
class Vocabulary:
    topics: tuple[str, ...]                 # slugs, in file order
    labels: dict[str, dict[str, str]]       # slug → {"zh": …, "en": …}
    aliases: dict[str, str | None]          # raw tag → slug, or None to drop
    groups: dict[str, dict[str, str]]       # group → {"zh": …, "en": …}, in file order
    topic_group: dict[str, str]             # topic slug → its group

    def is_topic(self, slug: str) -> bool:
        return slug in self.labels

    def group_of(self, slug: str) -> str | None:
        """The tag index heading a topic sits under; None for an entity."""
        return self.topic_group.get(slug)

    def group_label(self, group: str, lang: str = "zh") -> str:
        return self.groups.get(group, {}).get(lang, group)

    def label(self, slug: str, lang: str = "zh") -> str:
        """Display text for a slug. An entity has no entry and is shown as
        itself — proper nouns do not translate, and inventing a Chinese
        label for `ffmpeg` would be worse than leaving it alone."""
        return self.labels.get(slug, {}).get(lang, slug)


@lru_cache(maxsize=1)
def vocabulary() -> Vocabulary:
    with open(_VOCAB_PATH, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    topics: list[str] = []
    labels: dict[str, dict[str, str]] = {}
    groups: dict[str, dict[str, str]] = {}
    topic_group: dict[str, str] = {}
    group = None
    for row in raw["topics"]:
        if "group" in row:
            group = str(row["group"])
            groups[group] = {"zh": str(row["zh"]), "en": str(row["en"])}
            continue
        slug = str(row["slug"])
        topics.append(slug)
        labels[slug] = {"zh": str(row["zh"]), "en": str(row["en"])}
        if group is not None:
            topic_group[slug] = group
    aliases = {str(k): (None if v is None else str(v))
               for k, v in (raw.get("aliases") or {}).items()}
    return Vocabulary(tuple(topics), labels, aliases, groups, topic_group)


def slugify_entity(raw: str) -> str | None:
    """A free-form proper noun → an entity slug, or None if it cannot be one.

    Rejecting is the point, and `_ENTITY_OK` is the single guard that does
    it — an ASCII-only character class, so non-ASCII is refused by the same
    rule that refuses a leading dash. That refusal is load-bearing: a
    Chinese entity would reopen exactly the bilingual split this vocabulary
    closed, with 破解 and `crack` each getting their own page.
    """
    t = _ENTITY_SEP.sub("-", raw.strip().lstrip("#").strip()).strip("-")
    return t.lower() if _ENTITY_OK.match(t.lower()) else None


def normalize(raw_tags, *, vocab: Vocabulary | None = None) -> list[str]:
    """Whatever the model returned → the tags that get stored.

    Order of resolution per tag: alias table first (it is the only thing
    that can map 破解 onto `crack`), then the topic list, then the entity
    rules. Aliases win over a literal match so a slug can be retired by
    pointing its own name somewhere else.

    Topics keep file order out of the model's hands only in the sense of
    deduping; the returned list preserves the order the model chose, since
    that is its own ranking of relevance. Caps: 5 topics, 2 entities, 7
    total. Anything malformed yields [] rather than a partial guess.
    """
    if not isinstance(raw_tags, list):
        return []
    vocab = vocab or vocabulary()
    topics: list[str] = []
    entities: list[str] = []
    seen: set[str] = set()

    for value in raw_tags:
        if not isinstance(value, str):
            continue
        cleaned = value.strip().lstrip("#").strip()
        if not cleaned:
            continue

        slug = _resolve(cleaned, vocab)
        if slug is None or slug in seen:
            continue

        if vocab.is_topic(slug):
            if len(topics) >= MAX_TOPICS:
                continue
            topics.append(slug)
        else:
            if len(entities) >= MAX_ENTITIES:
                continue
            entities.append(slug)
        seen.add(slug)

    return (topics + entities)[:MAX_TAGS]


def _resolve(cleaned: str, vocab: Vocabulary) -> str | None:
    for key in (cleaned, cleaned.lower()):
        if key in vocab.aliases:
            return vocab.aliases[key]          # may be None: drop on purpose
    lowered = cleaned.lower() if cleaned.isascii() else cleaned
    if vocab.is_topic(lowered):
        return lowered
    return slugify_entity(cleaned)


def prompt_block(lang_hint: str = "zh") -> str:
    """The topic list as the model sees it, one line per slug with its
    Chinese gloss. Handing over the gloss rather than the bare slug is what
    lets a Chinese-reasoning model pick `game-cheat` for 外挂 instead of
    inventing 游戏外挂 next to it."""
    vocab = vocabulary()
    return "\n".join(f"{slug} = {vocab.label(slug, lang_hint)}"
                     for slug in vocab.topics)


def label(slug: str, lang: str = "zh") -> str:
    return vocabulary().label(slug, lang)
