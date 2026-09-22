"""Her answers get a version number of their own.

Three numbers, three questions, and until V1.4 only two existed:

    MATCH_SCHEMA_VERSION   the CODE moved
    config_version         the CONFIGURATION moved
    profile revision       HER ANSWERS moved

The third was missing and its absence had one specific consequence:
`config_version` is the only number that changes when she tells the product
where she lives, so "stale because the matcher changed" and "stale because I
answered a question" were the same number. Nobody could tell which had
happened -- not a person looking at a screen, and not a later reader of the
database.

WHAT THESE TESTS PROTECT
------------------------
That a revision records what she SAID and nothing else; that it is idempotent
by CONTENT so reformatting a file is not a new revision of a person; that it
never writes to `search.local.yaml`; and that it changes no score. The last is
the one that would be tempting to get wrong, because a version number that
invalidated the corpus would make the whole feature something to avoid using.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.support import REAL_CONFIG_DIR, committed_config_dir

from career_agent import profile_history
from career_agent.config.ownership import FACTS, Owner
from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"


@pytest.fixture
def writable_config() -> Iterator[Path]:
    """A copy of the committed configuration this test may edit.

    A test must never write to `config/`: the preference editor writes
    `search.local.yaml`, which overrides the committed example for every later
    command and bumps `config_version`, detaching the corpus from its scores.
    """
    root = Path(tempfile.mkdtemp(prefix="profile-revision")) / "config"
    shutil.copytree(REAL_CONFIG_DIR, root)
    for stray in [*root.glob("*.local.yaml"), *root.glob("*.local.yaml.*")]:
        stray.unlink()
    yield root


@pytest.fixture
def api(writable_config: Path) -> Iterator[JobsApi]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="profile-revision")) / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        loaded, _ = load_search_config(writable_config)
        seed_demo(conn, loaded, source=DEMO_FILE)
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db_path, config_dir=writable_config, port=0), quiet=True)


def open_db(api: JobsApi):
    return connect(api.config.db_path)


# =========================================================================
# 1. WHAT IT RECORDS
# =========================================================================


def test_nothing_is_recorded_until_something_asks(api: JobsApi) -> None:
    """`search_profile_version` has existed since migration 0001 and held zero
    rows for the whole of M0 through V1.3. Seeding a database does not fill
    it: a corpus is not a person."""
    conn = open_db(api)
    try:
        assert profile_history.latest(conn) is None
    finally:
        conn.close()


def test_the_first_recording_is_revision_one(api: JobsApi) -> None:
    conn = open_db(api)
    try:
        with transaction(conn):
            revision = profile_history.record(conn, api.config.config_dir)
        assert revision.number == 1
        assert revision.recorded is True
        assert revision.content_hash
    finally:
        conn.close()


def test_only_candidate_owned_answers_are_recorded(api: JobsApi) -> None:
    """A weight, a lexicon phrase or a threshold changing is not a new
    revision of a PERSON. The ownership table is what says which is which, and
    this reads it rather than keeping a second list."""
    conn = open_db(api)
    try:
        with transaction(conn):
            revision = profile_history.record(conn, api.config.config_dir)
    finally:
        conn.close()

    expected = {
        fact.field for fact in FACTS if fact.owner is Owner.CANDIDATE and fact.field is not None
    }
    assert set(revision.answers) == expected
    assert expected, "the ownership table names no candidate fields at all"


def test_recording_twice_with_nothing_changed_records_nothing(api: JobsApi) -> None:
    conn = open_db(api)
    try:
        with transaction(conn):
            first = profile_history.record(conn, api.config.config_dir)
        with transaction(conn):
            second = profile_history.record(conn, api.config.config_dir)

        assert first.recorded is True
        assert second.recorded is False
        assert second.number == first.number == 1
        assert len(profile_history.history(conn)) == 1
    finally:
        conn.close()


def test_the_hash_is_of_the_answers_and_not_of_the_file(api: JobsApi) -> None:
    """Reformatting the YAML, reordering a section or editing a comment
    produces the same hash and no spurious revision.

    The same discipline `config_digest` follows -- it hashes `model_dump`,
    never the file bytes -- and for the same reason: a version that moved when
    nothing about the meaning moved teaches people to ignore versions.
    """
    conn = open_db(api)
    try:
        with transaction(conn):
            before = profile_history.record(conn, api.config.config_dir)

        starter = api.config.config_dir / "search.starter.yaml"
        starter.write_text(
            "# A comment nobody reads, added here.\n" + starter.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        with transaction(conn):
            after = profile_history.record(conn, api.config.config_dir)
        assert after.recorded is False
        assert after.content_hash == before.content_hash
    finally:
        conn.close()


# =========================================================================
# 2. WHAT IT CHANGES, WHICH IS NOTHING
# =========================================================================


def test_recording_writes_no_configuration_file(api: JobsApi) -> None:
    """It is a record of what she said, never a second writer of where it
    lives. Two writers over one file is how two files start disagreeing."""
    directory = api.config.config_dir
    before = {
        path.name: path.read_bytes() for path in sorted(directory.iterdir()) if path.is_file()
    }

    conn = open_db(api)
    try:
        with transaction(conn):
            profile_history.record(conn, directory)
    finally:
        conn.close()

    after = {path.name: path.read_bytes() for path in sorted(directory.iterdir()) if path.is_file()}
    assert after == before


def test_recording_moves_no_score(api: JobsApi) -> None:
    """A version number that invalidated the corpus would make the whole
    feature something to avoid using."""
    conn = open_db(api)
    try:
        before = conn.execute(
            "SELECT job_id, match_score, data_confidence, config_version FROM job_match"
            " ORDER BY job_id"
        ).fetchall()
        with transaction(conn):
            profile_history.record(conn, api.config.config_dir)
        after = conn.execute(
            "SELECT job_id, match_score, data_confidence, config_version FROM job_match"
            " ORDER BY job_id"
        ).fetchall()
    finally:
        conn.close()

    assert [tuple(row) for row in after] == [tuple(row) for row in before]


# =========================================================================
# 3. A CHANGE IS A REVISION, AND IT NAMES BOTH SIDES
# =========================================================================


def test_changing_an_answer_through_the_panel_records_a_revision(api: JobsApi) -> None:
    conn = open_db(api)
    try:
        with transaction(conn):
            profile_history.record(conn, api.config.config_dir)
    finally:
        conn.close()

    result = api.patch_profile(query={}, body={"changes": {"travel_max_pct": 40}})

    assert result["profile_revision"] == {"number": 2, "new": True}
    conn = open_db(api)
    try:
        assert len(profile_history.history(conn)) == 2
        latest = profile_history.latest(conn)
        assert latest is not None
        assert latest.answers["travel_max_pct"] == 40
    finally:
        conn.close()


def test_the_difference_names_both_values(api: JobsApi) -> None:
    """ "Your profile changed" is not something anybody can act on. "Most
    travel you would accept: 30 -> 15" is."""
    conn = open_db(api)
    try:
        with transaction(conn):
            first = profile_history.record(conn, api.config.config_dir)
    finally:
        conn.close()

    api.patch_profile(query={}, body={"changes": {"travel_max_pct": 40}})

    conn = open_db(api)
    try:
        second = profile_history.latest(conn)
    finally:
        conn.close()
    assert second is not None

    moved = profile_history.changed_between(first, second)
    assert set(moved) == {"travel_max_pct"}
    was, now = moved["travel_max_pct"]
    assert now == 40
    assert was != now


