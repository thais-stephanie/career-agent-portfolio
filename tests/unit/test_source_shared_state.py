"""Home, the sidebar and Settings read one truth about a job site.

The postings are SHARED between the profiles of an install while the run
ledger is private. Two readings used the private ledger alone: a site another
profile had collected read "Never run" beside thousands of its postings, and a
run that stored postings and then ended in an error was recorded as a plain
failure for everybody, so a second profile was told the site "read nothing".
"""

from __future__ import annotations

import pytest

from career_agent.sources.catalogue import resolve
from career_agent.sources.health import SourceHealth
from career_agent.sources.public_health import FAILED, PARTIAL, outcome_of


def _source(source_id: str):
    return next(s for s in resolve() if s.id == source_id)


def test_a_failure_after_storing_postings_is_recorded_as_partial_for_everyone() -> None:
    outcome, reason = outcome_of(
        "FAILED", {"jobs_new": 71_777, "failures": ["transfer did not finish"]}
    )
    assert (outcome, reason) == (PARTIAL, "SOME_FAILED")


def test_a_failure_that_stored_nothing_is_still_a_failure() -> None:
    assert outcome_of("FAILED", {"jobs_new": 0}) == (FAILED, "ERROR")


@pytest.mark.parametrize("postings", [1, 124_000])
def test_a_site_with_postings_in_the_shared_corpus_was_run_by_somebody(postings: int) -> None:
    health = SourceHealth(source=_source("workable"), last_attempt=None, postings=postings)
    assert health.state == "HEALTHY"
    assert "Never collected" not in health.note


def test_a_site_nobody_ever_ran_is_not_run_yet() -> None:
    health = SourceHealth(source=_source("workable"), last_attempt=None, postings=0)
    assert health.state == "NOT_RUN"


def test_the_site_is_named_without_its_file_format() -> None:
    assert _source("workable").name == "Workable"
