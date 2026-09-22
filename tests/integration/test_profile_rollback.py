"""Going back to a previous set of answers, without erasing the ones between.

WHY THIS EXISTS
---------------
`search_profile_version` has held a snapshot of the owner's own answers since
migration 0001, and `profile_history` has been filling it since V1.4. Nothing
had ever read them back. They are the only record of what her preferences used
to say, which is what a rollback needs and what a diff is made of.

On her real database there are four of them, two written within seventeen
seconds of each other on 2026-09-08 when she set her seniority preferences
through the interface.

THE MODEL
---------
**History is never rewritten.** Going back to revision 2 writes revision 5
carrying revision 2's answers; the intervening ones stay exactly where they
are. A product that erased them would make "undo" the one action with no undo
of its own.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from career_agent.config.candidate_writer import set_candidate_fields
from career_agent.profile_history import record
from career_agent.storage.db import connect, migrate
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def api() -> Iterator[JobsApi]:
    """A configuration and a database of its own. Never the owner's."""
    root = Path(tempfile.mkdtemp(prefix="profile-rollback"))
    config = root / "config"
    config.mkdir()
    shutil.copy(ROOT / "config" / "search.worked-example.yaml", config)
    shutil.copy(config / "search.worked-example.yaml", config / "search.local.yaml")
    shutil.copy(ROOT / "config" / "places.yaml", config)
    db = root / "career.db"
    conn = connect(db)
    migrate(conn)
    conn.close()
    yield JobsApi(ServerConfig(db_path=db, config_dir=config, port=0), quiet=True)


def save(api: JobsApi, **changes) -> None:
    """An edit, recorded the way the product records one."""
    set_candidate_fields(api.config.config_dir, changes)
    api._search_config = None
    with connect(api.config.db_path) as conn:
        record(conn, api.config.config_dir)


def numbers(api: JobsApi) -> list[int]:
    return [r["number"] for r in api.profile_history(query={}, body={})["revisions"]]


# =========================================================================
# 1. THE HISTORY
# =========================================================================


def test_it_reads_back_what_was_already_being_written(api) -> None:
    """Four revisions on the owner's real database, and nothing had read one."""
    save(api, seniority_excluded=["LEAD"])
    save(api, seniority_excluded=["LEAD", "STAFF"])

    out = api.profile_history(query={}, body={})

    assert out["count"] >= 2
    assert out["revisions"][0]["is_current"] is True
    assert numbers(api) == sorted(numbers(api), reverse=True), "newest first"


def test_each_revision_says_what_moved_rather_than_that_something_did(api) -> None:
    """ "Your profile changed" is not something anybody can act on."""
    save(api, seniority_excluded=["LEAD"])
    save(api, seniority_excluded=["LEAD", "STAFF"])

    newest = api.profile_history(query={}, body={})["revisions"][0]

    assert "seniority_excluded" in newest["changed"]
    moved = newest["changed"]["seniority_excluded"]
    assert moved["from"] == ["LEAD"]
    assert moved["to"] == ["LEAD", "STAFF"]


def test_the_history_does_not_ship_her_whole_profile(api) -> None:
    """A revision list is a navigation aid. Returning every candidate answer
    in a payload the Jobs screen also polls would put her profile on the wire
    for a control that needs dates and a count."""
    save(api, seniority_excluded=["LEAD"])

    newest = api.profile_history(query={}, body={})["revisions"][0]

    assert "answers" not in newest
    assert set(newest) == {
        "number",
        "created_at",
        "content_hash",
        "changed",
        "changed_nothing_visible",
        "is_current",
    }


# =========================================================================
# 2. GOING BACK
# =========================================================================


def test_restoring_puts_the_old_answers_back(api) -> None:
    """The journey: two edits, then go back to what the first one said.

    `numbers()` is newest-first, so the oldest recorded revision is the last
    element. The state BEFORE any edit is not among them -- nothing records a
    revision until something has been saved -- so revision 1 holds
    `["LEAD"]`, which is what going back to it restores.
    """
    save(api, seniority_excluded=["LEAD"])
    save(api, seniority_excluded=["LEAD", "STAFF", "PRINCIPAL"])
    oldest = numbers(api)[-1]

    api.restore_profile(query={}, body={"number": oldest})

    from career_agent.config.candidate_writer import current_candidate_fields

    now = current_candidate_fields(api.config.config_dir)
    assert now["seniority_excluded"] == ["LEAD"]


def test_restoring_erases_nothing(api) -> None:
    """**History is never rewritten.**

    A product that deleted the revisions between here and there would make
    undo the one action with no undo of its own.

    And a restore adds no revision of its own, which is correct rather than a
    gap: `record` is content-addressed -- "hashed by CONTENT so reformatting a
    file is not a new revision of a person" -- and answers identical to an
    existing revision are that revision. The `config_version` does move,
    because a scoring identity is a different question from a set of answers.
    """
    save(api, seniority_excluded=["LEAD"])
    save(api, seniority_excluded=["LEAD", "STAFF"])
    before = numbers(api)

    out = api.restore_profile(query={}, body={"number": before[-1]})
    with connect(api.config.db_path) as conn:
        record(conn, api.config.config_dir)

    after = numbers(api)
    assert set(before).issubset(set(after)), "a revision disappeared"
    assert after == before, "restoring known answers invented a revision"
    assert out["config_version"] > 0, "the scoring identity has to move even so"


