"""The remote-versus-hiring-geography contract, end to end through the pipeline.

`test_hiring_scope_contract.py` tests the guard in isolation. This tests what a
posting actually becomes: a fingerprint with a work model and a hiring scope
that are separately true, separately evidenced, and never inferred from each
other.

The invariant underneath all of it:

    a country restriction is a FACT, not a verdict

`COUNTRY_LIST:BR` is neutral. For an applicant in Brazil it may later be a
strong positive eligibility signal; for an applicant elsewhere the same
fingerprint may block or stay unresolved. **The fingerprint is identical for
both**, because M2 is candidate-independent and M3 owns the comparison.
"""

import json
from typing import Any

import pytest

from career_agent.domain.enums import ExtractionStatus, HiringScopeKind, Region, WorkModel
from career_agent.llm.client import FakeClient, Family, LLMRequest, LLMResponse, ModelConfig
from career_agent.llm.transport import ALL_DIMENSIONS
from career_agent.pipeline.extract import JobSource, extract_job

CONFIG = ModelConfig(vendor="fake", identifier="fake-model")


def answer(sentence: str, *, work_model: str | None, scope: str | None) -> str:
    """What a model might return for a posting containing `sentence`.

    Both dimensions cite the same sentence when both are claimed, which is the
    realistic case and the interesting one: it is precisely when one sentence
    is offered as proof of two different things that the guard has to decide
    what it can actually support.
    """
    rows = [
        {"dimension": name, "status": "NOT_STATED", "value": None, "confidence": 0.0}
        for name in ALL_DIMENSIONS
        if name not in {"hiring_scope", "work_environment.work_model"}
    ]
    evidence = [
        {
            "id": "ev_01",
            "source_kind": "JOB_DESCRIPTION",
            "quote": "You will own our reporting.",
        }
    ]

    if work_model is None:
        rows.append(
            {
                "dimension": "work_environment.work_model",
                "status": "NOT_STATED",
                "value": None,
                "confidence": 0.0,
            }
        )
    else:
        rows.append(
            {
                "dimension": "work_environment.work_model",
                "status": "EXPLICIT",
                "value": work_model,
                "confidence": 0.95,
                "evidence_id": "ev_02",
            }
        )
        evidence.append({"id": "ev_02", "source_kind": "JOB_DESCRIPTION", "quote": sentence})

    if scope is None:
        rows.append(
            {"dimension": "hiring_scope", "status": "NOT_STATED", "value": None, "confidence": 0.0}
        )
    else:
        rows.append(
            {
                "dimension": "hiring_scope",
                "status": "EXPLICIT",
                "value": scope,
                "confidence": 0.9,
                "evidence_id": "ev_03",
            }
        )
        evidence.append({"id": "ev_03", "source_kind": "JOB_DESCRIPTION", "quote": sentence})

    return json.dumps(
        {
            "observed_title": "Revenue Systems Analyst",
            "observations": rows,
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
            "evidence": evidence,
        }
    )


def run(sentence: str, *, work_model: str | None, scope: str | None) -> Any:
    posting = f"Revenue Systems Analyst\n\nYou will own our reporting.\n{sentence}\n"
    payload = answer(sentence, work_model=work_model, scope=scope)

    def responder(request: LLMRequest, _: ModelConfig) -> LLMResponse:
        if request.family is Family.DESCRIPTION:
            return LLMResponse(raw_text=payload, model="fake-model")
        return LLMResponse(raw_text=json.dumps({"observations": [], "evidence": []}))

    outcome = extract_job(
        FakeClient(responder=responder),
        CONFIG,
        JobSource(
            job_id="job-1",
            content_hash="sha256:aaa",
            description_text=posting,
            provider="greenhouse",
        ),
    )
    assert outcome.ok, outcome.failure
    assert outcome.fingerprint is not None
    return outcome


