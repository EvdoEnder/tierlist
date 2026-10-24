"""The public JSON API: static files under /api/v1/, written with the site.

Every document is built from the same contexts and rows the HTML pages are
rendered from, so the API and the site cannot disagree about what an issue
held. Fields are whitelisted per document rather than copied from those
contexts: the 0-100 score and its per-dimension breakdown are never public,
and a context that grows a field does not grow the API with it.

The field reference is docs/api.md. `api_version` is 1 in every document;
a breaking change goes to /api/v2/ and v1 keeps being written.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

API_VERSION = 1
API_DIR = Path("api") / "v1"

# Item ids are `<source>:<name>`. Only the two shapes below map onto a safe
# file path; anything else (the retired `awesome:` rows carry a URL) gets no
# item document.
_ITEM_ID = re.compile(r"^(github):([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)$|^(hn):([0-9]+)$")


def item_path(item_id: str | None) -> str | None:
    """`github:owner/repo` → `items/github/owner/repo.json`; None when the id
    has no file of its own."""
    m = _ITEM_ID.match(item_id or "")
    if not m:
        return None
    source, name = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    if any(part in ("", ".", "..") for part in name.split("/")):
        return None
    return f"items/{source}/{name}.json"


def dumps(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=False,
                      default=str) + "\n"


def _item_ref(e: dict, site_url: str) -> dict:
    ref = item_path(e.get("id"))
    return {
        "id": e.get("id"),
        "name": e["name"],
        "url": e["url"],
        "source": e.get("source") or "github",
        "tier": e["tier"],
        "reason": e.get("reason") or "",
        "description": e.get("description") or "",
        "tags": list(e.get("tags") or []),
        "stars": e.get("stars"),
        "language": e.get("lang") if (e.get("source") or "github") == "github"
                    and e.get("lang") != "语言未知" else None,
        "api": f"{site_url}/{API_DIR.as_posix()}/{ref}" if ref else None,
    }


def _board_rows(rows: list[dict] | None, fields: tuple[str, ...]) -> list[dict]:
    return [{k: r.get(k) for k in fields} for r in rows or []]


def boards_doc(ctx: dict) -> dict:
    """The boards one issue rendered below its index, as public rows."""
    gain = ctx.get("gain_board") or {}
    out: dict = {}
    if ctx.get("hot_new"):
        out["hot_new"] = _board_rows(ctx["hot_new"],
                                     ("name", "url", "stars", "description"))
    if gain.get("ready") and gain.get("rows"):
        out[f"gain_{gain['window_days']}d"] = _board_rows(
            gain["rows"], ("name", "url", "gain", "stars_now"))
    for period, rows in (ctx.get("trending") or {}).items():
        out[f"trending_{period}"] = _board_rows(
            rows, ("name", "url", "gain", "stars", "tags"))
    return out


def issue_doc(ctx: dict, *, edition: str, slug: str, site_url: str) -> dict:
    """One issue (a daily, weekly or monthly page) as a document."""
    lede = ctx.get("lede") or {}
    return {
        "api_version": API_VERSION,
        "edition": edition,
        "period": slug,
        "window": {"from": lede.get("window_from"), "to": lede.get("window_to")},
        "page": f"{site_url}/{edition}/{slug}.html",
        "found": lede.get("new_count"),
        "fetched": lede.get("fetched"),
        "model": lede.get("model"),
        "items": [_item_ref(e, site_url) for e in ctx.get("entries") or []],
        "show_hn": [_item_ref(e, site_url) for e in ctx.get("hn_entries") or []],
        "boards": boards_doc(ctx),
    }


def issue_rel(edition: str, slug: str) -> str:
    return f"{edition}/{slug}.json"


def index_doc(chain: dict[str, list[str]], *, site_url: str) -> dict:
    """latest.json: the newest issue of each edition and every issue that
    exists, so a client can walk the archive without guessing dates."""
    base = f"{site_url}/{API_DIR.as_posix()}"
    return {
        "api_version": API_VERSION,
        "latest": {edition: (f"{base}/{issue_rel(edition, slugs[-1])}"
                             if slugs else None)
                   for edition, slugs in chain.items()},
        "issues": {edition: list(slugs) for edition, slugs in chain.items()},
        "tags": f"{base}/tags/index.json",
        "costs": f"{base}/costs.json",
    }


def tag_doc(tag: str, label: dict[str, str], items: list[dict], *,
            site_url: str) -> dict:
    return {
        "api_version": API_VERSION,
        "tag": tag,
        "label": label,
        "page": f"{site_url}/tags/{urllib.parse.quote(tag)}.html",
        "items": [{"name": r["name"], "url": r["url"], "tier": r["tier"],
                   "first_seen": r.get("date"),
                   "description": r.get("description") or ""}
                  for r in items],
    }


def tags_index_doc(cloud: list[dict], labels: dict[str, dict[str, str]], *,
                   site_url: str) -> dict:
    base = f"{site_url}/{API_DIR.as_posix()}/tags"
    return {
        "api_version": API_VERSION,
        "tags": [{"tag": c["tag"], "label": labels.get(c["tag"], {}),
                  "count": c["count"],
                  "api": f"{base}/{urllib.parse.quote(c['tag'])}.json"}
                 for c in cloud],
    }


def item_doc(item: dict, ratings: list[dict], *, tier_of) -> dict:
    """One item and every rating it has had, newest first. `tier_of` turns a
    stored (score, dims) pair into its tier, so the score stays here."""
    return {
        "api_version": API_VERSION,
        "id": item["id"],
        "name": item["name"],
        "url": item["url"],
        "source": item["source"],
        "description": item.get("description") or "",
        "language": item.get("lang"),
        "tags": list(item.get("tags") or []),
        "first_seen": item.get("first_seen"),
        "ratings": [{"date": r["date"], "tier": tier_of(r["score"], r["dims"],
                                                       r.get("model_version")),
                     "reason": r.get("reason") or "",
                     "reason_en": r.get("reason_en") or "",
                     "stars": r.get("stars")}
                    for r in ratings],
    }
