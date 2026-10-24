"""Render the template context to the markdown brief.

Two parts, one renderer each: `render_markdown` is the 精选 brief (the
repositories plus the hot board), `render_hn_brief` is the Show HN board that
follows it. They are rendered apart because one ranked list's order would
otherwise decide how much of each the reader gets — see intel.sections.

The renderer targets ~3800 chars and, when over, drops hot-board entries from
the bottom until it fits. The entries
section is never cut — it is the payload; the hot board is the garnish.
"""
from __future__ import annotations

from intel.tiers import tier

_BUDGET_CHARS = 3800


def _entry_block(entry: dict) -> list[str]:
    # `metric` carries the glyph the source uses (★ stars, ▲ HN points);
    # `badge` says which source that was. An older context without either
    # renders exactly as it did before.
    metric = entry.get("metric") or f"★{entry['stars']}"
    tr = entry.get("tier") or tier(entry.get("score"), dims=entry.get("dims"),
                                   version=entry.get("model_version"))
    badge = f"[{entry['badge']}] " if entry.get("badge") else ""
    lines = [f"{entry['rank']}. {badge}{entry['name']} "
             f"[{tr}] {metric}"]
    if entry["reason"]:
        lines.append(entry["reason"])
    lines.append(entry["url"])
    return lines


def _hot_line(row: dict) -> str:
    """One hot-board row. The name carries the link as an inline markdown
    link rather than a bare URL on its own line: ten bare URLs double the
    length of the section that the budget trims first, and the brief on disk
    is markdown anyway. An edition context has no `url` — those rows keep
    the plain name."""
    desc = row["desc"] or "(无描述)"
    name = f"[{row['name']}]({row['url']})" if row.get("url") else row["name"]
    return f"{row['rank']}. {name} ★{row['stars']} — {desc}"


def render_hn_brief(context: dict) -> str | None:
    """The Show HN board as its own message, or None when it is empty.

    A second message rather than a second section of the first: the two
    boards answer different questions, and a reader who came for
    repositories should be able to ignore this one without scrolling past
    it. None and not "" — the caller must be able to tell "nothing cleared
    the line" from "a message with no entries in it", and only the second
    would be worth suppressing after the fact.
    """
    entries = context.get("hn_entries") or []
    if not entries:
        return None
    parts = [
        f"**TIERLIST.TECH · Show HN** — {context['date']}",
        f"{len(entries)} 条 | {context['lede']['model']}",
        "",
    ]
    for entry in entries:
        if parts[-1]:
            parts.append("")
        parts.extend(_entry_block(entry))
    parts += ["", f"— {context['generated_at']} · tierlist.tech"]
    return "\n".join(parts)


def render_markdown(context: dict) -> str:
    lede = context["lede"]
    head = [
        f"**TIERLIST.TECH · {context['vertical_label']}** — {context['date']}",
        f"窗口 >{context['since_date']} | 抓取 {lede['fetched']} | "
        f"送审 {lede['sent']} | {lede['model']} | 成本 {lede['cost']}",
        "",
        "**今日精选**",
    ]
    entry_lines: list[str] = []
    for entry in context["entries"]:
        if entry_lines:
            entry_lines.append("")
        entry_lines.extend(_entry_block(entry))
    footer = ["", f"— {context['generated_at']} · tierlist.tech"]

    def assemble(hot_rows: list[dict]) -> str:
        parts = head + entry_lines
        if hot_rows:
            parts += ["", "**新仓库热榜**"]
            parts += [_hot_line(row) for row in hot_rows]
        parts += footer
        return "\n".join(parts)

    hot = list(context["hot_new"])
    text = assemble(hot)
    while len(text) > _BUDGET_CHARS and hot:
        hot.pop()
        text = assemble(hot)
    return text