# --- remote alone is never geographic permission ----------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "This role is fully remote.",
        "This role is remote.",
        "Remote-first.",
        "Work from anywhere.",
        "We are a fully distributed team.",
    ],
)
def test_remote_only_can_never_produce_a_verified_worldwide_scope(sentence: str) -> None:
    """The model claims WORLDWIDE on a work-arrangement sentence. It does not get it.

    Golden case 5 is this shape. The work model survives, because the sentence
    really does prove it; the scope goes to NOT_STATED, because the posting
    never said where it hires.
    """
    outcome = run(sentence, work_model="REMOTE", scope="WORLDWIDE")
    fingerprint = outcome.fingerprint

    assert fingerprint.work_environment.work_model.value is WorkModel.REMOTE
    assert fingerprint.work_environment.work_model.status is ExtractionStatus.EXPLICIT

    scope = fingerprint.eligibility.hiring_scope
    assert scope.status is ExtractionStatus.NOT_STATED
    assert scope.value is None
    assert scope.evidence_id is None
    assert "withdrawn" in scope.reasoning


def test_the_withdrawn_claim_stays_in_the_evidence_archive() -> None:
    """The quote is not deleted, only uncited.

    Evidence is a record of what the model claimed, and the moment a claim is
    withdrawn is exactly when that record is most worth keeping. Nothing points
    at it any more, which is what an unused evidence row means.
    """
    outcome = run("This role is fully remote.", work_model="REMOTE", scope="WORLDWIDE")

    quotes = [e.quote for e in outcome.fingerprint.evidence]
    assert "This role is fully remote." in quotes


def test_a_model_that_answers_correctly_is_left_alone() -> None:
    """No correction fires when the model already said NOT_STATED."""
    outcome = run("This role is fully remote.", work_model="REMOTE", scope=None)

    assert outcome.validation is not None
    assert outcome.validation.corrections == []
    assert outcome.fingerprint.eligibility.hiring_scope.status is ExtractionStatus.NOT_STATED


# --- what a stated geography produces ---------------------------------------


def test_explicit_worldwide_survives() -> None:
    outcome = run(
        "This role is open to candidates anywhere in the world.",
        work_model=None,
        scope="WORLDWIDE",
    )
    scope = outcome.fingerprint.eligibility.hiring_scope

    assert scope.status is ExtractionStatus.EXPLICIT
    assert scope.value.kind is HiringScopeKind.WORLDWIDE
    assert scope.value.exclusions == []


def test_remote_plus_explicit_worldwide_keeps_both() -> None:
    outcome = run(
        "This is a fully remote role open to candidates worldwide.",
        work_model="REMOTE",
        scope="WORLDWIDE",
    )
    fingerprint = outcome.fingerprint

    assert fingerprint.work_environment.work_model.value is WorkModel.REMOTE
    assert fingerprint.eligibility.hiring_scope.value.kind is HiringScopeKind.WORLDWIDE


def test_remote_plus_a_country_restriction_is_not_worldwide() -> None:
    outcome = run(
        "This is a fully remote role available only to candidates in the United States.",
        work_model="REMOTE",
        scope="COUNTRY_LIST:US",
    )
    fingerprint = outcome.fingerprint

    assert fingerprint.work_environment.work_model.value is WorkModel.REMOTE
    scope = fingerprint.eligibility.hiring_scope.value
    assert scope.kind is HiringScopeKind.COUNTRY_LIST
    assert scope.countries == ["US"]


def test_remote_plus_a_regional_restriction_is_not_worldwide() -> None:
    outcome = run("Remote within EMEA only.", work_model="REMOTE", scope="REGION:EMEA")
    scope = outcome.fingerprint.eligibility.hiring_scope.value

    assert scope.kind is HiringScopeKind.REGION
    assert scope.regions == [Region.EMEA]


def test_worldwide_with_exclusions_is_never_downgraded() -> None:
    """The approved case that runs backwards from intuition.

    "Open worldwide, except where we cannot legally employ" is the usual shape
    of a genuinely global posting -- exactly the jobs this product exists to
    find. It is also materially different from a bare WORLDWIDE, and the two
    must not collapse into each other.
    """
    outcome = run(
        "Open worldwide, except Cuba and Iran.", work_model=None, scope="WORLDWIDE:!CU,IR"
    )
    scope = outcome.fingerprint.eligibility.hiring_scope.value

    assert scope.kind is HiringScopeKind.WORLDWIDE
    assert set(scope.exclusions) == {"CU", "IR"}
    assert outcome.validation.corrections == []


