"""Where to start, and the properties that make the numbers trustworthy.

The queue's whole job is to turn "309 statements are waiting" into a first
thing to do. Two properties keep it honest and both are asserted here:

  PARTITION   every claim is in exactly one step, so the step counts sum to
              the package and no denominator was invented.
  NAVIGATION  a step decides WHERE a claim is met and never what it means. No
              step confirms, ranks credibility or hides anything.

Every claim here is synthetic.
"""

from __future__ import annotations

from career_agent.intake.models import ReviewState
from career_agent.intake.priority import (
    ANYTHING_ELSE,
    EARLIER_WORK,
    ESSENTIAL,
    MEASURABLE_OUTCOMES,
    NAME_THE_EMPLOYER,
    ORDER,
    RECENT_WORK,
    SETTLE_DISAGREEMENTS,
    SKILLS_AND_TOOLS,
    STUDY_AND_CERTIFICATES,
    Claim,
    assign,
    focus_matches,
    most_recent_employer,
    plan,
)

NONE: frozenset[str] = frozenset()


def claim(
    key: str,
    claim_type: str = "EMPLOYMENT",
    *,
    state: str = ReviewState.UNREVIEWED,
    conflict: str | None = None,
    employer: str | None = "Acme",
    start: str | None = "2020-01",
    metrics: bool = False,
) -> Claim:
    return Claim(
        claim_key=key,
        claim_type=claim_type,
        review_state=state,
        conflict_group=conflict,
        employer=employer,
        period_start=start,
        has_metrics=metrics,
    )


# =========================================================================
# the partition
# =========================================================================


def test_every_claim_lands_in_exactly_one_step() -> None:
    claims = [
        claim("a", conflict="EMPLOYMENT:acme"),
        claim("b", employer=None),
        claim("c", employer="Newest", start="2024-01"),
        claim("d", metrics=True),
        claim("e"),
        claim("f", "SKILL", employer=None, start=None),
        claim("g", "EDUCATION", employer=None, start=None),
        claim("h", "METRIC", employer=None, start=None),
    ]
    assigned = assign(claims, unresolved=frozenset({"EMPLOYMENT:acme"}))
    assert set(assigned) == {c.claim_key for c in claims}
    assert set(assigned.values()) <= set(ORDER)


def test_the_step_totals_sum_to_the_package() -> None:
    """The property the counts rest on. Break it and a step saying "8 left"
    can sit above a package saying "6 left", with nothing to say which lies."""
    claims = [claim(f"c{n}", metrics=n % 3 == 0) for n in range(20)]
    claims += [claim(f"s{n}", "SKILL", employer=None, start=None) for n in range(7)]
    claims += [claim(f"e{n}", "EDUCATION", employer=None, start=None) for n in range(3)]
    result = plan(claims, unresolved=NONE)
    assert sum(step["total"] for step in result["steps"]) == len(claims)
    assert result["progress"]["total"] == len(claims)
    assert sum(step["waiting"] for step in result["steps"]) == result["progress"]["waiting"]


def test_no_step_is_ever_omitted_even_when_empty() -> None:
    """A step that vanished when it emptied would make "you answered
    everything here" and "this kind does not exist in your package" look
    identical. They are opposite facts."""
    result = plan([claim("only", "SKILL", employer=None, start=None)], unresolved=NONE)
    assert [step["key"] for step in result["steps"]] == list(ORDER)
    assert result["steps"][0]["total"] == 0


# =========================================================================
# the order, and why each rule is there
# =========================================================================


def test_an_unsettled_disagreement_comes_first_because_it_blocks() -> None:
    """Not an opinion about importance. `store.confirm` refuses a claim in an
    unresolved group, so these really do stop the others."""
    disputed = claim("a", conflict="EMPLOYMENT:acme", employer="Newest", start="2030-01")
    assigned = assign([disputed], unresolved=frozenset({"EMPLOYMENT:acme"}))
    assert assigned["a"] == SETTLE_DISAGREEMENTS
    assert plan([disputed], unresolved=frozenset({"EMPLOYMENT:acme"}))["steps"][0]["blocking"]


def test_settling_a_disagreement_returns_its_claims_to_the_ordinary_queue() -> None:
    """Which is exactly what settling one is for. The claims do not disappear
    and they are not confirmed; they rejoin the queue carrying the period."""
    disputed = claim("a", conflict="EMPLOYMENT:acme")
    assert assign([disputed], unresolved=frozenset({"EMPLOYMENT:acme"}))["a"] == (
        SETTLE_DISAGREEMENTS
    )
    assert assign([disputed], unresolved=NONE)["a"] == RECENT_WORK


def test_work_with_no_employer_is_asked_about_before_anything_else() -> None:
    """26 of the owner's 243 employment claims name no employer. A statement
    nobody can attribute cannot evidence a requirement, so it is the first
    thing worth her attention after the disagreements."""
    assigned = assign([claim("a", employer=None)], unresolved=NONE)
    assert assigned["a"] == NAME_THE_EMPLOYER


def test_the_most_recent_job_is_offered_before_the_earlier_ones() -> None:
    claims = [
        claim("old", employer="First", start="2015-01"),
        claim("new", employer="Latest", start="2024-06"),
        claim("mid", employer="Middle", start="2019-03"),
    ]
    assigned = assign(claims, unresolved=NONE)
    assert assigned["new"] == RECENT_WORK
    assert assigned["old"] == EARLIER_WORK
    assert assigned["mid"] == EARLIER_WORK


