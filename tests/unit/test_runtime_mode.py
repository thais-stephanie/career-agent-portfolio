"""Personal or demo, and the refusals that keep them apart.

The defect these exist for: the documented launch path served `data/demo.db`,
so the product opened on nineteen invented postings while the owner's 18,549
real ones sat unused. Nobody chose that. It was a default in a document, and
nothing downstream could tell the difference.
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

from career_agent.runtime import (
    DEMO_DB_PATH,
    RuntimeMode,
    RuntimeModeError,
    database_ref,
    identity_of,
    read_identity,
    resolve_database,
    stamp_identity,
)
from career_agent.runtime.mode import (
    DEFAULT_PERSONAL_DB_PATH,
    indicator,
    record_retrieval,
)

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src"
    / "career_agent"
    / "storage"
    / "migrations"
    / "0016_database_identity.sql"
)


@pytest.fixture()
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("CREATE TABLE job (id TEXT PRIMARY KEY, closed_at TEXT);")
    # The real DDL, not a restatement of it. A fixture that declares its own
    # copy of a schema is two definitions that must agree with nothing
    # checking -- which broke five tests in the search-index work.
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    return conn


# =========================================================================
# The database says what it is
# =========================================================================


def test_an_unstamped_database_is_not_silently_adopted(db: sqlite3.Connection) -> None:
    """The fifth chance to read absence as permission, declined.

    A file that has never said which population it belongs to is a question,
    not a personal database. `identity_of` refuses rather than assuming, and
    the message names the command that would settle it.
    """
    assert read_identity(db) is None
    with pytest.raises(RuntimeModeError) as caught:
        identity_of(db, RuntimeMode.PERSONAL)
    assert "does not say whether it is personal or demo" in str(caught.value)


def test_personal_mode_refuses_a_demo_database(db: sqlite3.Connection) -> None:
    """The guarantee. Never a downgrade, never a fallback.

    Showing invented postings to someone who asked for their own is worse
    than showing nothing, because nothing is honest.
    """
    stamp_identity(db, RuntimeMode.DEMO, "demo")
    with pytest.raises(RuntimeModeError) as caught:
        identity_of(db, RuntimeMode.PERSONAL)
    assert "never falls back" in str(caught.value)


def test_demo_mode_refuses_a_personal_database(db: sqlite3.Connection) -> None:
    """The other direction matters too: a screenshot session must not
    accidentally publish real postings from someone's job search."""
    stamp_identity(db, RuntimeMode.PERSONAL, "mine")
    with pytest.raises(RuntimeModeError):
        identity_of(db, RuntimeMode.DEMO)


def test_a_demo_database_cannot_be_promoted_to_personal(db: sqlite3.Connection) -> None:
    """Re-stamping is how the two populations would mix in one file.

    Refused. A demo database holds only invented rows, so deleting it and
    collecting into a fresh one loses nothing.
    """
    stamp_identity(db, RuntimeMode.DEMO, "demo")
    with pytest.raises(RuntimeModeError) as caught:
        stamp_identity(db, RuntimeMode.PERSONAL, "mine")
    assert "must never mix" in str(caught.value)


def test_stamping_the_same_kind_twice_is_idempotent(db: sqlite3.Connection) -> None:
    first = stamp_identity(db, RuntimeMode.PERSONAL, "mine")
    second = stamp_identity(db, RuntimeMode.PERSONAL, "renamed")
    assert second.kind is RuntimeMode.PERSONAL
    assert second.label == "renamed"
    # The creation date is the file's, not this call's.
    assert second.created_at == first.created_at


# =========================================================================
# Which file a mode is allowed to open
# =========================================================================


def test_demo_mode_ignores_an_explicit_path(tmp_path: pathlib.Path) -> None:
    """ "Demo" means one specific throwaway database.

    Letting `--db` redirect it is how invented rows end up somewhere real.
    """
    assert resolve_database(RuntimeMode.DEMO, tmp_path / "somewhere-real.db") == DEMO_DB_PATH