# --- hiring geography that happens to be the candidate's own country --------
#
# These are the cases that show M2 staying candidate-independent. Nothing in
# this pipeline knows where the candidate lives, and a Brazilian scope is
# recorded exactly like any other.


def test_a_country_targeted_remote_role_keeps_both_facts() -> None:
    outcome = run("Remote - Brazil", work_model="REMOTE", scope="COUNTRY_LIST:BR")
    fingerprint = outcome.fingerprint

    assert fingerprint.work_environment.work_model.value is WorkModel.REMOTE
    assert fingerprint.eligibility.hiring_scope.value.countries == ["BR"]


@pytest.mark.parametrize(
    ("sentence", "scope", "expected"),
    [
        ("We are hiring Brazilian applicants.", "COUNTRY_LIST:BR", ["BR"]),
        ("Candidates must reside in Brazil.", "COUNTRY_LIST:BR", ["BR"]),
        (
            "Open to candidates in Brazil, Argentina, and Colombia.",
            "COUNTRY_LIST:BR,AR,CO",
            ["BR", "AR", "CO"],
        ),
    ],
)
def test_hiring_geography_stated_in_prose_is_recorded(
    sentence: str, scope: str, expected: list[str]
) -> None:
    """The country need not appear in a location field.

    A description that says who it hires has stated a hiring geography, and
    requiring a structured field to agree would discard the clearest evidence
    the posting offers.
    """
    outcome = run(sentence, work_model=None, scope=scope)

    assert outcome.fingerprint.eligibility.hiring_scope.value.countries == expected


def test_a_region_stays_a_region_and_is_not_expanded() -> None:
    """LATAM is not a list of countries here.

    Region expansion is deterministic and happens later, against a config file
    that can be reviewed and corrected. A model expanding it inline would bake
    one reading of "LATAM" into every fingerprint that used it.
    """
    outcome = run("Open to candidates across LATAM.", work_model=None, scope="REGION:LATAM")
    scope = outcome.fingerprint.eligibility.hiring_scope.value

    assert scope.kind is HiringScopeKind.REGION
    assert scope.regions == [Region.LATAM]
    assert scope.countries == []


def test_worldwide_except_one_country_is_not_the_same_as_worldwide() -> None:
    outcome = run("Worldwide except Brazil.", work_model=None, scope="WORLDWIDE:!BR")
    scope = outcome.fingerprint.eligibility.hiring_scope.value

    assert scope.kind is HiringScopeKind.WORLDWIDE
    assert scope.exclusions == ["BR"]


def test_the_fingerprint_never_says_anything_about_a_candidate() -> None:
    """M2 records the restriction. M3 compares it to a person.

    The same document has to serve an applicant in Brazil and an applicant
    outside it, so nothing in it may take a view on who is reading.
    """
    outcome = run("Remote - Brazil", work_model="REMOTE", scope="COUNTRY_LIST:BR")
    document = outcome.fingerprint.model_dump_json().upper()

    # The verdict labels the architecture forbids by name. `contractor_eligible`
    # is a fact about the employer's terms and is deliberately not on this list.
    for verdict in ("GOOD_FOR_", "ELIGIBLE_FOR_APPLICANT", "TARGETS_ME", "SUITABLE_FOR"):
        assert verdict not in document


def test_the_dimension_vocabulary_is_closed_and_candidate_free() -> None:
    """A candidate-aware dimension cannot be added quietly.

    The document's scalar dimensions are exactly the transport's vocabulary. A
    new one that took a view on who is reading would have to pass through here
    first, where it is visible.
    """
    outcome = run("Remote - Brazil", work_model="REMOTE", scope="COUNTRY_LIST:BR")

    assert set(outcome.fingerprint.scalar_fields()) == set(ALL_DIMENSIONS)
