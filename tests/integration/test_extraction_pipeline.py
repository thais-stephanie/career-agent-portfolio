"""What happens between a model answering and a fingerprint existing.

Every test here drives the real `extract_job` with a `FakeClient` whose answers
the test writes, which is the only way to exercise the failure modes that
matter: a hallucinated tool, an unquotable hard requirement, a salary range the
wrong way round, an answer that arrives as prose.

The direction of every correction is the thing under test. This layer is
allowed to say "we do not know"; it is never allowed to guess in the other
direction and call the guess a finding.
"""

import json
from typing import Any

from career_agent.domain.enums import (
    ExtractionStatus,
    LanguageRequirement,
    SoftwareCentrality,
)
from career_agent.llm.client import (
    FakeClient,
    Family,
    LLMRequest,
    LLMResponse,
    ModelConfig,
    Runner,
)
from career_agent.llm.transport import ALL_DIMENSIONS
from career_agent.pipeline.extract import JobSource, extract_job

CONFIG = ModelConfig(vendor="fake", identifier="fake-model")

POSTING = (
    "Revenue Systems Analyst\n\n"
    "You will own our HubSpot instance and the reporting around it.\n"
    "This role is fully remote.\n"
    "German is a plus.\n"
)


def source(**changes: Any) -> JobSource:
    defaults: dict[str, Any] = {
        "job_id": "job-1",
        "content_hash": "sha256:aaa",
        "description_text": POSTING,
        "provider": "greenhouse",
        "payload": {"location": {"name": "Remote - United States"}},
        "payload_hash": "sha256:ppp",
        "observations": (),
        "field_map_digest": "sha256:map",
    }
    defaults.update(changes)
    return JobSource(**defaults)


def description(**changes: Any) -> str:
    """A complete, minimally valid description-family answer.

    Complete because a forgotten dimension is a different failure with its own
    test; these tests are about what happens to answers that *are* complete.
    """
    observations = [
        {"dimension": name, "status": "NOT_STATED", "value": None, "confidence": 0.0}
        for name in ALL_DIMENSIONS
    ]
    payload: dict[str, Any] = {
        "observed_title": "Revenue Systems Analyst",
        "function_signals": ["revenue_operations"],
        "observations": observations,
        "responsibilities": [
            {
                "category": "reporting_and_dashboards",
                "prominence": "PRIMARY",
                "confidence": 0.9,
                "evidence_id": "ev_01",
            }
        ],
        "software": [],
        "languages": [],
        "evidence": [
            {
                "id": "ev_01",
                "source_kind": "JOB_DESCRIPTION",
                "quote": "You will own our HubSpot instance and the reporting around it.",
            }
        ],
    }
    payload.update(changes)
    return json.dumps(payload)


def observe(payload: str, dimension: str, **row: Any) -> str:
    """Replace one dimension's row in an already-built answer."""
    parsed = json.loads(payload)
    parsed["observations"] = [o for o in parsed["observations"] if o["dimension"] != dimension]
    parsed["observations"].append({"dimension": dimension, **row})
    return json.dumps(parsed)


def cite(payload: str, **evidence: Any) -> str:
    parsed = json.loads(payload)
    parsed["evidence"].append(evidence)
    return json.dumps(parsed)


def answering(*by_family: str) -> FakeClient:
    """A client that returns the given text for the description family.

    The provider family is never reached in these tests: the sources carry no
    observations, so `build_provider_request` returns None and the call is not
    made at all.
    """
    answers = list(by_family)

    def responder(request: LLMRequest, _: ModelConfig) -> LLMResponse:
        if request.family is Family.DESCRIPTION:
            return LLMResponse(raw_text=answers.pop(0), model="fake-model")
        return LLMResponse(raw_text=json.dumps({"observations": [], "evidence": []}))

    return FakeClient(responder=responder)


# --- the happy path ---------------------------------------------------------


def test_a_clean_answer_becomes_a_fingerprint() -> None:
    outcome = extract_job(answering(description()), CONFIG, source())

    assert outcome.ok, outcome.failure
    assert outcome.fingerprint is not None
    assert outcome.fingerprint.role.observed_title == "Revenue Systems Analyst"
    assert outcome.verification.rate == 1.0


def test_an_empty_ats_record_costs_no_provider_call() -> None:
    """The zero-call rule, from the top of the pipeline.

    Nothing in the record needs interpreting, so the provider family is not
    asked. One call, not two -- which on a corpus-wide backfill is the
    difference the topology decision was made on.
    """
    client = answering(description())
    extract_job(client, CONFIG, source(observations=()))

    assert [family for family, _ in client.calls] == [Family.DESCRIPTION]


def test_the_runner_travels_onto_every_attempt() -> None:
    """Recorded, never branched on. It is how a cost report separates a paid
    call from a free one after the fact."""
    outcome = extract_job(answering(description()), CONFIG, source())

    assert {a.runner for a in outcome.attempts} == {Runner.FAKE}
    assert outcome.paid_calls == 0


# --- corrections: every one moves towards uncertainty -----------------------


def test_a_tool_the_posting_never_names_is_dropped() -> None:
    """A tool that does not appear in the text cannot have been observed in it."""
    payload = description(
        software=[
            {"raw_mention": "HubSpot", "centrality": "CORE", "confidence": 0.9},
            {"raw_mention": "Salesforce", "centrality": "CORE", "confidence": 0.9},
        ]
    )

    outcome = extract_job(answering(payload), CONFIG, source())

    assert outcome.fingerprint is not None
    assert [t.raw_mention for t in outcome.fingerprint.software] == ["HubSpot"]
    assert outcome.validation is not None
    assert any("does not appear" in c.reason for c in outcome.validation.corrections)