def test_personal_mode_prefers_explicit_then_environment_then_default(
    tmp_path: pathlib.Path,
) -> None:
    explicit = tmp_path / "explicit.db"
    assert resolve_database(RuntimeMode.PERSONAL, explicit) == explicit
    assert resolve_database(
        RuntimeMode.PERSONAL, None, env={"CAREER_AGENT_DB": "data/from-env.db"}
    ) == pathlib.Path("data/from-env.db")
    assert resolve_database(RuntimeMode.PERSONAL, None, env={}) == DEFAULT_PERSONAL_DB_PATH


def test_personal_mode_never_resolves_to_the_demo_database() -> None:
    """Stated as its own test because it is the whole bug in one line."""
    assert resolve_database(RuntimeMode.PERSONAL, None, env={}) != DEMO_DB_PATH


# =========================================================================
# What the interface is allowed to show
# =========================================================================


def test_the_database_reference_never_contains_a_path(tmp_path: pathlib.Path) -> None:
    """This string is rendered in a page that gets screenshotted.

    An absolute path on Windows carries the person's username through the
    screenshot and into a portfolio.
    """
    ref = database_ref(tmp_path / "career.db", "mine")
    assert "mine" in ref
    assert str(tmp_path) not in ref
    assert "/" not in ref and "\\" not in ref


def test_two_databases_with_one_label_stay_distinguishable(
    tmp_path: pathlib.Path,
) -> None:
    a = database_ref(tmp_path / "a" / "career.db", "personal")
    b = database_ref(tmp_path / "b" / "career.db", "personal")
    assert a != b


def test_the_indicator_reports_the_mode_and_what_is_in_it(
    db: sqlite3.Connection, tmp_path: pathlib.Path
) -> None:
    stamp_identity(db, RuntimeMode.PERSONAL, "mine")
    db.execute("INSERT INTO job (id, closed_at) VALUES ('j1', NULL)")
    db.execute("INSERT INTO job (id, closed_at) VALUES ('j2', NULL)")
    db.execute("INSERT INTO job (id, closed_at) VALUES ('j3', '2026-01-01')")

    ind = indicator(db, tmp_path / "career.db", read_identity(db), displayed=2)
    assert ind.mode is RuntimeMode.PERSONAL
    assert ind.banner == "Personal data"
    assert ind.is_personal is True
    # Closed postings are not "active".
    assert ind.total_active == 2
    assert ind.displayed == 2
    assert ind.as_dict()["mode"] == "PERSONAL"


def test_the_demo_banner_says_demo(db: sqlite3.Connection, tmp_path: pathlib.Path) -> None:
    """Required to be visible, so it is required to exist."""
    stamp_identity(db, RuntimeMode.DEMO, "demo")
    ind = indicator(db, tmp_path / "demo.db", read_identity(db))
    assert ind.banner == "Demo data"
    assert ind.is_personal is False


def test_a_retrieval_that_found_nothing_still_updates_the_clock(
    db: sqlite3.Connection,
) -> None:
    """ "When did we last look" and "when was the newest posting published"
    are different questions.

    A run that found nothing still ran, and the interface must be able to say
    "checked, nothing new" instead of showing a three-week-old date and
    implying the collector has not run.
    """
    stamp_identity(db, RuntimeMode.PERSONAL, "mine")
    assert read_identity(db).last_retrieval_at is None

    record_retrieval(db, "2026-09-04T12:00:00Z")
    assert read_identity(db).last_retrieval_at == "2026-09-04T12:00:00Z"

    # No jobs were added, and the clock still moved.
    record_retrieval(db, "2026-09-04T13:00:00Z")
    assert read_identity(db).last_retrieval_at == "2026-09-04T13:00:00Z"


def test_relabelling_does_not_erase_the_retrieval_clock(db: sqlite3.Connection) -> None:
    """An omitted field means "leave it", not "clear it" -- the rule this
    repository learned when a status transition erased the date you applied."""
    stamp_identity(db, RuntimeMode.PERSONAL, "mine")
    record_retrieval(db, "2026-09-04T12:00:00Z")
    stamp_identity(db, RuntimeMode.PERSONAL, "renamed")
    assert read_identity(db).last_retrieval_at == "2026-09-04T12:00:00Z"
