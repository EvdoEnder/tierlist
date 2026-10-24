"""Schema contract pins: the intel namespace, all four tables, additive DDL.

No database is touched — these assert on the statement strings themselves,
so a careless edit that drops IF NOT EXISTS or a table fails here.
"""
from __future__ import annotations

from intel.store.schema import SCHEMA_STATEMENTS

TABLES = ("intel.items", "intel.scores", "intel.daily_rollup", "intel.runs",
          "intel.star_snapshots")


def test_schema_creates_the_intel_namespace():
    assert any("CREATE SCHEMA IF NOT EXISTS intel" in s
               for s in SCHEMA_STATEMENTS)


def test_every_state_table_has_a_statement():
    for table in TABLES:
        assert any(table in s for s in SCHEMA_STATEMENTS), table


def test_table_ddl_is_additive_if_not_exists_only():
    creates = [s for s in SCHEMA_STATEMENTS if "CREATE TABLE" in s]
    assert len(creates) >= len(TABLES)
    for stmt in creates:
        assert "IF NOT EXISTS" in stmt, stmt.strip().splitlines()[0]


def test_items_tags_column_is_added_additively():
    alters = [s for s in SCHEMA_STATEMENTS if s.startswith("ALTER TABLE")]
    assert any("intel.items" in s and "ADD COLUMN IF NOT EXISTS tags JSONB" in s
               for s in alters), alters


def test_star_snapshots_key_spans_item_and_date():
    stmt = next(s for s in SCHEMA_STATEMENTS if s.lstrip().startswith(
        "CREATE TABLE") and "star_snapshots" in s)
    assert "PRIMARY KEY (item_id, snap_date)" in stmt


def test_hot_path_indexes_are_still_created():
    """The tag containment scan (items.tags GIN) and the two date-window
    queries (scores/star_snapshots by snap_date) need their indexes; this
    test lives on the strings, since that is all the offline schema suite
    sees."""
    index_stmts = [s for s in SCHEMA_STATEMENTS if s.startswith("CREATE INDEX")]
    assert any("USING GIN" in s and "intel.items" in s for s in index_stmts)
    assert any("intel.scores (snap_date)" in s for s in index_stmts)
    assert any("intel.star_snapshots (snap_date)" in s for s in index_stmts)


def test_no_statement_touches_a_table_before_it_is_created():
    """The statements run in list order on an empty database. An index or
    ALTER placed above its CREATE TABLE never fails on a store that already
    has the table, and fails every fresh install."""
    import re

    created: set[str] = set()
    for stmt in SCHEMA_STATEMENTS:
        made = re.search(r"CREATE TABLE IF NOT EXISTS (intel\.\w+)", stmt)
        if made:
            created.add(made.group(1))
            continue
        for table in re.findall(r"\b(?:ON|ALTER TABLE) (intel\.\w+)", stmt):
            assert table in created, f"{table} used before it is created: {stmt.strip()}"