def test_saving_a_value_it_already_had_records_no_revision(api: JobsApi) -> None:
    """Content, not keystrokes.

    The committed configuration already tolerates 15% travel, and this test
    found that out by writing 15 and getting revision 1 back. Pressing Save on
    an unchanged form is a thing people do, and it must not produce a history
    of identical entries.
    """
    conn = open_db(api)
    try:
        with transaction(conn):
            profile_history.record(conn, api.config.config_dir)
    finally:
        conn.close()

    result = api.patch_profile(query={}, body={"changes": {"travel_max_pct": 15}})

    assert result["profile_revision"] == {"number": 1, "new": False}
    conn = open_db(api)
    try:
        assert len(profile_history.history(conn)) == 1
    finally:
        conn.close()


def test_a_failed_save_records_no_revision(api: JobsApi) -> None:
    """The settings were not written, so nothing about her answers moved."""
    from career_agent.web.api import ApiError

    conn = open_db(api)
    try:
        with transaction(conn):
            profile_history.record(conn, api.config.config_dir)
    finally:
        conn.close()

    with pytest.raises(ApiError):
        api.patch_profile(query={}, body={"changes": {"candidate_country": "not a country"}})

    conn = open_db(api)
    try:
        assert len(profile_history.history(conn)) == 1
    finally:
        conn.close()


# =========================================================================
# 4. AND IT REACHES THE SCREEN
# =========================================================================


def test_the_profile_payload_reports_nothing_rather_than_zero(api: JobsApi) -> None:
    """A revision of `0` reads as "you have answered nothing", and she may
    have answered everything and never run the command."""
    assert api.profile(query={}, body={})["profile_revision"] is None


def test_the_profile_payload_reports_the_revision_once_there_is_one(api: JobsApi) -> None:
    conn = open_db(api)
    try:
        with transaction(conn):
            profile_history.record(conn, api.config.config_dir)
    finally:
        conn.close()

    reported = api.profile(query={}, body={})["profile_revision"]
    assert reported["number"] == 1
    assert reported["recorded_at"]
    assert len(reported["digest"]) == 16


def test_the_snapshot_is_readable_json_rather_than_an_opaque_blob(api: JobsApi) -> None:
    """A history nobody can read is a history nobody can migrate."""
    conn = open_db(api)
    try:
        with transaction(conn):
            profile_history.record(conn, api.config.config_dir)
        row = conn.execute("SELECT yaml_snapshot FROM search_profile_version").fetchone()
    finally:
        conn.close()
    assert isinstance(json.loads(str(row["yaml_snapshot"])), dict)
