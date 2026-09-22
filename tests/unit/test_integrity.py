"""Every check proves it catches the thing it is named after.

A checker nobody has broken on purpose is a checker that returns an empty list
and looks like good news. So each test below builds the defect and asserts the
check finds it, and the last one asserts a clean database produces nothing.
"""

from __future__ import annotations

import sqlite3

import pytest

from career_agent.storage import integrity
from career_agent.storage.integrity import BROKEN, NOTE, STALE, audit

SCHEMA = """
CREATE TABLE company (id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE job_raw (content_hash TEXT PRIMARY KEY, description_text TEXT);
CREATE TABLE job (
  id TEXT PRIMARY KEY, company_id TEXT, content_hash TEXT,
  first_seen_at TEXT, closed_at TEXT, collection_status TEXT, location_raw TEXT
);
CREATE TABLE job_match (
  id TEXT PRIMARY KEY, job_id TEXT, content_hash TEXT,
  config_id TEXT, config_version INTEGER, schema_version INTEGER, countries TEXT
);
CREATE TABLE job_application (
  id TEXT PRIMARY KEY, job_id TEXT, status TEXT, applied_at TEXT
);
CREATE TABLE job_application_event (id TEXT PRIMARY KEY, job_id TEXT);
CREATE TABLE job_discovery_source (id TEXT PRIMARY KEY, job_id TEXT);
"""


@pytest.fixture
def db() -> sqlite3.Connection:
    """A small, internally consistent database. Each test breaks one thing."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO company VALUES ('c1', 'Acme')")
    conn.execute("INSERT INTO job_raw VALUES ('h1', 'We build things.')")
    conn.execute(
        "INSERT INTO job VALUES ('j1','c1','h1','2026-01-01T00:00:00Z',NULL,'NORMALISED','Oslo')"
    )
    conn.execute(
        "INSERT INTO job_match VALUES ('m1','j1','h1','personal',2,?,'|NO|')",
        (integrity.MATCH_SCHEMA_VERSION,),
    )
    conn.commit()
    return conn


def found(conn: sqlite3.Connection, name: str):
    return next((f for f in audit(conn) if f.check == name), None)


def test_a_consistent_database_reports_nothing(db: sqlite3.Connection) -> None:
    assert list(audit(db)) == []


def test_a_score_whose_posting_vanished_is_broken(db: sqlite3.Connection) -> None:
    db.execute("DELETE FROM job WHERE id = 'j1'")
    finding = found(db, "orphan_match")
    assert finding is not None and finding.severity == BROKEN
    assert finding.examples == ("m1",)


def test_a_score_whose_text_vanished_is_broken(db: sqlite3.Connection) -> None:
    """The drawer quotes evidence out of `job_raw`. Without it a score cannot
    be explained, which is worse than it being absent."""
    db.execute("DELETE FROM job_raw WHERE content_hash = 'h1'")
    assert found(db, "orphan_match") is not None


def test_tracking_state_for_a_deleted_posting_is_broken(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO job_application VALUES ('a1','ghost','SAVED',NULL)")
    finding = found(db, "orphan_application")
    assert finding is not None and finding.severity == BROKEN


def test_a_status_outside_the_vocabulary_is_broken(db: sqlite3.Connection) -> None:
    """A CHECK constraint added by a later migration does not validate the rows
    that were already there, which is why this is asked of the data."""
    db.execute("INSERT INTO job_application VALUES ('a1','j1','INTERESTED',NULL)")
    finding = found(db, "unknown_application_status")
    assert finding is not None and finding.severity == BROKEN


def test_interviewing_with_no_date_of_application_is_broken(db: sqlite3.Connection) -> None:
    """ADR-0012 in a query. The date records that an application was SENT, and
    no later move makes that untrue -- so a row past APPLIED without one means
    the date was lost, which is the defect that ADR exists to prevent."""
    db.execute("INSERT INTO job_application VALUES ('a1','j1','INTERVIEW',NULL)")
    finding = found(db, "applied_without_date")
    assert finding is not None and finding.severity == BROKEN


def test_a_saved_posting_needs_no_date(db: sqlite3.Connection) -> None:
    """The other half. Nobody applied, so nothing is missing."""
    db.execute("INSERT INTO job_application VALUES ('a1','j1','SAVED',NULL)")
    assert found(db, "applied_without_date") is None


def test_closed_at_and_collection_status_must_agree(db: sqlite3.Connection) -> None:
    db.execute("UPDATE job SET closed_at = '2026-02-01T00:00:00Z' WHERE id = 'j1'")
    finding = found(db, "closed_state_disagrees")
    assert finding is not None and finding.severity == BROKEN


def test_norway_is_not_the_boolean_false(db: sqlite3.Connection) -> None:
    """`norway: NO` written unquoted, read by YAML 1.1 as a boolean.

    This shipped once. The generator and the guard are fixed; this is how the
    DATABASE answers whether it still carries any.
    """
    db.execute("UPDATE job_match SET countries = '|FALSE|' WHERE id = 'm1'")
    finding = found(db, "boolean_country_code")
    assert finding is not None and finding.severity == BROKEN


def test_a_score_from_an_older_analysis_build_is_stale_not_broken(
    db: sqlite3.Connection,
) -> None:
    db.execute("UPDATE job_match SET schema_version = 1 WHERE id = 'm1'")
    finding = found(db, "stale_analysis")
    assert finding is not None
    assert finding.severity == STALE
    assert not finding.is_broken, "a score correct when written must not read as broken"


def test_an_earlier_configuration_version_is_history_not_a_defect(
    db: sqlite3.Connection,
) -> None:
    """The false alarm this module produced the first time it ran.

    `job_match` is versioned on purpose: v1 and v2 sit side by side so a past
    evaluation stays reproducible, and the read path has scoped every query to
    one version since the beginning. 21 rows carrying `FALSE` as a country code
    and 21,202 rows below the current schema were all `config_version = 1`,
    which no screen has read since the Post-V3 reconciliation.
    """
    db.execute(
        "INSERT INTO job_match VALUES ('m0','j1','h1','personal',1,1,'|FALSE|')",
    )
    assert found(db, "boolean_country_code") is None, "history was reported as a broken row"
    assert found(db, "stale_analysis") is None, "history was reported as needing a rescore"

    superseded = found(db, "superseded_version")
    assert superseded is not None
    assert superseded.severity == NOTE
    assert superseded.examples == ("m0",)


def test_a_posting_with_no_description_is_only_a_note(db: sqlite3.Connection) -> None:
    """A board that published a title and no body is a real thing, and the
    product handles it. Counted so a sudden jump is visible."""
    db.execute("INSERT INTO job VALUES ('j2','c1','missing',NULL,NULL,'NORMALISED','Oslo')")
    finding = found(db, "job_without_text")
    assert finding is not None and finding.severity == NOTE


def test_nothing_in_this_module_writes() -> None:
    """The rule that lets it be run on the owner's real corpus without thought.

    Two of the tables it reads hold RAW OBSERVATIONS, and an observation this
    system edits is no longer an observation.
    """
    import inspect

    source = inspect.getsource(integrity)
    for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE"):
        assert verb not in source.upper().replace("CREATED", ""), (
            f"{verb} appears in a module that must only read"
        )
