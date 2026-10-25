"""UI strings for the two site languages.

Contract: every string the templates render comes from here, keyed as
"group.name". Content (item names, descriptions, LLM reasons, tags) is DATA
and stays as produced — i18n covers the chrome (nav, lede labels, board
titles, buttons), not the payload. Adding a language = one new dict + the
lang overrides in `intel.editions` labels; no template edits.

`lang` value: "zh" | "en". Anything unknown falls back to "zh" text.
"""

from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "zh": {
        # masthead + nav
        "main.title": "tierlist.tech 晨报",
        "main.subtitle": "开发者工具情报",
        "nav.daily": "每日",
        "nav.weekly": "每周",
        "nav.monthly": "每月",
        "nav.news": "新闻",
        "nav.archive": "归档",
        "nav.docs": "文档",
        "nav.funding": "资助",
        "nav.connect": "接入",
        "nav.api": "API",
        "nav.tags": "标签",
        "nav.tiers": "等级",
        "intro.once": "下次不再显示",
        "intro.skip": "跳过",
        "intro.replay": "重播开场动画",
        "nav.search": "搜索",
        "nav.rss": "RSS",
        # lede (issue + landing)
        "lede.created_range": "收录新建",
        "lede.window_range": "统计窗口",
        "lede.fetched": "抓取",
        "lede.filtered": "粗筛",
        "lede.sent": "送审",
        "lede.new_found": "新发现",
        # The one sentence of prose in the chrome: the period's result,
        # stated. Keyed off new_count, which every context carries —
        # `fetched` is hardcoded None by build_aggregate_context, so a
        # headline that needed it rendered on no page at all.
        "lede.headline_daily": "今天发现 {found} 个新项目，{n} 个评了级。",
        "lede.headline_period": "本期发现 {found} 个新项目，{n} 个进了榜。",
        # the funnel card under the page title
        "lede.funnel": "本期漏斗",
        "lede.scored": "已评级",
        "lede.ranked": "进榜",
        "lede.top_tier": "最高等级",
        # sections
        "sec.selected": "Selected",
        "sec.this_week": "This Week",
        "sec.this_month": "This Month",
        "sec.entries": "entries",
        "sec.daily_title": "今日精选",
        "sec.weekly_title": "本周精选",
        "sec.monthly_title": "本月精选",
        # The second board. 今日精选 ranks repositories; a Show HN post is a
        # different claim and says so in its own section. The floor it
        # applies rides in the kick line, not in a sentence.
        "sec.hn_title": "Show HN 精选",
        "sec.latest_daily": "Latest daily",
        "sec.sections": "三个专区",
        "sec.empty": "本期没有过线项目。",
        "sec.scorer_empty": "评级未能完成:{sent} 个项目送审,模型没有返回可用结果。"
                            "本期榜单缺失,与项目质量无关。",
        "sec.no_projects": "No projects cleared the threshold.",
        "sec.below_line": "本期最高只到 {top} 级，没有 A 级以上的项目，仅供参考。",
        # cards
        "card.repo_link": "仓库链接",
        "card.hn_link": "看讨论",
        "card.lang_unknown": "语言未知",
        "board.gain_h": "star 增速榜",
        "card.verdict": "判断",
        "card.tier": "{tier} 级",
        # tier names, as the bands and the footer key print them
        "tier.S": "今天就去看",
        "tier.A": "值得关注",
        "tier.B": "有亮点",
        "tier.C": "普通",
        "tier.D": "可以跳过",
        "end.today": "今天就到这里。下面是其余等级和榜单。",
        "legend.title": "等级符号",
        "since.last": "你上次看到的是 {d} 那一期。",
        "nav.skip": "跳到正文",
        # boards
        "board.star_gain": "Star Gain",
        "board.hot_new": "新仓库热榜",
        "trending.daily": "GitHub 今日热门",
        "trending.weekly": "GitHub 本周热门",
        "trending.monthly": "GitHub 本月热门",
        "trending.kick": "GitHub 原始排序 · 看相对平时的热度，不按涨星",
        "board.hot_new_en": "Hot New Repos",
        "board.by_stars": "by stars",
        "board.accumulating": ("数据积累中 · 需 {need} 天快照，现有 {have} 天。"
                               "快照每天自动累积，届时此榜自动点亮。"),
        "board.accumulating_en": ("Data accumulating · {need} days needed, "
                                  "{have} so far. Snapshots build every day "
                                  "and the board lights up on its own."),
        # landing cross cards
        "cross.daily": "每日 →",
        "cross.weekly": "每周 →",
        "cross.monthly": "每月 →",
        # archive
        "archive.title": "归档",
        "archive.prev": "← 上个月",
        "archive.next": "下个月 →",
        "archive.prev_en": "← Previous",
        "archive.next_en": "Next →",
        "cal.dow1": "一", "cal.dow2": "二", "cal.dow3": "三",
        "cal.dow4": "四", "cal.dow5": "五", "cal.dow6": "六",
        "cal.dow7": "日",
        # issue pager (bottom of every daily/weekly/monthly page). The
        # arrows are part of the label so the two links read as directions
        # even when the labels beside them are bare slugs.
        "pager.prev": "← 上一期",
        "pager.next": "下一期 →",
        "pager.oldest": "← 已是最早一期",
        "pager.newest": "已是最新一期 →",
        "pager.hint": "← → 键翻页",
        # tags
        "tags.title": "标签索引",
        "tags.cloud": "标签云",
        "tags.all": "全部标签",
        "tags.topics": "主题",
        "tags.names": "产品与名称",
        "tags.jump": "按首字母跳转",
        "tags.empty": "还没有标签 — 第一期评级完成后这里会亮起来。",
        "tags.empty_page": "这个标签下还没有项目。",
        "tags.projects": "projects",
        "tags.unrated": "未评级",
        # tier history
        "tiers.title": "历代等级",
        "tiers.lede": "每个评过级的仓库都在这里，按最近一次评级归入一个等级，"
                      "同一个仓库只出现一次。点一个等级看它最近一个月，"
                      "或在下表里选月份。",
        "tiers.by_month": "按月份",
        "tiers.month": "月份",
        "tiers.all": "全部等级",
        "tiers.switch": "切换等级",
        "tiers.months": "切换月份",
        "tiers.empty": "还没有评过级的仓库。",
        "tiers.rated_on": "{day} 评级",
        "tiers.page_desc": "{month} 评为 {tier} 级的 {n} 个仓库。",
        # footer
        "foot.brand": "tierlist.tech · 每日信号雷达",
        # feed + page metadata (Atom channel, <meta description>, OG cards)
        "feed.title": "tierlist.tech 晨报",
        "feed.subtitle": "每日信号雷达 · 开发者工具精选",
        "feed.entry_summary": "tierlist.tech 精选 {tier} 级 · {title}",
        "meta.description": "每日自动扫描 GitHub 新仓库，"
                            "在主流榜单收录之前挑出"
                            "值得关注的开发者工具，"
                            "附等级与理由。",
        "foot.generated": "生成于",
        "foot.updated": "更新于",
        # news
        "news.title": "实时新闻",
        "news.live": "LIVE",
        "news.updated_at": "更新于 {ts} UTC",
        "news.lede": "科技信号 · 来自 HN 与 RSS，15 分钟刷新一次",
        "news.ticker_prefix": "快讯",
        "news.empty": "新闻源还没抓到条目。",
        "news.points": "{n} 热度",
        # search
        "search.title": "搜索",
        "search.placeholder": "搜项目名 / 标签 / 作者…",
        "search.hint": "输入 1 个以上字符，回车或点结果",
        "search.empty_query": "输入关键词开始搜索。支持：项目名、标签、"
                              "仓库作者(owner/repo 前面那截)。"
                              "以 # 开头只匹配该标签本身，如 #mcp。",
        "search.no_results": "没有匹配的项目。换个词试试。",
        "search.results": "{n} 个结果",
        "search.index_count": "索引内 {n} 个项目",
        "search.submit": "搜索",
        "search.index_note": "索引每次日报刷新时更新",
        # language switch
        "lang.alt": "EN",
        "lang.zh": "中文",
        "lang.en": "English",
        "lang.zh_short": "中",
        "lang.en_short": "EN",
    },
    "en": {
        "main.title": "tierlist.tech brief",
        "main.subtitle": "DEV TOOL INTELLIGENCE",
        "nav.daily": "Daily",
        "nav.weekly": "Weekly",
        "nav.monthly": "Monthly",
        "nav.news": "News",
        "nav.archive": "Archive",
        "nav.docs": "Docs",
        "nav.funding": "Funding",
        "nav.connect": "Connect",
        "nav.api": "API",
        "nav.tags": "Tags",
        "nav.tiers": "Tiers",
        "intro.once": "Don't show this again",
        "intro.skip": "Skip",
        "intro.replay": "Replay intro",
        "nav.search": "Search",
        "nav.rss": "RSS",
        "lede.created_range": "Created",
        "lede.window_range": "Window",
        "lede.fetched": "Fetched",
        "lede.filtered": "Filtered",
        "lede.sent": "Sent",
        "lede.new_found": "New finds",
        "lede.headline_daily": ("{found} new projects found today, "
                               "{n} rated."),
        "lede.headline_period": ("{found} new projects found this period, "
                                "{n} made the list."),
        "lede.funnel": "This issue's funnel",
        "lede.scored": "Rated",
        "lede.ranked": "Ranked",
        "lede.top_tier": "Top tier",
        "sec.selected": "Selected",
        "sec.this_week": "This Week",
        "sec.this_month": "This Month",
        "sec.entries": "entries",
        "sec.daily_title": "Today's Picks",
        "sec.weekly_title": "This Week's Picks",
        "sec.monthly_title": "This Month's Picks",
        "sec.hn_title": "Show HN Picks",
        "sec.latest_daily": "Latest daily",
        "sec.sections": "Sections",
        "sec.empty": "Nothing cleared the threshold today.",
        "sec.scorer_empty": "Rating did not complete: {sent} projects were "
                            "sent, the model returned no usable result. This "
                            "issue has no board, which says nothing about the "
                            "projects.",
        "sec.no_projects": "No projects cleared the threshold.",
        "sec.below_line": "The best this period is {top} tier; nothing reached "
                          "A or above. Shown for reference.",
        "card.repo_link": "repository",
        "card.hn_link": "discussion",
        "card.lang_unknown": "Language unknown",
        "board.gain_h": "Star gain",
        "card.verdict": "Verdict",
        "card.tier": "{tier} tier",
        "tier.S": "Look today",
        "tier.A": "Worth watching",
        "tier.B": "Has a spark",
        "tier.C": "Ordinary",
        "tier.D": "Skip",
        "end.today": "That's today. The other tiers and the boards follow.",
        "legend.title": "Tier key",
        "since.last": "You last read the {d} issue.",
        "nav.skip": "Skip to content",
        "board.star_gain": "Star Gain",
        "board.hot_new": "Hot New Repos",
        "board.hot_new_en": "Hot New Repos",
        "trending.daily": "GitHub Trending · Today",
        "trending.weekly": "GitHub Trending · This Week",
        "trending.monthly": "GitHub Trending · This Month",
        "trending.kick": "GitHub's own order · heat relative to usual, not stars gained",
        "board.by_stars": "by stars",
        "board.accumulating": ("Data accumulating · {need} days needed, "
                               "{have} so far. Snapshots build every day "
                               "and the board lights up on its own."),
        "board.accumulating_en": ("Data accumulating · {need} days needed, "
                                  "{have} so far. Snapshots build every day "
                                  "and the board lights up on its own."),
        "cross.daily": "Daily →",
        "cross.weekly": "Weekly →",
        "cross.monthly": "Monthly →",
        "archive.title": "Archive",
        "archive.prev": "← Previous",
        "archive.next": "Next →",
        "archive.prev_en": "← Previous",
        "archive.next_en": "Next →",
        "cal.dow1": "M", "cal.dow2": "T", "cal.dow3": "W",
        "cal.dow4": "T", "cal.dow5": "F", "cal.dow6": "S",
        "cal.dow7": "S",
        "pager.prev": "← Previous",
        "pager.next": "Next →",
        "pager.oldest": "← Oldest issue",
        "pager.newest": "Latest issue →",
        "pager.hint": "← → keys to page",
        "tags.title": "Tag Index",
        "tags.cloud": "Tag cloud",
        "tags.all": "All tags",
        "tags.topics": "Topics",
        "tags.names": "Products & names",
        "tags.jump": "Jump to a letter",
        "tags.empty": "No tags yet — they light up after the first rated issue.",
        "tags.empty_page": "No projects for this tag yet.",
        "tags.projects": "projects",
        "tags.unrated": "unrated",
        # tier history
        "tiers.title": "Tier history",
        "tiers.lede": "Every repository ever rated, each once, in the tier of "
                      "its latest rating. Open a tier for its newest month, "
                      "or pick any month in the table.",
        "tiers.by_month": "By month",
        "tiers.month": "Month",
        "tiers.all": "All tiers",
        "tiers.switch": "Switch tier",
        "tiers.months": "Switch month",
        "tiers.empty": "No repository has been rated yet.",
        "tiers.rated_on": "rated {day}",
        "tiers.page_desc": "{n} repositories rated {tier} tier in {month}.",
        "foot.brand": "tierlist.tech · daily signal radar",
        # feed + page metadata (Atom channel, <meta description>, OG cards)
        "feed.title": "tierlist.tech brief",
        "feed.subtitle": "Daily signal radar · dev-tool picks",
        "feed.entry_summary": "tierlist.tech pick, {tier} tier · {title}",
        "meta.description": "A daily automated sweep of new GitHub repositories, "
                            "surfacing dev tools worth watching before the "
                            "mainstream rankings pick them up, each with a "
                            "tier and the reasoning behind it.",
        "foot.generated": "generated",
        "foot.updated": "updated",
        "news.title": "Live news",
        "news.live": "LIVE",
        "news.updated_at": "updated {ts} UTC",
        "news.lede": "Tech signals · HN + RSS, refreshed every 15 minutes",
        "news.ticker_prefix": "FLASH",
        "news.empty": "No news items yet.",
        "news.points": "{n} points",
        # search
        "search.title": "Search",
        "search.placeholder": "project / tag / owner…",
        "search.hint": "Type 1+ characters, then Enter or click",
        "search.empty_query": "Type a keyword to start. Everything matches: "
                              "name, tag, owner (before the slash). "
                              "Lead with # to match that tag exactly, "
                              "e.g. #mcp.",
        "search.no_results": "No matches. Try another word.",
        "search.results": "{n} results",
        "search.index_count": "{n} projects indexed",
        "search.submit": "Search",
        "search.index_note": "index refreshed with every daily sweep",
        # language switch
        "lang.alt": "中",
        "lang.zh": "中文",
        "lang.en": "English",
        "lang.zh_short": "中",
        "lang.en_short": "EN",
    },
}

