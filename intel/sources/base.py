"""The signal-source contract shared by every adapter.

A Source turns one platform's raw feed into `Item`s — the single shape the
rest of the pipeline knows. Adapters own all platform quirks; nothing
downstream speaks to a platform API or imports a platform SDK.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Item:
    """One discovered signal, normalized.

    `id` is globally unique across sources (`github:owner/repo`); it is the
    join key the store will dedupe on from M2. `raw` keeps the platform
    payload so a later milestone can re-enrich without refetching.
    """

    id: str
    source: str
    url: str
    name: str
    description: str
    lang: str | None
    stars: int
    created_at: datetime | None
    is_fork: bool = False
    feed: str | None = None          # rss adapter: which feed key this came from
    raw: dict = field(default_factory=dict)


class Source(Protocol):
    """What every adapter promises: `fetch(since)` returns Items created at
    or after `since`, best-first by the platform's own relevance order."""

    name: str

    async def fetch(self, since: datetime) -> list[Item]: ...