def test_too_many_core_tools_are_demoted_rather_than_discarded() -> None:
    """The mentions are real; the emphasis was not.

    Dropping them would lose tools the posting genuinely names, which is a
    larger error than overstating how central they are.
    """
    tools = " ".join(f"Tool{n}" for n in range(7))
    payload = description(
        software=[
            {"raw_mention": f"Tool{n}", "centrality": "CORE", "confidence": 0.9} for n in range(7)
        ]
    )

    outcome = extract_job(answering(payload), CONFIG, source(description_text=POSTING + tools))

    assert outcome.fingerprint is not None
    assert len(outcome.fingerprint.software) == 7
    assert all(t.centrality is SoftwareCentrality.REQUIRED for t in outcome.fingerprint.software)


# The hiring-scope contract has its own file: work model versus hiring
# geography is a large enough idea, with enough required cases, that burying it
# among the other corrections would make it hard to find and easy to weaken.
# See tests/integration/test_hiring_scope_pipeline.py.


def test_an_unquotable_hard_language_requirement_becomes_unclear() -> None:
    """HARD_REQUIREMENT is a gate that can end a candidacy.

    Letting one rest on inference would turn "German is a plus" into a wall.
    UNCLEAR penalises nothing and asks a question.
    """
    payload = description(
        languages=[{"language_code": "de", "requirement_level": "HARD_REQUIREMENT"}]
    )

    outcome = extract_job(answering(payload), CONFIG, source())

    assert outcome.fingerprint is not None
    assert outcome.fingerprint.languages[0].requirement_level is LanguageRequirement.UNCLEAR


def test_an_impossible_salary_range_is_cleared_rather_than_swapped() -> None:
    """Swapping assumes the model transposed two correct numbers.

    It may equally have read the wrong sentence entirely, and downstream salary
    arithmetic must never rest on a guess about which failure occurred.
    """
    payload = observe(
        description(), "compensation.min", status="INFERRED", value="200000", confidence=0.5
    )
    payload = observe(payload, "compensation.max", status="INFERRED", value="90000", confidence=0.5)

    outcome = extract_job(answering(payload), CONFIG, source())

    assert outcome.fingerprint is not None
    comp = outcome.fingerprint.compensation
    assert comp.min.status is ExtractionStatus.NOT_STATED
    assert comp.max.status is ExtractionStatus.NOT_STATED
    assert comp.min.value is None and comp.max.value is None


# --- failures -------------------------------------------------------------


def test_a_posting_that_describes_no_work_fails_rather_than_storing_nothing() -> None:
    """Every real posting describes work.

    An empty responsibility list means the extraction did not read the body --
    the one thing this milestone exists to do -- so it fails and stays
    countable rather than being stored as an analysis that says nothing.
    """
    outcome = extract_job(answering(description(responsibilities=[])), CONFIG, source())

    assert not outcome.ok
    assert outcome.fingerprint is None
    assert "responsibilities is empty" in (outcome.failure or "")


def test_an_unverifiable_quote_is_recorded_rather_than_removed() -> None:
    """The claim stays visible and marked.

    "The model cited a sentence that does not exist" is exactly the measurement
    the evaluation plan is built around, and deleting the citation would delete
    the measurement.
    """
    payload = description(
        evidence=[
            {
                "id": "ev_01",
                "source_kind": "JOB_DESCRIPTION",
                "quote": "You will report to the Chief Revenue Officer.",
            }
        ]
    )

    outcome = extract_job(answering(payload), CONFIG, source())

    assert outcome.ok, outcome.failure
    assert outcome.unverified_evidence == ("ev_d01",)
    assert outcome.verification.rate == 0.0


def test_a_failed_attempt_is_retried_and_both_attempts_are_recorded() -> None:
    """The second attempt succeeds and the first is not forgotten.

    A table that only held successes could not tell a prompt problem from a
    model problem months later.
    """
    outcome = extract_job(answering("Sure! Here you go: {oops", description()), CONFIG, source())

    assert outcome.ok, outcome.failure
    assert [a.attempt for a in outcome.attempts] == [1, 2]
    assert [a.parsed_ok for a in outcome.attempts] == [False, True]
    assert outcome.attempts[0].raw_output == "Sure! Here you go: {oops"


def test_an_incomplete_answer_is_retried_rather_than_crashing() -> None:
    """Every transport field has a default, so an answer to nothing validates.

    Completeness is checked where a retry can still fix it. Discovering it at
    assembly instead would end the batch on one bad posting, which under a paid
    runner means re-paying for everything that already succeeded.
    """
    outcome = extract_job(
        answering(json.dumps({"observed_title": "nothing else"}), description()),
        CONFIG,
        source(),
    )

    assert outcome.ok, outcome.failure
    assert [a.validated_ok for a in outcome.attempts] == [False, True]
    assert "omitted" in (outcome.attempts[0].error or "")


def test_giving_up_leaves_the_failure_and_every_attempt_behind() -> None:
    outcome = extract_job(answering("not json", "still not json"), CONFIG, source())

    assert not outcome.ok
    assert len(outcome.attempts) == 2
    assert "description" in (outcome.failure or "")
