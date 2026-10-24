"""The awesome-list adapter: the parser, and the rule that keeps a first
sighting from flooding the batch.

The parser runs against frozen README fragments — the real shapes these
lists use, including the ones that must NOT be read as entries (badges, a
table of contents, prose). The diff is exercised through `new_entries` with
a `seen_ids` set standing in for the store.
"""
from __future__ import annotations

import pytest

from intel.sources import awesome

pytestmark = pytest.mark.anyio

LIST = "agarrharr/awesome-cli-apps"

README = """
# Awesome CLI Apps [![Awesome](https://awesome.re/badge.svg)](https://awesome.re)

> A curated list.

## Contents

- [Productivity](#productivity)
- [Games](#games)

## Productivity

- [ripgrep](https://github.com/BurntSushi/ripgrep) - Recursively search
- [fd](https://github.com/sharkdp/fd) — A simple, fast alternative to `find`
* [bat](https://github.com/sharkdp/bat): A cat clone with wings
  - [nested](https://github.com/me/nested) - Indented entries count too
- [no description](https://example.com/thing)

Prose mentioning [a link](https://example.com/prose) does not list it.
"""


def _urls(items):
    return [i.url for i in items]


# -------------------------------------------------------------------- parser

def test_every_bullet_entry_is_parsed_with_its_description():
    items = awesome.parse_entries(README, LIST)
    assert "https://github.com/BurntSushi/ripgrep" in _urls(items)
    first = next(i for i in items if i.name == "ripgrep")
    assert first.description == "Recursively search"
    assert first.id == (f"awesome:{LIST}:https://github.com/BurntSushi/ripgrep")
    assert first.source == "awesome" and first.feed == LIST


@pytest.mark.parametrize("name", ["fd", "bat", "nested"])
def test_the_separators_and_bullets_lists_actually_use(name):
    items = awesome.parse_entries(README, LIST)
    entry = next(i for i in items if i.name == name)
    assert entry.description


def test_a_badge_is_not_an_entry():
    # `[![Awesome](badge.svg)](https://awesome.re)` is decoration; listing
    # it would put awesome.re itself in the brief.
    assert "https://awesome.re" not in _urls(awesome.parse_entries(README,
                                                                   LIST))


def test_the_table_of_contents_is_not_a_set_of_entries():
    # Anchor links carry no scheme and so cannot match at all.
    assert not [u for u in _urls(awesome.parse_entries(README, LIST))
                if u.startswith("#")]


def test_prose_containing_a_link_is_not_an_entry():
    assert "https://example.com/prose" not in _urls(
        awesome.parse_entries(README, LIST))


def test_an_entry_without_a_description_still_counts():
    entry = next(i for i in awesome.parse_entries(README, LIST)
                 if i.url == "https://example.com/thing")
    assert entry.description == ""


def test_the_same_url_twice_is_one_entry():
    doubled = README + "\n- [ripgrep again](https://github.com/BurntSushi/ripgrep) - dup\n"
    urls = _urls(awesome.parse_entries(doubled, LIST))
    assert urls.count("https://github.com/BurntSushi/ripgrep") == 1


def test_an_empty_readme_parses_to_nothing():
    assert awesome.parse_entries("", LIST) == []


# ---------------------------------------------------------------- the diff

class _Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


class _Client:
    def __init__(self, text=README, status=200):
        self.text = text
        self.status = status
        self.calls = 0

    async def get(self, url, **kw):
        self.calls += 1
        return _Resp(self.text, self.status)


async def test_a_first_sighting_returns_everything_for_the_caller_to_record():
    entries = await awesome.new_entries(_Client(), LIST, seen_ids=set(),
                                        baseline=True, max_new=25)
    assert len(entries) == 5      # the caller records these and emits none


async def test_after_the_baseline_only_additions_come_back():
    seen = {i.id for i in awesome.parse_entries(README, LIST)}
    added = README + "\n- [new-thing](https://github.com/me/new) - Just added\n"
    fresh = await awesome.new_entries(_Client(added), LIST, seen_ids=seen,
                                      baseline=False, max_new=25)
    assert [i.name for i in fresh] == ["new-thing"]


async def test_a_quiet_list_contributes_nothing():
    seen = {i.id for i in awesome.parse_entries(README, LIST)}
    assert await awesome.new_entries(_Client(), LIST, seen_ids=seen,
                                     baseline=False, max_new=25) == []


async def test_a_restructured_readme_is_capped_and_says_so(capsys):
    # Every URL rewritten reads as hundreds of discoveries; it is one
    # formatting event and must not be able to buy the whole day's batch.
    many = "\n".join(f"- [t{n}](https://example.com/{n}) - Thing {n}"
                     for n in range(200))
    fresh = await awesome.new_entries(_Client(many), LIST, seen_ids={"old"},
                                      baseline=False, max_new=25)
    assert len(fresh) == 25
    assert "capped at 25" in capsys.readouterr().err


async def test_an_unreadable_list_yields_nothing_instead_of_raising(capsys):
    # A renamed or deleted watchlist must not take the other lists with it,
    # but it must not be silent either: a 404 and a list that added nothing
    # both produce zero entries, and only one of them is a problem.
    assert await awesome.new_entries(_Client(status=404), LIST,
                                     seen_ids={"old"}, baseline=False,
                                     max_new=25) == []
    assert "no readable README" in capsys.readouterr().err


async def test_the_readme_filename_is_tried_in_several_spellings():
    # raw.githubusercontent is case-sensitive and agarrharr/awesome-cli-apps
    # ships `readme.md`; asking only for README.md returned a 404 that read
    # as an empty list (observed 2026-08-30).
    class _CaseSensitive(_Client):
        async def get(self, url, **kw):
            self.calls += 1
            if url.endswith("/readme.md"):
                return _Resp(README, 200)
            return _Resp("404: Not Found", 404)

    entries = await awesome.new_entries(_CaseSensitive(), LIST,
                                        seen_ids=set(), baseline=True,
                                        max_new=25)
    assert len(entries) == 5


# ------------------------------------------------------------- enrichment

async def test_enrichment_gives_the_entry_the_repositorys_own_numbers(
        monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")

    class _Repo(_Client):
        async def get(self, url, **kw):
            if url.startswith("https://api.github.com"):
                return _Json({"full_name": "BurntSushi/ripgrep",
                              "html_url": "https://github.com/BurntSushi/ripgrep",
                              "description": "recursively search",
                              "language": "Rust", "stargazers_count": 48000,
                              "created_at": "2016-01-01T00:00:00Z",
                              "fork": False})
            return await super().get(url, **kw)

    class _Json(_Resp):
        def __init__(self, payload):
            super().__init__("")
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    entries = awesome.parse_entries(README, LIST)[:1]
    enriched = await awesome.enrich_from_github(_Repo(), entries)
    # An awesome entry has no number of its own, so the repository's star
    # count IS its metric — unlike an HN post, whose points must survive.
    assert enriched[0].stars == 48000 and enriched[0].lang == "Rust"


async def test_enrichment_without_a_token_leaves_entries_alone(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    entries = awesome.parse_entries(README, LIST)
    assert await awesome.enrich_from_github(_Client(), entries) == entries
