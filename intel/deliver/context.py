"""RunResult → plain template context. Pure: no I/O, no rendering.

Both renderers (HTML brief, markdown push) consume the same dict, so the
copy decisions — truncation lengths, the 语言未知 fallback, the cost formula —
live exactly here and nowhere else.

The picks arrive as TWO boards: `entries` (repositories) and `hn_entries`
(Show HN, above its own floor). One list would let its order decide how much
of each the reader gets — see intel.sections.
"""
from __future__ import annotations

import html
from datetime import datetime

from intel import sections
from intel.score.llm import llm_model
from intel.tiers import tier

_DESC_MAX_CHARS = 200
_HOT_DESC_MAX_CHARS = 70
_HIGH_SCORE_MIN = 70

# Which platform a signal came from, in two characters. Every surface shows
# it because the reader's next question after "what is this" is "where did
# this come from": a repository with 40 stars and a Show HN post with 40
# points are not the same claim, and the row must not read as if they were.
SOURCE_BADGES = {"github": "GH", "hn": "HN", "awesome": "AW"}
SOURCE_METRIC_GLYPHS = {"github": "★", "hn": "▲", "awesome": "★"}

_VERTICAL_LABELS = {
    "dev_tools": "开发者工具晨报",
}
_VERTICAL_LABELS_EN = {
    "dev_tools": "Dev Tools Daily",
}

# $/token, per the M3 plan's cost line; prompt reads are cheaper than writes.
# Public: the store's edition_stats reuses them for the period cost sum.
PROMPT_USD_PER_TOKEN = 0.22e-6
COMPLETION_USD_PER_TOKEN = 0.66e-6


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _one_line(text: str) -> str:
    # Show HN text arrives entity-encoded (`y&#x27;all`); autoescape would
    # print the entities literally.
    return " ".join(html.unescape(text).split())


def cost_str(usage: dict) -> str:
    usage = usage or {}
    usd = (usage.get("prompt_tokens") or 0) * PROMPT_USD_PER_TOKEN \
        + (usage.get("completion_tokens") or 0) * COMPLETION_USD_PER_TOKEN
    return f"${usd:.4f}"


def llm_model_label() -> str:
    """The model named on the page — always the one the scorer actually
    calls, never a second reading of the same variable."""
    return llm_model()


def source_badge(source: str | None) -> str:
    return SOURCE_BADGES.get(source or "", "—")


def source_metric(source: str | None, count: int | None) -> str:
    """The headline number carrying the glyph its own platform uses: stars
    for a repository, upvotes for an HN post. A star in front of a point
    count is a small lie that makes two incomparable numbers look like one
    column."""
    glyph = SOURCE_METRIC_GLYPHS.get(source or "", "★")
    return f"{glyph}{count if count is not None else 0}"


def vertical_label(vertical: str, lang: str = "zh") -> str:
    table = _VERTICAL_LABELS_EN if lang == "en" else _VERTICAL_LABELS
    return table.get(vertical, f"{vertical} 晨报" if lang == "zh"
                     else vertical.replace("_", " ").title() + " Daily")


def rank_rows(rows: list[dict]) -> list[dict]:
    """Number one board's rows 01, 02, … — after the split, never before.

    Each board is a list in its own right, so its first entry is 01. Ranking
    the mixed list and splitting it afterwards would publish a 精选 board
    that starts at 02 and skips 04, which reads as entries withheld.
    """
    return [{"rank": f"{i:02d}", **row} for i, row in enumerate(rows, 1)]


def build_context(result) -> dict:
    ran_at = datetime.fromisoformat(result.ran_at)
    rows = []
    for scored in result.top:
        item = next(i for i in result.candidate_items
                    if i.id == scored.item_id)
        rows.append({
            "name": item.name,
            "url": item.url,
            "stars": item.stars,
            "source": item.source,
            "badge": source_badge(item.source),
            "metric": source_metric(item.source, item.stars),
            "lang": (item.lang or "语言未知")
                    if item.source == "github" else item.lang,
            "score": scored.score,
            "tier": tier(scored.score, dims=scored.dims,
                         version=scored.model_version),
            "dims": dict(scored.dims),
            "description": _truncate(_one_line(item.description),
                                     _DESC_MAX_CHARS),
            "reason": scored.reason,
            "is_watch": scored.verdict == "watch",
            "high_score": scored.score >= _HIGH_SCORE_MIN,
        })
    boards = sections.split(rows)
    hot_new = [{
        "rank": f"{rank:02d}",
        "name": item.name,
        "url": item.url,
        "stars": item.stars,
        "desc": _truncate(_one_line(item.description), _HOT_DESC_MAX_CHARS)
        if item.description else None,
    } for rank, item in enumerate(result.hot_new, 1)]
    return {
        "date": result.ran_at[:10],
        "vertical_label": vertical_label(result.vertical),
        "since_date": result.since_date,
        "lede": {
            "fetched": result.fetched,
            "candidates": result.candidates,
            "sent": result.stored_new,
            "scored": result.scored,
            "model": llm_model_label(),
            "cost": cost_str(result.usage),
        },
        "entries": rank_rows(boards[sections.PICKS]),
        "hn_entries": rank_rows(boards[sections.HN]),
        "hn_min_score": sections.min_score(sections.HN),
        "hot_new": hot_new,
        "generated_at": ran_at.strftime("%Y-%m-%d %H:%M UTC"),
    }
