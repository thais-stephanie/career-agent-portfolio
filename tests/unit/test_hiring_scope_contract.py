"""Work model is not hiring geography. This file is that contract.

The distinction is one of the reasons Career Agent exists:

    WORK MODEL     remote / hybrid / onsite
    HIRING SCOPE   worldwide / region / country list / not stated

A posting can be REMOTE and US-only, REMOTE and EMEA-only, REMOTE and
Brazil-only, or REMOTE with no hiring geography stated at all -- which is the
most common shape of the four. Remote work is never geographic permission.

The guard below is a **safety check against one known-invalid evidence
pattern**, not a geography parser. It refuses to let a citation that describes
only the working arrangement support an EXPLICIT WORLDWIDE hiring scope.
Deciding what a genuinely geographic sentence actually means stays with the
extractor, judged against the golden set -- and the tests here say so by
asserting the guard stays *out* of the way of everything else.
"""

import pytest

from career_agent.domain.validate import evidence_is_work_arrangement_only

# --- what cannot support a hiring-scope claim -------------------------------

WORK_ARRANGEMENT_ONLY = [
    "Remote",
    "remote",
    "This role is remote.",
    "This role is fully remote.",
    "This position is fully remote",
    "This is a fully remote role.",
    "The role is 100% remote.",
    "Remote-first.",
    "Remote first",
    "Work from home.",
    "Work from anywhere.",
    "Distributed team.",
    "We are a fully distributed team",
    "remote position",
    "  Fully Remote  ",
]


@pytest.mark.parametrize("quote", WORK_ARRANGEMENT_ONLY)
def test_a_work_arrangement_sentence_cannot_support_a_hiring_scope(quote: str) -> None:
    """Every one of these proves REMOTE and proves nothing about hiring.

    They must behave *equivalently*. The defect this replaces caught the bare
    token `"Remote"` and let `"This role is fully remote."` through -- which is
    the shape a model actually cites, so the guard was catching a synthetic
    case while every real one walked past it.
    """
    assert evidence_is_work_arrangement_only(quote) is True


def test_a_missing_citation_supports_nothing_at_all() -> None:
    """A worldwide scope with nothing behind it has less support, not more."""
    assert evidence_is_work_arrangement_only(None) is True


# --- what the guard must stay out of the way of -----------------------------

CARRIES_MORE_THAN_ARRANGEMENT = [
    "This role is open to candidates anywhere in the world.",
    "This is a fully remote role open to candidates worldwide.",
    "We hire globally.",
    "Remote, and we hire anywhere in the world.",
    "This is a fully remote role available only to candidates in the United States.",
    "Remote within EMEA only.",
    "Remote - Brazil",
    "We are hiring Brazilian applicants.",
    "Candidates must reside in Brazil.",
    "Open to candidates in Brazil, Argentina, and Colombia.",
    "Open to candidates across LATAM.",
    "Worldwide except Brazil.",
]


@pytest.mark.parametrize("quote", CARRIES_MORE_THAN_ARRANGEMENT)
def test_a_citation_saying_more_than_the_arrangement_is_left_alone(quote: str) -> None:
    """False here does not mean "this is a valid worldwide claim".

    It means the guard has nothing to say. Whether the sentence supports the
    scope the extractor assigned is a semantic question, answered by the prompt
    and measured by the golden set -- putting that judgement in code would move
    semantics somewhere it cannot be evaluated.
    """
    assert evidence_is_work_arrangement_only(quote) is False


def test_the_guard_is_not_a_country_detector() -> None:
    """It never asks "does this name a place?".

    A sentence with no geography and no work-arrangement phrase is simply not
    this guard's business -- and if code started deciding which sentences count
    as geographic, the deterministic layer would have quietly become the
    extractor.
    """
    assert evidence_is_work_arrangement_only("You will report to the CRO.") is False
