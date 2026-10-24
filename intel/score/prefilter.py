"""The free candidate reduction, before any LLM call.

Hundreds of daily signals must reach the LLM as 30–50 rows — the paid
judgment is bought only for entries worth judging. Every rule here is
string/int math: no network, no model, deterministic and fully testable
offline.

Rules are per source, because a threshold is a statement about one
platform's units. "Five stars a day" is a sentence about GitHub; a Hacker
News post has no stars at all, and an entry added to an awesome-list has no
velocity of any kind — it arrived with a human's endorsement instead. One
rule set applied to all three would either wave everything through or filter
two sources out of existence.

The merge is round-robin rather than a concatenation for the same reason.
Sources are ranked internally by their own measure, and those measures do
not compare: putting them in one list and cutting at `max_candidates` would
hand every slot to whichever source happened to be first.

One rule here is not about a single item at all. `bursts` reads the whole
fetch, because "this repository bought its stars" is only visible in the
company it keeps — see its docstring.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from intel.sources.base import Item

# A repo younger than half a day gets its age floored at 0.5 d, so a 6-hour-old
# repo needs ~half the stars of a day-old one to show the same velocity — not a
# free pass, but no longer an unbounded multiplier.
_MIN_AGE_DAYS = 0.5

# The same guard on the HN clock: a post minutes old would otherwise divide
# its points by a fraction of an hour and outrank everything on the board.
#
# The floor is also why a rate alone cannot admit an HN post: with a
# one-hour denominator, a post with ONE point scores exactly 1.0/h, so a
# threshold of 1 admits every new submission on the site (12 admitted on
# 2026-08-30, 9 of them one-point posts minutes old). Rate says "climbing";
# only an absolute floor says "climbing to somewhere", which is why the HN
# rules carry both.
_MIN_AGE_HOURS = 1.0


def star_velocity(stars: int, created_at: datetime | None,
                  now: datetime) -> float:
    """Stars per day since creation; 0.0 when the age cannot be established."""
    if created_at is None:
        return 0.0
    age_days = max((now - created_at).total_seconds() / 86400.0, _MIN_AGE_DAYS)
    return stars / age_days


def points_per_hour(points: int, created_at: datetime | None,
                    now: datetime) -> float:
    """HN's equivalent of star velocity: points per hour since posting.

    Points alone would rank a day-old post above an hour-old one that is
    climbing twice as fast, and "early" is the entire reason to read Show HN.
    0.0 when the post carries no timestamp — such a row cannot be judged for
    momentum and is dropped rather than guessed at.
    """
    if created_at is None:
        return 0.0
    age_hours = max((now - created_at).total_seconds() / 3600.0, _MIN_AGE_HOURS)
    return points / age_hours


@dataclass(frozen=True, slots=True)
class PrefilterRules:
    """One source's admission rules. A threshold left at zero is not applied,
    so each source states only the lines that mean something to it."""

    max_candidates: int
    min_description_chars: int = 0
    min_star_velocity_per_day: float = 0.0
    min_points_per_hour: float = 0.0
    min_points: int = 0
    drop_forks: bool = False
    # Star-farm guard, GitHub's in practice: how many repositories must share
    # one star count inside one window before the whole burst is dropped.
    # 0 is off. See `bursts` for what the two numbers mean.
    burst_min_cluster: int = 0
    burst_window_minutes: int = 60


def bursts(items: list[Item], *, min_cluster: int,
           window_minutes: int) -> set[str]:
    """Ids of the items that arrived as a bought batch rather than as news.

    A star farm buys one package for every repository it publishes, so its
    output shares two properties nothing organic does: an IDENTICAL star
    count, and creation timestamps minutes apart. On 2026-08-30 that was 40
    repositories at exactly 150 stars created inside 36 minutes, named
    `gta-6-download-cyberleek`, `wemod-free-alternative` and so on. At 125
    stars a day each they cleared a velocity line written for real momentum,
    and they took 37 of the 38 GitHub slots in that day's paid batch.

    Neither half of the rule works alone. A shared star count is ordinary —
    the store holds 97 repositories with 8 stars fetched on one day, spread
    over 35 hours, and they are genuine. A tight creation window is ordinary
    too: GitHub creates thousands of repositories an hour. Only the two
    together say "one actor, one purchase".

    Measured over every GitHub row the store holds (1981 rows, 2026-08-23 to
    08-30): five repositories sharing a star count inside 60 minutes drops
    753 rows and **not one** of the 163 repositories that ever scored 40 or
    more. The highest score any dropped row ever earned is 9.

    An item with no creation time is never dropped — the rule is a statement
    about when things were made, and it cannot be made about a row that does
    not say.
    """
    if min_cluster < 2:
        return set()
    window = timedelta(minutes=window_minutes)
    by_stars: dict[int, list[Item]] = {}
    for item in items:
        if item.created_at is None:
            continue
        by_stars.setdefault(item.stars, []).append(item)

    dropped: set[str] = set()
    for group in by_stars.values():
        if len(group) < min_cluster:
            continue
        group.sort(key=lambda i: i.created_at)
        # For each start, how far the window reaches. The run is taken whole,
        # so a burst straddling an hour boundary stays one burst instead of
        # two halves that each fall under the line.
        for start in range(len(group)):
            end = start
            while (end + 1 < len(group)
                   and group[end + 1].created_at - group[start].created_at
                   <= window):
                end += 1
            if end - start + 1 >= min_cluster:
                dropped.update(i.id for i in group[start:end + 1])
    return dropped


def drop_bursts(items: list[Item], rules_by_source: dict[str, PrefilterRules]
                ) -> tuple[list[Item], dict[str, int]]:
    """Every item except the bought batches → (survivors, dropped per source).

    Applied to the raw fetch, before anything reads it: the paid batch is not
    the only consumer a farm ruins. The 新仓库热榜 is a slice of the same
    fetch, and on 2026-08-30 five of its ten rows were farm repositories;
    `star_snapshots` takes that same slice, so the star-gain board would have
    carried them for a month.
    """
    rules = {name: r for name, r in rules_by_source.items()
             if r.burst_min_cluster >= 2}
    if not rules:
        return items, {}
    by_source: dict[str, list[Item]] = {}
    for item in items:
        by_source.setdefault(item.source, []).append(item)
    dropped: set[str] = set()
    counts: dict[str, int] = {}
    for source, rule in rules.items():
        ids = bursts(by_source.get(source, []),
                     min_cluster=rule.burst_min_cluster,
                     window_minutes=rule.burst_window_minutes)
        if ids:
            counts[source] = len(ids)
            dropped |= ids
    return [i for i in items if i.id not in dropped], counts


def keeps(item: Item, rules: PrefilterRules, now: datetime) -> bool:
    """Does one item clear its source's lines?"""
    if rules.drop_forks and item.is_fork:
        return False
    if len(item.description or "") < rules.min_description_chars:
        return False
    if rules.min_star_velocity_per_day and \
            star_velocity(item.stars, item.created_at,
                          now) < rules.min_star_velocity_per_day:
        return False
    if rules.min_points and item.stars < rules.min_points:
        return False
    if rules.min_points_per_hour and \
            points_per_hour(item.stars, item.created_at,
                            now) < rules.min_points_per_hour:
        return False
    return True


