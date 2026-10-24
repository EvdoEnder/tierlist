"""What may be committed. The operator's own files sit in the same folder
as the repository and are kept out by .gitignore alone, so a forced add
would publish them; these checks fail the suite when one is tracked.

Personal strings (names, addresses, hostnames) cannot be listed here, since
this file is public. They are read from private/denylist.txt, one per line,
when that file exists; the check is skipped elsewhere, such as in CI."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_PATHS = ("private/", "data/", ".env", "CLAUDE.local.md", "PRODUCT.md",
                 "DESIGN.md", ".impeccable/")


def _tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return out.splitlines()


def test_no_private_path_is_tracked():
    bad = [f for f in _tracked()
           if f != ".env.example"
           and any(f == p or f.startswith(p) for p in PRIVATE_PATHS)]
    assert bad == []


def test_no_tracked_file_carries_a_denylisted_string():
    denylist = ROOT / "private" / "denylist.txt"
    if not denylist.exists():
        pytest.skip("no private/denylist.txt on this machine")
    words = [w.strip().lower() for w in denylist.read_text(encoding="utf-8").splitlines()
             if w.strip() and not w.startswith("#")]
    hits = []
    for rel in _tracked():
        try:
            text = (ROOT / rel).read_text(encoding="utf-8").lower()
        except (UnicodeDecodeError, OSError):
            continue
        hits += [f"{rel}: {w}" for w in words if w in text]
    assert hits == []
