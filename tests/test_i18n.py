"""i18n contract: every UI string exists in both languages; zh is the
fallback; the t() function formats placeholders; month labels localize."""
from __future__ import annotations

from intel.i18n import LANGS, STRINGS, month_label, t


def test_both_languages_have_identical_keys():
    zh, en = STRINGS["zh"], STRINGS["en"]
    assert set(zh) == set(en), f"missing: {set(zh) ^ set(en)}"


def test_t_falls_back_to_zh_and_echoes_unknown():
    assert t("nav.daily", "en") == "Daily"
    assert t("nav.daily", "xx") == "每日"       # unknown lang → zh
    assert t("no.such.key", "zh") == "no.such.key"   # loud, not silent


def test_t_formats_placeholders():
    assert t("board.accumulating", "zh", need=8, have=3) == \
        "数据积累中 · 需 8 天快照，现有 3 天。快照每天自动累积，届时此榜自动点亮。"
    assert t("card.tier", "en", tier="S") == "S tier"


def test_month_labels_localize():
    assert month_label(2026, 8, "zh") == "2026年8月"
    assert month_label(2026, 8, "en") == "August 2026"


def test_langs_contains_both():
    assert LANGS == ("zh", "en")
