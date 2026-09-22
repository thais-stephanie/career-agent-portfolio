"""Eligibility gates: three values, and never PASS by silence.

The single most expensive error this system could make is reading "Remote" as
"worldwide". Every test below is a variation on refusing to make it: a posting
that says nothing is UNRESOLVED, a barrier stated in a negated sentence records
nothing at all, and the only routes to PASS and FAIL are sentences quoted back
with offsets into the original.
"""

from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import EligibilityStatus, GateResult
from career_agent.match.gates import eligibility_status_from, evaluate_gates
from career_agent.match.lexicon import observe
from career_agent.match.text import split_sections

# The COMMITTED configuration, never `config/` itself.
#
# `config/` resolves `search.local.yaml` when one exists, so reaching for it
# here means this module tests the owner's private search on her machine and
# the shipped worked example on everybody else's -- two different suites
# wearing one name, and the second one is what a fresh clone runs.
# `committed_config_dir` is the copy with every local override removed.
CONFIG, _ = load_search_config(committed_config_dir())


def gates(title: str, description: str):
    sections = split_sections(
        description,
        CONFIG.prominence.primary_headings,
        CONFIG.prominence.secondary_headings,
    )
    observed = observe(CONFIG, title, description)
    return {g.gate: g for g in evaluate_gates(CONFIG, observed, title, description, sections)}


# --- silence ---------------------------------------------------------------


def test_a_posting_that_says_nothing_leaves_every_gate_unresolved() -> None:
    outcomes = gates("Automation Engineer", "We are hiring an automation engineer. Remote.")

    assert {g.result for g in outcomes.values()} == {GateResult.UNRESOLVED}
    assert outcomes["geography"].reason == "The posting does not state where it hires."


def test_the_word_remote_alone_does_not_open_the_geography_gate() -> None:
    """Remote is not worldwide. This is the error the whole module exists to
    prevent, so it gets its own test rather than living inside another one."""
    assert gates("Engineer", "Fully remote role.")["geography"].result is GateResult.UNRESOLVED


# --- blockers --------------------------------------------------------------


def test_an_explicit_us_residence_requirement_fails_geography() -> None:
    description = "Great team.\nCandidates must be located in the United States to apply.\n"
    outcome = gates("Business Systems Engineer", description)["geography"]

    assert outcome.result is GateResult.FAIL
    assert outcome.blocker_id == "us_residence_required"
    assert outcome.quote is not None and outcome.quote in description
    assert description[outcome.char_start : outcome.char_end].lower().startswith("must be located")


def test_a_negated_blocker_records_nothing_at_all() -> None:
    """ "We do not require a security clearance" contains the blocker phrase.
    It is neither a FAIL nor a PASS: a sentence saying a barrier is absent is
    not a sentence saying the candidate is admitted."""
    outcome = gates("Engineer", "We do not require a security clearance for this role.")[
        "clearance"
    ]

    assert outcome.result is GateResult.UNRESOLVED
    assert outcome.blocker_id is None


def test_travel_beyond_tolerance_fails_the_travel_gate() -> None:
    description = "You will travel up to 50% of the time to customer sites."
    outcome = gates("Forward Deployed Engineer", description)["travel"]

    assert outcome.result is GateResult.FAIL
    assert outcome.blocker_id == "travel_incompatible"
    assert outcome.quote in description


def test_a_relocation_requirement_fails_the_worksite_gate() -> None:
    outcome = gates("Engineer", "Relocation is required for this position.")["worksite"]

    assert outcome.result is GateResult.FAIL
    assert outcome.blocker_id == "onsite_incompatible"


# --- positive scope --------------------------------------------------------


def test_an_explicit_worldwide_scope_opens_the_geography_gate() -> None:
    description = "We hire globally and you can work from anywhere."
    outcome = gates("Automation Engineer", description)["geography"]

    assert outcome.result is GateResult.PASS
    assert outcome.quote in description


def test_a_brazil_scope_opens_the_geography_gate() -> None:
    outcome = gates("Automation Engineer", "This role is remote in Brazil.")["geography"]
    assert outcome.result is GateResult.PASS


def test_a_blocker_wins_over_a_positive_scope_phrase() -> None:
    """A posting that names Brazil somewhere and still says US-only is US-only."""
    description = "We have an office in Brazil. Candidates must be located in the United States."
    assert gates("Engineer", description)["geography"].result is GateResult.FAIL


# --- folding ---------------------------------------------------------------


def test_eligibility_is_verified_not_eligible_when_any_gate_fails() -> None:
    outcomes = gates("Engineer", "Candidates must be located in the United States.")
    assert (
        eligibility_status_from(tuple(outcomes.values())) is EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    )


def test_eligibility_is_unresolved_when_the_HIRING_SCOPE_is_silent() -> None:
    """Absence is never permission -- about the one question it is about.

    This test replaces an earlier one that required positive evidence on
    EVERY gate before saying eligible. That reading was a category error: a
    posting that never mentions security clearance is not "unknown on
    clearance", and demanding proof of a negative made VERIFIED_ELIGIBLE
    unreachable for real postings, which is not caution -- it is a status
    column that says the same thing about every job.
    """
    outcomes = gates("Engineer", "You will build integrations and own our CRM.")
    assert outcomes["geography"].result is GateResult.UNRESOLVED
    assert eligibility_status_from(tuple(outcomes.values())) is EligibilityStatus.UNRESOLVED


def test_a_stated_hiring_scope_is_enough_when_nothing_disqualifies() -> None:
    """The exclusionary gates are silent, and silence there is not a doubt.

    Clearance, mandatory citizenship, full-time onsite attendance and heavy
    travel are conditions employers STATE when they exist, because they are
    the conditions that disqualify applicants. Their absence is the absence of
    a disqualification.
    """
    outcomes = gates("Engineer", "We hire globally and work from anywhere.")
    assert outcomes["geography"].result is GateResult.PASS
    assert {o.result for k, o in outcomes.items() if k != "geography"} == {GateResult.UNRESOLVED}
    assert eligibility_status_from(tuple(outcomes.values())) is EligibilityStatus.VERIFIED_ELIGIBLE


def test_a_blocker_still_overrides_a_stated_scope() -> None:
    """Relaxing the exclusionary gates did not relax the blockers."""
    outcomes = gates(
        "Engineer",
        "We hire globally and work from anywhere. An active security clearance is required.",
    )
    assert outcomes["geography"].result is GateResult.PASS
    assert outcomes["clearance"].result is GateResult.FAIL
    assert (
        eligibility_status_from(tuple(outcomes.values())) is EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    )


def test_likely_eligible_is_never_emitted_by_the_deterministic_matcher() -> None:
    """It is reserved for provider-corroborated evidence, which this matcher
    does not read. Emitting it from text alone would claim a source exists."""
    for description in ("", "We hire globally.", "Must be located in the United States."):
        outcomes = gates("Engineer", description)
        assert eligibility_status_from(tuple(outcomes.values())) is not (
            EligibilityStatus.LIKELY_ELIGIBLE
        )