def apply(items: list[Item], rules: PrefilterRules,
          now: datetime) -> list[Item]:
    """Keep the items that clear `rules`, best-first, capped.

    Input order is preserved — an adapter hands over rows in its own quality
    order (GitHub star-descending, HN newest-first), so truncation keeps the
    strongest of the survivors rather than an arbitrary subset.
    """
    survivors: list[Item] = []
    for item in items:
        if not keeps(item, rules, now):
            continue
        survivors.append(item)
        if len(survivors) >= rules.max_candidates:
            break
    return survivors


def interleave(per_source: list[list[Item]], cap: int) -> list[Item]:
    """Round-robin merge of each source's survivors, capped at `cap`.

    Every source contributes its best item, then its second, and so on, so a
    source that returns 300 rows cannot crowd out one that returns 8. A
    source that runs out simply stops contributing and the rest keep going —
    the cap is always filled if the material exists.
    """
    merged: list[Item] = []
    seen: set[str] = set()
    for rank in range(max((len(rows) for rows in per_source), default=0)):
        for rows in per_source:
            if rank >= len(rows):
                continue
            item = rows[rank]
            if item.id in seen:      # the same repo can arrive from two sources
                continue
            seen.add(item.id)
            merged.append(item)
            if len(merged) >= cap:
                return merged
    return merged


def apply_by_source(items: list[Item], rules_by_source: dict[str,
                    PrefilterRules], cap: int, now: datetime) -> list[Item]:
    """Filter each source's items by its own rules, then merge round-robin.

    Items from a source with no rules are dropped: a source reaches the paid
    stage only once someone has written down what makes one of its entries
    worth paying for.
    """
    by_source: dict[str, list[Item]] = {}
    for item in items:
        by_source.setdefault(item.source, []).append(item)
    survivors = [
        apply(rows, rules_by_source[source], now)
        for source, rows in by_source.items() if source in rules_by_source
    ]
    return interleave(survivors, cap)