LANGS = ("zh", "en")

MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December")


def lang_prefix(lang: str) -> str:
    """URL prefix of a language tree: en is the site root, zh lives under
    /zh. The single definition — `editions.lang_prefix` re-exports it."""
    return "/zh" if lang == "zh" else ""


def other_lang(lang: str) -> str:
    return "zh" if lang == "en" else "en"


def switch_url(lang: str, page_path: str = "/") -> str:
    """Where the masthead's language link goes: the SAME page in the other
    tree, not that tree's root.

    Every page exists in both trees (the site writer renders each one per
    language), so /tags/mcp.html has a counterpart at /zh/tags/mcp.html and
    sending the reader to /zh/ instead loses their place. This was previously
    computed in six places from the prefix alone — including a helper that
    accepted a `page_path` argument and then ignored it — so the link always
    landed on a root no matter which page it was clicked from.

    `page_path` is prefix-free (what render._page_vars threads through)."""
    return (lang_prefix(other_lang(lang)) + (page_path or "/")) or "/"


def t(key: str, lang: str = "zh", **fmt) -> str:
    """UI string for `lang`; unknown keys return the key itself so a missing
    translation is loud (a bare "nav.x" on the page) rather than silent."""
    table = STRINGS.get(lang, STRINGS["zh"])
    text = table.get(key, STRINGS["zh"].get(key, key))
    return text.format(**fmt) if fmt else text


def reason_in(lang: str, reason: str | None, reason_en: str | None) -> str:
    """The model's one-line judgment in the page's language.

    It is written in Chinese, with an English version beside it. An English
    page whose row has no English yet (an older score not translated, or a
    run whose translation step failed) shows the Chinese line rather than
    nothing; `intel.translate_reasons` fills the gap on the next run."""
    if lang == "en" and reason_en:
        return reason_en
    return reason or ""


def month_label(year: int, month: int, lang: str) -> str:
    """Archive calendar head: 2026年8月 (zh) / August 2026 (en)."""
    if lang == "en":
        return f"{MONTHS_EN[month - 1]} {year}"
    return f"{year}年{month}月"
