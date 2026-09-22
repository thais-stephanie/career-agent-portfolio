"""The living document had no test, and that is how it shipped a false instruction.

WHY THIS FILE EXISTS
--------------------
`career-agent source-matrix --write` regenerates `docs/product/source-matrix.md`,
which is the official answer to "can I get jobs out of this today, and whose move
is it". Every other answer in this repository is prose somebody has to remember to
update; this one is measured.

It had no unit test. So on 2026-09-10, immediately after a forced rescore of all
103,109 scoreable postings, it printed:

    Rescore: 158 postings have no stored score yet.

All 158 are TEXTLESS. No configuration can ever score a posting with no
description in it, so that sentence sent a reader to run a twenty-minute command
that would change nothing, twice. An action a reader cannot carry out is worse
than no action at all.

The two things asserted here are the two that were wrong or nearly wrong: the
arithmetic behind the Next action column, and the ORDER of `_state_for`, where
`postings` has to be asked before the owner-run rule or work the owner has
already done reads as waiting for her.
"""

from __future__ import annotations

import pytest

from career_agent.sources.catalogue import Coverage, Source, SourceStatus
from career_agent.sources.matrix import (
    OWNER_RUN_ONLY,
    MatrixState,
    _next_action,
    _state_for,
)


def test_historical_content_scores_do_not_prove_current_inventory() -> None:
    import sqlite3

    from career_agent.sources.matrix import _full_content_by_provider, _scored_by_provider

    with sqlite3.connect(":memory:") as conn:
        conn.executescript("""
            CREATE TABLE job (id TEXT, provider TEXT, content_hash TEXT, closed_at TEXT);
            CREATE TABLE job_match (job_id TEXT, content_hash TEXT, content_completeness TEXT);
            INSERT INTO job VALUES ('changed', 'example', 'new', NULL);
            INSERT INTO job VALUES ('current', 'example', 'same', NULL);
            INSERT INTO job VALUES ('closed', 'example', 'old', '2026-09-10');
            INSERT INTO job_match VALUES ('changed', 'old', 'FULL_CONTENT');
            INSERT INTO job_match VALUES ('current', 'same', 'FULL_CONTENT');
            INSERT INTO job_match VALUES ('current', 'same', 'FULL_CONTENT');
            INSERT INTO job_match VALUES ('closed', 'old', 'FULL_CONTENT');
        """)
        assert _scored_by_provider(conn) == {"example": 1}
        assert _full_content_by_provider(conn) == {"example": 1}


@pytest.mark.parametrize("full, expected", [(99_999, "<100%"), (100_000, "100.0%")])
def test_percentage_never_rounds_incomplete_inventory_to_complete(full: int, expected: str) -> None:
    from career_agent.sources.matrix import MatrixRow
    from career_agent.sources.matrix_doc import _row

    row = MatrixRow(
        source_id="example",
        name="Example",
        region="global",
        provider="example",
        state=MatrixState.PRODUCTION,
        permission="PERMITTED",
        maturity="BUILT",
        production_enabled=True,
        ever_run=True,
        last_success=None,
        postings=100_000,
        scored=full,
        full_content=full,
        blocker=None,
        next_action="Refresh on schedule.",
    )
    assert f"| {expected} |" in _row(row)


def _source(**kwargs: object) -> Source:
    """A catalogue row with only the fields the matrix reads."""
    fields: dict[str, object] = {
        "id": "example",
        "name": "Example",
        "region": "global",
        "status": SourceStatus.LIVE_INTEGRATION,
        "authorization": "a first-party page permits it",
        "provider": "example",
        "coverage": Coverage.OPERATIONAL,
    }
    fields.update(kwargs)
    return Source(**fields)  # type: ignore[arg-type]


# =========================================================================
# 1. THE NEXT ACTION, WHICH SOMEBODY IS GOING TO TYPE
# =========================================================================


def test_a_posting_with_no_description_is_not_waiting_for_a_rescore() -> None:
    """The defect this file was written for.

    Speedrun: 884 open postings, 726 with a stored score, and all 158 of the
    difference hold no text. A rescore had just run over everything. The column
    must not ask for another one.
    """
    action = _next_action(
        MatrixState.PRODUCTION,
        _source(id="a16z_speedrun"),
        postings=884,
        scored=726,
        textless=158,
    )
    assert "Rescore" not in action
    assert "no description" in action
    assert "158" in action


def test_a_board_family_with_no_boards_is_told_to_load_the_registry() -> None:
    """A family that has not been ASKED has not failed.

    `companies.yaml` is a file and `source_board` is the table `collect` reads;
    only `load-registry` carries the first into the second, and V1.7 lost 46
    promoted boards for a whole session to exactly that gap. Workday found it
    again on 2026-09-10: three boards in the YAML, none in the table, and the
    matrix said "run the collector and find out why it stored nothing" -- an
    instruction to debug a collector that works, about boards nobody handed it.

    `boards` arrives as None rather than as zero, because the count is a
    GROUP BY and a provider with no rows is absent from the result.
    """
    for boards in (None, 0):
        action = _next_action(
            MatrixState.NOT_STARTED,
            _source(provider="greenhouse", boards=boards),
        )
        assert "load-registry" in action, (boards, action)
        assert "why it stored nothing" not in action, (boards, action)


def test_a_board_family_that_has_boards_is_told_to_run_the_collector() -> None:
    """The complement, so the test above cannot pass by the branch swallowing
    every board family. A family WITH boards and no postings is a real "it ran
    and stored nothing", and that is a collector to read the output of."""
    action = _next_action(
        MatrixState.NOT_STARTED,
        _source(provider="greenhouse", boards=122),
    )
    assert "why it stored nothing" in action, action


