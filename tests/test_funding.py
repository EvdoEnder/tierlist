"""The /funding/ page and costs.json: scoring spend from the runs' own
reports, and donations from the file alone."""
from __future__ import annotations

from datetime import date

import pytest

from intel.deliver import funding
from intel.render import site


def test_spend_comes_from_the_runs_scoring_reports():
    runs = [{"day": date(2026, 10, 22), "runs": 1, "cost_usd": 0.005,
             "models": "m"},
            {"day": date(2026, 11, 1), "runs": 2, "cost_usd": 0.015,
             "models": "m"}]
    facts = funding.costs(runs, [])
    assert facts["source"] == "runs"
    assert facts["average_daily_usd"] == pytest.approx(0.01)
    assert facts["total_usd"] == pytest.approx(0.02)
    assert facts["months"] == [{"month": "2026-11", "spent_usd": 0.015},
                               {"month": "2026-10", "spent_usd": 0.005}]


def test_with_no_runs_there_is_no_source():
    facts = funding.costs([], [])
    assert facts["source"] is None and facts["average_daily_usd"] is None


def test_an_incomplete_donation_row_is_an_error(tmp_path):
    path = tmp_path / "d.yaml"
    path.write_text("donations:\n  - date: 2026-11-01\n    amount_usd: 1\n",
                    encoding="utf-8")
    with pytest.raises(ValueError):
        funding.load_donations(path)


def test_the_shipped_ledger_is_empty():
    """Nobody has donated before launch; a name in this file before then
    would be an invented donor on a public page."""
    assert funding.load_donations() == []


def _page(lang="en", donations=()):
    facts = funding.costs([], list(donations))
    ctx = {"lang": lang, "p": "" if lang == "en" else "/zh",
           "nav": {k: v for k, v in {"daily": "/daily/", "weekly": "/weekly/",
                                     "monthly": "/monthly/", "news": "/news/",
                                     "archive": "/archive/", "tags": "/tags/",
                                     "search": "/search/", "docs": "/docs/"}.items()},
           "edition_label": "Funding", "period_label": "funding",
           "page_path": "/funding/",
           "funding": {"costs": facts, "average_days": 7,
                       "github_sponsors": "someone"}}
    return site.render_funding_page(ctx)


def test_page_with_no_donations_says_so():
    html = _page()
    assert "No donations yet." in html
    assert "https://github.com/sponsors/someone" in html


def test_page_lists_exactly_the_donors_in_the_ledger():
    html = _page(donations=[{"date": "2026-11-02", "name": "octocat",
                             "amount_usd": 1.0, "via": "GitHub Sponsors"}])
    assert "octocat" in html and "$1.00" in html
    assert "No donations yet." not in html


def test_zh_page_states_the_exclusion():
    html = _page("zh")
    assert "电费和服务器不算在内" in html


def test_hidden_features_leave_no_link_on_the_page(monkeypatch):
    """With funding_page and json_api off, no page links to them."""
    monkeypatch.delenv("TIERLIST_FEATURES", raising=False)
    from intel import features
    assert features.enabled("funding_page") is False
    assert features.enabled("json_api") is False
    page = _page()
    assert 'href="/funding/"' not in page.split("<footer")[1]
    assert "/api/v1/latest.json" not in page.split("<footer")[1]
    assert "github.com/EvdoEnder/tierlist" in page


def test_a_feature_can_be_switched_on_for_one_process(monkeypatch):
    from intel import features
    monkeypatch.setenv("TIERLIST_FEATURES", "funding_page")
    assert features.enabled("funding_page") is True
    assert 'href="/funding/"' in _page().split("<footer")[1]
