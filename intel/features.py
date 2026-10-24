"""Parts of the site that are built but not yet published.

`features:` in config.yaml switches each one; a switched-off feature writes
no files and no page links to it, so the public site shows nothing of it.
TIERLIST_FEATURES (a comma-separated list of names) turns features on for one
process without editing the config, which is how a private preview builds
them locally.
"""
from __future__ import annotations

import os
from functools import cache
from pathlib import Path

import yaml

_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"

# Every switch, and what it publishes when on.
FEATURES = {
    "funding_page": "/funding/ and /api/v1/costs.json",
    "json_api": "the JSON documents under /api/v1/",
}


@cache
def _configured() -> dict[str, bool]:
    raw = yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}
    return {k: bool(v) for k, v in (raw.get("features") or {}).items()}


def enabled(name: str) -> bool:
    if name not in FEATURES:
        raise KeyError(f"unknown feature {name!r}")
    forced = {f.strip() for f in os.environ.get("TIERLIST_FEATURES", "").split(",")}
    return name in forced or _configured().get(name, False)


def all_enabled() -> dict[str, bool]:
    return {name: enabled(name) for name in FEATURES}