def test_a_feed_with_nothing_collected_is_not_told_about_the_registry() -> None:
    """A feed has no board per employer, so `load-registry` is not its move and
    naming it would send somebody to a command that cannot help them."""
    action = _next_action(
        MatrixState.NOT_STARTED,
        _source(provider="himalayas", boards=None),
    )
    assert "load-registry" not in action, action


def test_a_genuine_backlog_still_asks_for_a_rescore() -> None:
    """The case the column exists for, which must survive the fix.

    A source that collected after the last rescore has postings in the database
    and in no list, because every discovery query is keyed on a configuration
    version. That is the single most common reason a screen here is inexplicably
    empty.
    """
    action = _next_action(MatrixState.PRODUCTION, _source(), postings=99, scored=0, textless=0)
    assert action.startswith("Rescore.")
    assert "99" in action


def test_a_mixed_backlog_counts_only_what_a_rescore_could_fix() -> None:
    """100 open, 80 scored, 5 textless: fifteen are waiting, not twenty."""
    action = _next_action(MatrixState.PRODUCTION, _source(), postings=100, scored=80, textless=5)
    assert action == "Rescore: 15 postings have no stored score yet."


def test_everything_scored_says_nothing_to_do() -> None:
    action = _next_action(MatrixState.PRODUCTION, _source(), postings=99, scored=99, textless=0)
    assert action == "Nothing. Keep collecting."


def test_more_textless_than_unscored_does_not_go_negative() -> None:
    """A textless posting can carry a score from an earlier configuration.

    So `textless` is not bounded by `postings - scored`, and subtracting it
    blindly produced a negative backlog in an early draft -- the same shape of
    arithmetic error `ingestion-report` made with UNACCOUNTED.
    """
    action = _next_action(MatrixState.PRODUCTION, _source(), postings=100, scored=100, textless=9)
    assert action == "Nothing. Keep collecting."


def test_a_forbidden_source_never_asks_anybody_to_look_into_it() -> None:
    """The column's own rule: whose move it is, in one sentence, never "investigate"."""
    action = _next_action(
        MatrixState.FORBIDDEN,
        _source(coverage=Coverage.BLOCKED, reason="robots.txt disallows everything"),
        postings=None,
        scored=None,
    )
    assert action.startswith("None. Do not fetch.")


# =========================================================================
# 2. THE ORDER OF `_state_for`
# =========================================================================


def test_a_source_with_postings_is_in_production_whoever_pressed_the_button() -> None:
    """Remote OK is the case, and it is the reason the order is what it is.

    Its robots file names our agent, so the first live call had to be the
    owner's. She made it, and 99 postings are in the corpus. Asking the
    owner-run rule first would report that as "waiting for you" and tell her to
    do again the thing she has just done.
    """
    assert "remoteok" in OWNER_RUN_ONLY, "the fixture below would prove nothing"
    state = _state_for(_source(id="remoteok"), postings=99, ever_run=True)
    assert state is MatrixState.PRODUCTION


def test_an_owner_run_source_with_nothing_collected_is_still_the_owner_s_move() -> None:
    state = _state_for(_source(id="remoteok"), postings=0, ever_run=False)
    assert state is MatrixState.READY_OWNER_RUN


@pytest.mark.parametrize("coverage", [Coverage.BLOCKED, Coverage.UNSUPPORTED])
def test_forbidden_outranks_everything_including_a_spent_quota(coverage: Coverage) -> None:
    """A quota is irrelevant to something we must not call at all."""
    state = _state_for(_source(id="workable", coverage=coverage), postings=0, ever_run=False)
    assert state is MatrixState.FORBIDDEN


def test_a_source_with_no_adapter_is_not_built_rather_than_empty() -> None:
    state = _state_for(_source(provider=None), postings=0, ever_run=False)
    assert state is MatrixState.PERMITTED_NOT_BUILT


def test_no_attempt_failure_and_verified_empty_are_distinct() -> None:
    assert _state_for(_source(), postings=0, ever_run=False) is MatrixState.NOT_STARTED
    assert _state_for(_source(), postings=0, ever_run=True, failed=True) is MatrixState.FAILED
    assert (
        _state_for(_source(), postings=0, ever_run=True, successful_empty=True)
        is MatrixState.PRODUCTION_EMPTY
    )


def test_access_blocker_is_not_a_permission_prohibition() -> None:
    source = _source(provider=None, collection_blocker="Security checkpoint")
    assert _state_for(source, postings=0, ever_run=False) is MatrixState.BLOCKED_PROVIDER


def test_marketplace_without_inventory_is_not_a_request_to_build() -> None:
    source = _source(provider=None, coverage=Coverage.NOTHING_PUBLISHED)
    assert _state_for(source, postings=0, ever_run=False) is MatrixState.NOTHING_PUBLISHED


def test_shared_run_does_not_prove_an_unattempted_family(tmp_path):
    from career_agent.domain.enums import PipelineRunStatus
    from career_agent.sources.matrix import _run_evidence
    from career_agent.storage.db import connect, migrate, transaction
    from career_agent.storage.repositories import PipelineRunRepo

    conn = connect(tmp_path / "scratch.db")
    migrate(conn)
    with transaction(conn):
        runs = PipelineRunRepo(conn)
        run = runs.start("collect")
        runs.finish(
            run,
            PipelineRunStatus.OK,
            stats={
                "by_provider": {
                    "example": {
                        "boards_attempted": 1,
                        "boards_succeeded": 1,
                        "postings_observed": 0,
                    },
                    "attribution": {"boards_not_this_runners": 90},
                }
            },
        )
    evidence = _run_evidence(conn)
    assert evidence["example"]["empty"] is True
    assert "attribution" not in evidence
    assert "unattempted" not in evidence
    conn.close()
