"""The `intel` schema as executable DDL.

Every statement is idempotent — CREATE ... IF NOT EXISTS only, so the store
evolves additively and a rerun never rewrites history. Column names here are
the contract the rest of the engine queries against.
"""
from __future__ import annotations

SCHEMA_STATEMENTS: list[str] = [
    "CREATE SCHEMA IF NOT EXISTS intel",

    """
    CREATE TABLE IF NOT EXISTS intel.items (
        id           TEXT PRIMARY KEY,
        vertical     TEXT NOT NULL,
        source       TEXT NOT NULL,
        url          TEXT NOT NULL,
        name         TEXT,
        description  TEXT,
        lang         TEXT,
        created_at   TIMESTAMPTZ,
        first_seen   TIMESTAMPTZ DEFAULT now(),
        last_seen    TIMESTAMPTZ DEFAULT now(),
        raw          JSONB
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS intel.scores (
        item_id      TEXT REFERENCES intel.items(id),
        snap_date    DATE NOT NULL,
        score        INT,
        dims         JSONB,
        reason       TEXT,
        verdict      TEXT,
        stars        INT,
        PRIMARY KEY (item_id, snap_date)
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS intel.daily_rollup (
        vertical     TEXT,
        snap_date    DATE,
        new_count    INT,
        top_ids      TEXT[],
        lang_dist    JSONB,
        PRIMARY KEY (vertical, snap_date)
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS intel.runs (
        id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        vertical     TEXT NOT NULL,
        started_at   TIMESTAMPTZ DEFAULT now(),
        finished_at  TIMESTAMPTZ,
        status       TEXT NOT NULL DEFAULT 'running',
        model        TEXT,
        fetched      INT,
        candidates   INT,
        scored       INT,
        usage        JSONB,
        error        TEXT
    )
    """,

    # The 新仓库热榜 as it was rendered: a slice of the fetch, ranked by
    # stars, which the prefilter drops and no other table keeps. Without it
    # every page rebuilt from the store — the landing page the news cron
    # rewrites every 15 minutes included — lost the board entirely, and
    # `--rebuild-site` stripped it from the daily permalinks it re-rendered.
    # Rows are stored in display shape so the read path cannot drift from
    # the write path.
    "ALTER TABLE intel.runs ADD COLUMN IF NOT EXISTS hot_new JSONB",
    # The run's GitHub Trending boards, {period: [display rows]}, stored for
    # the same reason as hot_new: GitHub's ranking of that day exists
    # nowhere else once the page is gone.
    "ALTER TABLE intel.runs ADD COLUMN IF NOT EXISTS trending JSONB",

    "ALTER TABLE intel.items ADD COLUMN IF NOT EXISTS tags JSONB",
    # Tag pages query `tags` with jsonb array ops (the `@>` containment /
    # element-expansion scans in deliver.site). A GIN index makes that a
    # look-up instead of a growing table scan.
    "CREATE INDEX IF NOT EXISTS idx_items_tags_gin ON intel.items USING GIN (tags)",
    # The gain board and the weekly/monthly windows both filter scores by
    # snap_date; without this, those scans walk the whole table.
    "CREATE INDEX IF NOT EXISTS idx_scores_snap_date ON intel.scores (snap_date)",

    # No FK on item_id: items are never deleted, and the daily upsert stays
    # cheap — one narrow table, one row per item per day.
    """
    CREATE TABLE IF NOT EXISTS intel.star_snapshots (
        item_id      TEXT NOT NULL,
        snap_date    DATE NOT NULL,
        stars        INT NOT NULL,
        PRIMARY KEY (item_id, snap_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_star_snapshots_snap_date "
    "ON intel.star_snapshots (snap_date)",

    # Live news (HN posts + RSS items). Upserted by the 15-minute news cron;
    # `points` is the HN score / RSS items are 0. No garbage collection: the
    # feed page reads the newest `published_at` window, old rows shrink into
    # the archive naturally.
    """
    CREATE TABLE IF NOT EXISTS intel.news (
        id           TEXT PRIMARY KEY,   -- 'hn:<objectID>' | 'rss:<feed>:<guid>'
        source       TEXT NOT NULL,      -- 'hn' | 'rss'
        feed         TEXT,               -- rss feed key (rss adapters), null for hn
        url          TEXT NOT NULL,
        title        TEXT NOT NULL,
        summary      TEXT,
        points       INT NOT NULL DEFAULT 0,
        published_at TIMESTAMPTZ,
        first_seen   TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_news_published "
    "ON intel.news (published_at DESC NULLS LAST, first_seen DESC)",


    # Which model version produced a score. Tier lines are percentiles of
    # one model's own scores, so a score is read against the lines of the
    # version that made it. NULL is a score from before this column, made
    # by the model the rubric's own `tiers:` lines were measured on.
    "ALTER TABLE intel.scores ADD COLUMN IF NOT EXISTS model_version TEXT",
    # The English version of `reason`; NULL until written or translated.
    "ALTER TABLE intel.scores ADD COLUMN IF NOT EXISTS reason_en TEXT",
    # The English line of a description its author wrote in another script.
    "ALTER TABLE intel.items ADD COLUMN IF NOT EXISTS description_en TEXT",

    # Tier lines per model version, written by intel.calibrate. The newest
    # row for a (vertical, version) is the one in force; older rows stay as
    # the record of each calibration. `dims` is the rubric's dimension set
    # the lines belong to, sorted, so a later rubric cannot borrow them.
    """
    CREATE TABLE IF NOT EXISTS intel.tier_lines (
        id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        vertical       TEXT NOT NULL,
        model_version  TEXT NOT NULL,
        dims           TEXT[] NOT NULL,
        s_line         INT NOT NULL,
        a_line         INT NOT NULL,
        b_line         INT NOT NULL,
        c_line         INT NOT NULL,
        sample         INT NOT NULL,
        source         TEXT NOT NULL,   -- 'rubric' | 'calibration'
        window_from    DATE,
        window_to      DATE,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,

    # A calibration's rescoring of recent items by a new model version. Kept
    # apart from intel.scores on purpose: writing there would replace the
    # scores past issues were built from and change their tiers. Keyed by
    # version so an interrupted calibration resumes without paying twice.
    """
    CREATE TABLE IF NOT EXISTS intel.calibration_scores (
        model_version  TEXT NOT NULL,
        item_id        TEXT NOT NULL,
        snap_date      DATE NOT NULL,
        score          INT NOT NULL,
        dims           JSONB,
        scored_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (model_version, item_id)
    )
    """,
]
