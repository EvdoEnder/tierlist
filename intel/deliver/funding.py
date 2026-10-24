"""What the site costs to run and who has paid for it: the facts behind the
/funding/ page and /api/v1/costs.json.

Spend comes from the runs' own usage reports (intel.runs.usage), which
cover the scoring call only; the page says so.

Donations are read from intel/funding/donations.yaml, entered by hand. The
page never shows a donor the file does not name.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

_DONATIONS = Path(__file__).resolve().parent.parent / "funding" / "donations.yaml"
# The average that "days left" divides by: long enough to smooth one heavy
# day, short enough to follow a change of model.
AVERAGE_DAYS = 7


def load_donations(path: Path = _DONATIONS) -> list[dict]:
    """Donations newest first. A row needs a date, a name and an amount; a
    row missing any of them is an error in the file, not a donation."""
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    rows = []
    for d in raw.get("donations") or []:
        if not (d.get("date") and d.get("name") and d.get("amount_usd") is not None):
            raise ValueError(f"donations.yaml: incomplete row {d!r}")
        rows.append({"date": str(d["date"]), "name": str(d["name"]),
                     "amount_usd": round(float(d["amount_usd"]), 2),
                     "via": str(d.get("via") or "")})
    return sorted(rows, key=lambda r: r["date"], reverse=True)


def _days(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(str(d))


def costs(run_rows: list[dict], donations: list[dict]) -> dict:
    """Everything the page and costs.json show, as plain numbers."""
    scoring = [{"date": _days(r["day"]).isoformat(), "runs": int(r["runs"]),
                "cost_usd": float(r["cost_usd"] or 0.0),
                "models": r.get("models") or ""} for r in run_rows]
    recent = scoring[-AVERAGE_DAYS:]
    average = (sum(s["cost_usd"] for s in recent) / len(recent)
               if recent else None)
    months: dict[str, float] = {}
    for s in scoring:
        months[s["date"][:7]] = months.get(s["date"][:7], 0.0) + s["cost_usd"]
    return {
        "source": "runs" if scoring else None,
        "average_daily_usd": average,
        "total_usd": sum(s["cost_usd"] for s in scoring),
        "months": [{"month": m, "spent_usd": v} for m, v in sorted(months.items(),
                                                                  reverse=True)],
        "scoring_runs": scoring[::-1],
        "donations": {"count": len(donations),
                      "total_usd": round(sum(d["amount_usd"] for d in donations), 2),
                      "entries": donations},
    }


def costs_doc(facts: dict, *, api_version: int) -> dict:
    """costs.json: the same facts, with the one exclusion stated."""
    return {"api_version": api_version, **facts,
            "excludes": "electricity and the server; only LLM spend is counted"}