def test_the_most_recent_employer_is_stable_between_reads() -> None:
    """A queue that reshuffled itself between visits is one nobody finishes."""
    tied = [
        claim("a", employer="Beta", start="2024-01"),
        claim("b", employer="Alpha", start="2024-01"),
    ]
    assert most_recent_employer(tied) == most_recent_employer(list(reversed(tied)))


def test_an_undatable_job_is_never_the_most_recent_one() -> None:
    claims = [claim("a", employer="NoDates", start=None), claim("b", employer="D", start="2001-01")]
    assert most_recent_employer(claims) == "D"


def test_a_measured_outcome_is_met_before_the_rest_of_the_older_work() -> None:
    claims = [
        claim("new", employer="Latest", start="2024-01"),
        claim("metric", employer="Old", start="2010-01", metrics=True),
        claim("plain", employer="Old", start="2010-01"),
    ]
    assigned = assign(claims, unresolved=NONE)
    assert assigned["metric"] == MEASURABLE_OUTCOMES
    assert assigned["plain"] == EARLIER_WORK


def test_skills_and_study_have_their_own_steps() -> None:
    claims = [
        claim("s", "SKILL", employer=None, start=None),
        claim("t", "TOOL", employer=None, start=None),
        claim("e", "EDUCATION", employer=None, start=None),
        claim("c", "CERTIFICATION", employer=None, start=None),
    ]
    assigned = assign(claims, unresolved=NONE)
    assert assigned["s"] == assigned["t"] == SKILLS_AND_TOOLS
    assert assigned["e"] == assigned["c"] == STUDY_AND_CERTIFICATES


def test_an_unknown_claim_type_falls_through_rather_than_disappearing() -> None:
    """The catch-all is what makes the partition total. A type nobody
    anticipated must be somewhere she can reach, not nowhere."""
    assigned = assign([claim("x", "METRIC", employer=None, start=None)], unresolved=NONE)
    assert assigned["x"] == ANYTHING_ELSE


# =========================================================================
# what "enough to start" means
# =========================================================================


def test_the_essential_set_is_the_first_three_steps_and_says_so() -> None:
    claims = [
        claim("a", conflict="g"),
        claim("b", employer=None),
        claim("c", employer="Latest", start="2024-01"),
        claim("d", employer="Old", start="2001-01"),
        claim("e", "SKILL", employer=None, start=None),
    ]
    result = plan(claims, unresolved=frozenset({"g"}))
    assert result["essential"]["steps"] == list(ESSENTIAL)
    assert result["essential"]["total"] == 3
    assert result["essential"]["waiting"] == 3


def test_progress_counts_answers_and_never_invents_a_target() -> None:
    """A deterministic denominator, and nothing shaped like a level or a
    completeness percentage anywhere in the payload."""
    claims = [
        claim("a", state=ReviewState.CONFIRMED),
        claim("b", state=ReviewState.REJECTED),
        claim("c", state=ReviewState.UNREVIEWED),
        claim("d", state=ReviewState.UNRESOLVED),
    ]
    result = plan(claims, unresolved=NONE)
    assert result["progress"] == {"total": 4, "waiting": 2, "answered": 2}

    flat = repr(result).casefold()
    for invented in ("percent", "score", "level", "complete", "badge", "streak"):
        assert invented not in flat, f"the queue invented a {invented}"


def test_not_sure_yet_counts_as_answered_and_unreviewed_does_not() -> None:
    """ "I do not know" is an answer, and one that should stop a row being
    presented as untouched. `ReviewState.ANSWERABLE` is the same distinction
    the review itself draws, read here rather than restated."""
    assert plan([claim("a", state=ReviewState.UNRESOLVED)], unresolved=NONE)["progress"] == {
        "total": 1,
        "waiting": 1,
        "answered": 0,
    }
    assert plan([claim("a", state=ReviewState.CORRECTED_BY_USER)], unresolved=NONE)["progress"] == {
        "total": 1,
        "waiting": 0,
        "answered": 1,
    }


# =========================================================================
# the job she came from
# =========================================================================


def test_the_focus_lens_matches_on_the_words_and_nothing_cleverer() -> None:
    """A fuzzy match would quietly decide that a requirement and a sentence
    are about the same thing, which is a judgement about her career."""
    claims = [claim("a"), claim("b"), claim("c")]
    texts = {
        "a": "Built integrations with Workato",
        "b": "Ran the Salesforce migration",
        "c": "workato administration",
    }
    assert focus_matches(claims, texts, "Workato") == ("a", "c")
    assert focus_matches(claims, texts, "  ") == ()
    assert focus_matches(claims, texts, "Netsuite") == ()


def test_the_focus_lens_steals_no_claim_from_its_step() -> None:
    """It is a lens, not a step. A claim that matches a requirement is still
    met where it belongs, or the step counts stop summing."""
    claims = [claim("a", employer="Latest", start="2024-01")]
    before = assign(claims, unresolved=NONE)
    focus_matches(claims, {"a": "Workato"}, "Workato")
    assert assign(claims, unresolved=NONE) == before