def test_restoring_costs_a_recalculation_and_says_so(api) -> None:
    save(api, seniority_excluded=["LEAD"])
    save(api, seniority_excluded=["LEAD", "STAFF"])

    out = api.restore_profile(query={}, body={"number": numbers(api)[-1]})

    assert out["rescore_required"] is True
    assert out["config_version"] > 0
    assert "seniority_excluded" in out["fields"]


# =========================================================================
# 3. WHAT IT REFUSES
# =========================================================================


def test_restoring_the_current_revision_is_refused(api) -> None:
    """It would bump the configuration version and ask for a rescore over a
    change nobody made."""
    save(api, seniority_excluded=["LEAD"])

    with pytest.raises(ApiError) as refused:
        api.restore_profile(query={}, body={"number": numbers(api)[0]})

    assert refused.value.status == 400
    assert "already" in refused.value.message


def test_an_unknown_revision_is_a_404_rather_than_a_guess(api) -> None:
    save(api, seniority_excluded=["LEAD"])

    with pytest.raises(ApiError) as refused:
        api.restore_profile(query={}, body={"number": 999})

    assert refused.value.status == 404


def test_a_missing_number_is_refused_before_anything_is_read(api) -> None:
    for body in ({}, {"number": "2"}, {"number": None}):
        with pytest.raises(ApiError) as refused:
            api.restore_profile(query={}, body=body)
        assert refused.value.status == 400


def test_a_restore_may_not_reach_past_the_editable_whitelist(api) -> None:
    """A recorded revision covers every candidate-owned fact, and
    `ownership.FACTS` deliberately includes facts that are hers but not
    editable from a browser. Restoring one of those would be this route
    reaching past the whitelist that exists to stop exactly that.
    """
    from career_agent.config.candidate_writer import FIELDS
    from career_agent.profile_history import candidate_answers

    recorded = set(candidate_answers(api.config.config_dir))

    # The guard is meaningful only if the two sets genuinely differ; if they
    # ever converge this test should be deleted rather than left passing
    # vacuously.
    assert recorded, "no candidate answers were recorded at all"
    assert recorded.issuperset(set(FIELDS)), (
        "the writer can change a field the history does not record"
    )


# =========================================================================
# 4. AND IT SURVIVES A RESTART
# =========================================================================


def test_the_history_is_on_disk_rather_than_in_the_process(api) -> None:
    save(api, seniority_excluded=["LEAD"])
    save(api, seniority_excluded=["LEAD", "STAFF"])
    before = numbers(api)

    restarted = JobsApi(
        ServerConfig(db_path=api.config.db_path, config_dir=api.config.config_dir, port=0),
        quiet=True,
    )

    assert numbers(restarted) == before


def test_the_rows_are_the_ones_the_product_already_wrote(api) -> None:
    """Read from `search_profile_version` itself, so this cannot pass over a
    second store somebody added beside it."""
    save(api, seniority_excluded=["LEAD"])

    with sqlite3.connect(api.config.db_path) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM search_profile_version").fetchone()[0]

    assert rows == len(numbers(api))


def test_a_field_that_did_not_exist_is_not_reported_as_having_been_empty(api) -> None:
    """**Found on the owner's own history.**

    `changed_between` reads both sides through `.get`, so a field ABSENT from
    the older snapshot and one PRESENT holding nothing both arrive as None.
    Her revision 4 rendered `seniority_excluded: None -> [...]` for a field
    whose real previous value was an empty list -- it had been added to the
    writer between the two recordings.

    "Not recorded then" and "was empty" are different sentences, and only the
    snapshot's keys can tell them apart.
    """
    save(api, seniority_excluded=["LEAD"])
    save(api, seniority_excluded=["LEAD", "STAFF"])

    newest = api.profile_history(query={}, body={})["revisions"][0]
    moved = newest["changed"]["seniority_excluded"]

    assert moved["existed_before"] is True, "a field that was recorded reads as recorded"


def test_a_revision_with_no_visible_change_says_so(api) -> None:
    """Her revision 3 is one. The recorder is content-addressed and saw a real
    difference; a field-by-field diff cannot show what it was.

    Rendering a blank space under a revision invites the reader to assume the
    product lost something.
    """
    save(api, seniority_excluded=["LEAD"])
    revisions = api.profile_history(query={}, body={})["revisions"]

    # The first ever revision has nothing before it, so it is not "no visible
    # change" -- it is the beginning, and the flag must not claim otherwise.
    assert revisions[-1]["changed_nothing_visible"] is False
