"""github_trending — the parser over github.com/trending markup.

The fixture keeps the structure of the live page (2026-10-10) and drops the
octicon paths and tracking attributes, which the parser never reads.
"""
from __future__ import annotations

from intel.sources.github_trending import parse_trending

PAGE = """
<article class="Box-row">
  <div class="float-right d-flex"><a href="/login?return_to=%2Fmorluto%2Frea">Star</a></div>
  <h2 class="h3 lh-condensed">
    <a data-hydro-click="{}" href="/morluto/rea" class="Link"><svg></svg>
      <span class="text-normal">morluto /</span>
      rea</a>  </h2>
    <p class="col-9 color-fg-muted my-1 tmp-pr-4">
      Reverse engineer anything with agents &amp; tools.
    </p>
  <div class="f6 color-fg-muted mt-2">
    <span><span itemprop="programmingLanguage">TypeScript</span></span>
    <a href="/morluto/rea/stargazers" class="Link"><svg aria-label="star"></svg>
        62,573</a>
    <a href="/morluto/rea/forks" class="Link"><svg></svg>
        12,663</a>
    <span class="d-inline-block float-sm-right"><svg></svg>
        25,784 stars today
</span>  </div>
</article>
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/someone/no-desc" class="Link">no-desc</a></h2>
  <div class="f6 color-fg-muted mt-2">
    <a href="/someone/no-desc/stargazers" class="Link">900</a>
    <span>1 star this week</span>
  </div>
</article>
<article class="Box-row"><p>no repository link</p></article>
"""


def test_parses_name_description_language_and_both_star_counts():
    rea, other = parse_trending(PAGE, "daily")

    assert rea.id == "github:morluto/rea"
    assert rea.source == "github"
    assert rea.url == "https://github.com/morluto/rea"
    assert rea.description == "Reverse engineer anything with agents & tools."
    assert rea.lang == "TypeScript"
    assert rea.stars == 62573
    assert rea.created_at is None
    assert rea.raw == {"board_only": True,
                       "period_rank": {"daily": 1},
                       "period_stars": {"daily": 25784}}

    assert other.name == "someone/no-desc"
    assert other.description == ""
    assert other.lang is None
    assert other.stars == 900
    assert other.raw["period_stars"] == {"daily": 1}


def test_a_row_without_a_repository_link_is_skipped():
    assert len(parse_trending(PAGE, "weekly")) == 2


def test_every_row_is_board_only():
    assert all(i.raw["board_only"] for i in parse_trending(PAGE, "daily"))


def test_each_period_reconstructs_the_count_one_window_back():
    from intel.pipeline import trending_bases

    rea = parse_trending(PAGE, "daily")[0]
    rea.raw["period_stars"].update({"weekly": 40000, "monthly": 60000})
    assert trending_bases([rea]) == {
        1: {"github:morluto/rea": 62573 - 25784},
        7: {"github:morluto/rea": 62573 - 40000},
        30: {"github:morluto/rea": 62573 - 60000},
    }


def test_a_zero_or_unknown_period_seeds_nothing():
    from intel.pipeline import trending_bases

    item = parse_trending(PAGE, "daily")[0]
    item.raw["period_stars"] = {"daily": 0, "yearly": 5}
    assert trending_bases([item]) == {}


def test_a_repo_on_several_pages_collects_every_period(monkeypatch):
    import asyncio

    from intel.sources import github_trending

    class Resp:
        text = PAGE

        def raise_for_status(self):
            pass

    class Client:
        async def get(self, *a, **k):
            return Resp()

    rows = asyncio.run(github_trending.fetch_trending(
        Client(), ["daily", "weekly"]))
    assert len(rows) == 2
    assert rows[0].raw["period_stars"] == {"daily": 25784, "weekly": 25784}


def test_boards_follow_githubs_order_per_period_with_tags():
    from intel.editions import trending_rows

    rea, other = parse_trending(PAGE, "daily")
    other.raw["period_rank"]["weekly"] = 1
    other.raw["period_stars"]["weekly"] = 77
    boards = trending_rows([other, rea], {"github:morluto/rea": ["security"]})

    assert [r["name"] for r in boards["daily"]] == ["morluto/rea",
                                                     "someone/no-desc"]
    assert boards["daily"][0] == {
        "rank": "01", "name": "morluto/rea",
        "url": "https://github.com/morluto/rea", "stars": 62573,
        "gain": 25784, "tags": ["security"]}
    assert boards["weekly"] == [{
        "rank": "01", "name": "someone/no-desc",
        "url": "https://github.com/someone/no-desc", "stars": 900,
        "gain": 77, "tags": []}]
    # A period the fetch did not return is absent, not an empty board.
    assert "monthly" not in boards
