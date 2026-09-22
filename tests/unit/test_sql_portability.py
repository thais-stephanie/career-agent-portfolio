"""Acceptance criterion 16: the migration SQL must port to PostgreSQL.

Appendix A lists eighteen hazards that would turn a mechanical migration into a
rewrite. Several of them are invisible when you read SQL casually and expensive
to unwind once thousands of rows exist, so they are asserted here instead of
being left to discipline.
"""

import re
from pathlib import Path

import pytest

from career_agent.storage.db import discover_migrations

MIGRATIONS = discover_migrations()
SQL_FILES = [m.path for m in MIGRATIONS]

#: SQLite date functions have no PostgreSQL equivalent. Every timestamp in this
#: project is produced by career_agent.clock.now_utc() in Python instead.
#: Word-anchored so that `REFERENCES candidate(id)` is not mistaken for `date(`.
FORBIDDEN_DATE_FUNCTIONS = ["datetime", "julianday", "strftime", "date", "time"]


def _statements(sql: str) -> str:
    """SQL with comments stripped, so a hazard named in a comment is allowed."""
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return "\n".join(line.split("--", 1)[0] for line in without_block.splitlines())


@pytest.fixture(params=SQL_FILES, ids=lambda p: p.name)
def sql(request: pytest.FixtureRequest) -> str:
    path: Path = request.param
    return _statements(path.read_text(encoding="utf-8"))


def test_no_autoincrement(sql: str) -> None:
    """Hazard A1. ULID TEXT keys generated in Python instead."""
    assert "AUTOINCREMENT" not in sql.upper()


def test_no_integer_primary_key_rowid_alias(sql: str) -> None:
    """`INTEGER PRIMARY KEY` is SQLite's implicit rowid alias, which is exactly
    the sequence-migration problem A1 exists to avoid. schema_migration is the
    single deliberate exception: its version numbers are authored by hand."""
    offenders = [
        line.strip()
        for line in sql.splitlines()
        if re.search(r"\bINTEGER\s+PRIMARY\s+KEY\b", line, re.IGNORECASE)
        and "version" not in line.lower()
    ]
    assert not offenders, f"unexpected rowid-alias primary keys: {offenders}"


def test_no_sqlite_date_functions(sql: str) -> None:
    """Hazard A3.

    One documented exception: the dirty-ledger triggers of migration 0034
    stamp `marked_at` with `strftime(..., 'now')`, because a trigger body
    cannot call `clock.now_utc()` and is dialect-specific by nature -- the
    PostgreSQL port rewrites those six bodies with `now()`. The exception is
    held to the trigger bodies: the same file's DDL outside them is checked.
    """
    checked = _outside_trigger_bodies(sql)
    found = [
        fn for fn in FORBIDDEN_DATE_FUNCTIONS if re.search(rf"\b{fn}\s*\(", checked, re.IGNORECASE)
    ]
    assert not found, f"SQLite date functions are not portable: {found}"


def _outside_trigger_bodies(sql: str) -> str:
    """The migration text with every `CREATE TRIGGER ... END` removed."""
    return re.sub(r"CREATE\s+TRIGGER\b.*?\bEND\s*;", " ", sql, flags=re.IGNORECASE | re.DOTALL)


def test_the_date_function_check_is_not_fooled_by_column_names() -> None:
    """A guard on the guard: `candidate(id)` contains the substring `date(`,
    and an unanchored check would have failed on it. A test that can only pass
    is worse than no test."""
    assert not re.search(r"\bdate\s*\(", "REFERENCES candidate(id)", re.IGNORECASE)
    assert re.search(r"\bdate\s*\(", "SELECT date('now')", re.IGNORECASE)


def test_no_insert_or_replace(sql: str) -> None:
    """Hazard A5. INSERT OR REPLACE is not valid PostgreSQL, and it deletes
    then re-inserts, silently firing FK cascades."""
    assert "OR REPLACE" not in sql.upper()


def test_every_json_column_is_not_null_with_a_default(sql: str) -> None:
    """Hazard A13. A NULL in a TEXT column breaks the later JSONB conversion."""
    for line in sql.splitlines():
        if re.search(r"\b\w*_json\b", line, re.IGNORECASE) and "TEXT" in line.upper():
            upper = line.upper()
            assert "NOT NULL" in upper, f"JSON column without NOT NULL: {line.strip()}"
            assert "DEFAULT" in upper, f"JSON column without DEFAULT: {line.strip()}"


def test_enum_checks_use_portable_in_syntax(sql: str) -> None:
    """Hazard A11: CHECK (col IN (...)) translates; SQLite-specific forms do not."""
    for match in re.finditer(r"CHECK\s*\((.*?)\)\s*\)", sql, re.IGNORECASE | re.DOTALL):
        body = match.group(1).upper()
        assert " IN " in body or "IN(" in body, f"non-portable CHECK: {match.group(0)[:80]}"


def test_no_sqlite_specific_pragmas_inside_migrations() -> None:
    """Pragmas belong in db.py, where they apply to every connection. A pragma
    inside a migration would silently stop applying after the migration ran."""
    for path in SQL_FILES:
        assert "PRAGMA" not in _statements(path.read_text(encoding="utf-8")).upper()


def test_migration_filenames_are_ordered_and_gapless() -> None:
    versions = [m.version for m in MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1)), (
        f"migration versions must start at 1 with no gaps, got {versions}"
    )
