"""Worksite is not hiring geography. This file is that contract.

Three questions, extracted independently, combined only much later:

    1. WHERE MAY THE EMPLOYER HIRE?     hiring_scope
    2. WHERE MUST THE PERSON WORK?      work_environment.worksite_requirement
    3. IS RELOCATION REQUIRED?          relocation_required

A required New York office does not prove US-only hiring. The employer may
hire internationally and require relocation -- and postings that say exactly
that exist, which is why the two facts must be able to coexist without either
being derived from the other.

THE DOWNSTREAM RULE THIS PRESERVES INFORMATION FOR (not implemented here)
-------------------------------------------------------------------------
A later milestone owns the candidate-specific conclusion:

    candidate residence = BR
    + worksite_requirement = REQUIRED New York
    + relocation.willing  = false
    = BLOCKED

Note what that is *not*. The job is not "not hireable from Brazil" -- it may
well be. It is "not feasible for this candidate without relocation". Those are
different sentences about different things, and M2's job is to keep enough
fact around that the later layer can tell them apart.
"""

import json
from typing import Any

from career_agent.domain.enums import (
    ExtractionStatus,
    HiringScopeKind,
    PresenceRequirement,
    WorkModel,
)
from career_agent.llm.client import FakeClient, Family, LLMRequest, LLMResponse, ModelConfig
from career_agent.llm.transport import ALL_DIMENSIONS
from career_agent.pipeline.extract import JobSource, extract_job

CONFIG = ModelConfig(vendor="fake", identifier="fake-model")


def answer(sentence: str, rows: dict[str, dict[str, Any]]) -> str:
    """A complete description answer with only the named dimensions set."""
    observations = [
        {"dimension": name, "status": "NOT_STATED", "value": None, "confidence": 0.0}
        for name in ALL_DIMENSIONS
        if name not in rows
    ]
    evidence = [
        {"id": "ev_01", "source_kind": "JOB_DESCRIPTION", "quote": "You will own our reporting."}
    ]
    for name, row in rows.items():
        entry = {"dimension": name, "confidence": 0.9, **row}
        if entry.get("status") == "EXPLICIT":
            entry["evidence_id"] = "ev_02"
        observations.append(entry)
    if any(r.get("status") == "EXPLICIT" for r in rows.values()):
        evidence.append({"id": "ev_02", "source_kind": "JOB_DESCRIPTION", "quote": sentence})

    return json.dumps(
        {
            "observed_title": "Revenue Systems Analyst",
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
            "evidence": evidence,
        }
    )


def run(sentence: str, rows: dict[str, dict[str, Any]]) -> Any:
    posting = f"Revenue Systems Analyst\n\nYou will own our reporting.\n{sentence}\n"
    payload = answer(sentence, rows)

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
    return outcome.fingerprint


# --- A. hybrid NYC, hiring geography unstated -------------------------------


def test_a_required_office_does_not_prove_a_hiring_country() -> None:
    """The case the whole change exists for.

    A New York office is where the desk is. It is not a statement about who may
    sit at it, and turning one into the other would invent a restriction the
    employer never wrote.
    """
    sentence = "This role is hybrid and requires three days per week in our New York office."
    fp = run(
        sentence,
        {
            "work_environment.work_model": {"status": "EXPLICIT", "value": "HYBRID"},
            "work_environment.worksite_requirement": {
                "status": "EXPLICIT",
                "value": "REQUIRED:New York",
            },
            "work_environment.onsite_frequency": {
                "status": "EXPLICIT",
                "value": "three days per week",
            },
        },
    )

    assert fp.work_environment.work_model.value is WorkModel.HYBRID
    worksite = fp.work_environment.worksite_requirement.value
    assert worksite.level is PresenceRequirement.REQUIRED
    assert worksite.locations == ["New York"]
    assert fp.work_environment.onsite_frequency.value == "three days per week"

    # The assertion that matters.
    assert fp.eligibility.hiring_scope.status is ExtractionStatus.NOT_STATED
    assert fp.eligibility.hiring_scope.value is None


# --- B. explicit US restriction PLUS hybrid NYC -----------------------------


def test_a_stated_residence_requirement_does_prove_a_hiring_country() -> None:
    """Both facts, from two different sentences, held side by side.

    US-only is supported here because the posting states candidate geography --
    not because there is an office in New York.
    """
    fp = run(
        "Candidates must reside in the United States.",
        {
            "hiring_scope": {"status": "EXPLICIT", "value": "COUNTRY_LIST:US"},
            "work_environment.work_model": {"status": "INFERRED", "value": "HYBRID"},
            "work_environment.worksite_requirement": {
                "status": "INFERRED",
                "value": "REQUIRED:New York",
            },
        },
    )

    assert fp.eligibility.hiring_scope.value.kind is HiringScopeKind.COUNTRY_LIST
    assert fp.eligibility.hiring_scope.value.countries == ["US"]
    assert fp.work_environment.worksite_requirement.value.locations == ["New York"]


# --- C. international hiring AND required relocation ------------------------


def test_global_hiring_and_a_required_worksite_are_not_contradictory() -> None:
    """The shape that proves the two dimensions are independent.

    "We welcome international applicants and provide visa sponsorship.
    Successful candidates must relocate to New York." Every one of those facts
    is true at once, and a model that treated them as conflicting would have to
    discard one.
    """
    fp = run(
        "We welcome international applicants and provide visa sponsorship.",
        {
            "visa_sponsorship": {"status": "EXPLICIT", "value": "true"},
            "relocation_required": {"status": "INFERRED", "value": "true"},
            "work_environment.worksite_requirement": {
                "status": "INFERRED",
                "value": "REQUIRED:New York",
            },
        },
    )

    assert fp.eligibility.visa_sponsorship.value is True
    assert fp.eligibility.relocation_required.value is True
    assert fp.work_environment.worksite_requirement.value.level is PresenceRequirement.REQUIRED

    # No contradiction is recorded, because there is none.
    assert fp.eligibility.hiring_scope.status is ExtractionStatus.NOT_STATED


# --- D. an office that merely exists ----------------------------------------


def test_an_available_office_is_not_a_presence_requirement() -> None:
    """The distinction `office_locations` could not express.

    Under the old schema this posting and case A produced identical documents:
    both said `office_locations = ["New York"]`. One is a condition of the job
    and the other is a perk, and no downstream layer could tell them apart.
    """
    fp = run(
        "This is a remote role. Employees may work from our New York office if they prefer.",
        {
            "work_environment.work_model": {"status": "EXPLICIT", "value": "REMOTE"},
            "work_environment.worksite_requirement": {
                "status": "EXPLICIT",
                "value": "OPTIONAL:New York",
            },
        },
    )

    assert fp.work_environment.work_model.value is WorkModel.REMOTE
    assert fp.work_environment.worksite_requirement.value.level is PresenceRequirement.OPTIONAL
    assert fp.eligibility.hiring_scope.status is ExtractionStatus.NOT_STATED


# --- E. a preference is not a requirement -----------------------------------


def test_a_stated_preference_stays_a_preference() -> None:
    """EXPLICIT says the posting states it. PREFERRED says how binding it is.

    Collapsing those two axes would turn "we prefer candidates near London"
    into a wall -- the same mistake `LanguageRequirement` exists to prevent for
    languages.
    """
    fp = run(
        "We prefer candidates near our London office, but remote applicants will be considered.",
        {
            "work_environment.worksite_requirement": {
                "status": "EXPLICIT",
                "value": "PREFERRED:London",
            },
        },
    )

    field = fp.work_environment.worksite_requirement
    assert field.status is ExtractionStatus.EXPLICIT
    assert field.value.level is PresenceRequirement.PREFERRED
    assert field.value.locations == ["London"]


# --- F. onsite --------------------------------------------------------------


def test_onsite_in_berlin_does_not_become_germany_only_hiring() -> None:
    """Same rule as case A, at the other end of the work-model range."""
    fp = run(
        "This position is full-time onsite in Berlin.",
        {
            "work_environment.work_model": {"status": "EXPLICIT", "value": "ONSITE"},
            "work_environment.worksite_requirement": {
                "status": "EXPLICIT",
                "value": "REQUIRED:Berlin",
            },
        },
    )

    assert fp.work_environment.work_model.value is WorkModel.ONSITE
    assert fp.work_environment.worksite_requirement.value.locations == ["Berlin"]
    assert fp.eligibility.hiring_scope.status is ExtractionStatus.NOT_STATED


# --- the invariants ---------------------------------------------------------


def test_a_required_worksite_must_name_somewhere() -> None:
    """ "You must be in an office" with no office named is not actionable.

    Recorded as a requirement it would block jobs on a fact the posting never
    supplied, which is the direction this system is built never to fail in.
    """
    from pydantic import ValidationError

    from career_agent.domain.fingerprint import WorksiteRequirement

    try:
        WorksiteRequirement(level=PresenceRequirement.REQUIRED, locations=[])
    except ValidationError as exc:
        assert "must name where" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a REQUIRED worksite with no location was accepted")


def test_the_fingerprint_still_says_nothing_about_a_candidate() -> None:
    """M2 records the facts. M3 combines them with a person.

    The same document has to serve an applicant in Brazil and one in New York.
    """
    fp = run(
        "This role is hybrid and requires three days per week in our New York office.",
        {
            "work_environment.worksite_requirement": {
                "status": "EXPLICIT",
                "value": "REQUIRED:New York",
            },
        },
    )
    document = fp.model_dump_json().upper()

    for verdict in ("GOOD_FOR_", "ELIGIBLE_FOR_APPLICANT", "TARGETS_ME", "BLOCKED", "FEASIBLE"):
        assert verdict not in document


def test_the_rename_needs_no_migration(tmp_path: Any) -> None:
    """Schema version 3 lands on the existing tables without any DDL.

    ``office_locations`` became ``worksite_requirement`` and its value shape
    changed from a bare list to a level plus places. Neither is a schema change
    at the SQL layer, and it is worth proving rather than assuming:

    * ``fp_eligibility.dimension`` is a plain TEXT column with no CHECK
      constraining which dimension names may appear. A renamed dimension is a
      different string in the same column.
    * ``fp_eligibility.value_json`` holds JSON. A richer value is a different
      document in the same column.
    * ``fingerprint`` carries ``schema_version``, and ``existing()`` keys on it.
      A version-2 row can never be mistaken for a version-3 one, so the 23
      development fingerprints already stored stay put, stay readable as
      history, and are never reused as if they answered the current question.

    The cache reaches the same conclusion by a different route: description
    identity includes ``prompt_version`` and ``transport_version``, both of
    which moved, so no cached v2 answer can be served for a v3 request.
    """
    from career_agent.storage.db import connect, migrate

    conn = connect(tmp_path / "m.db")
    migrate(conn)

    columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(fp_eligibility)")}
    assert columns["dimension"]["type"] == "TEXT"

    # No CHECK anywhere in the table definition mentions a dimension name.
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'fp_eligibility'").fetchone()[
        "sql"
    ]
    assert "worksite_requirement" not in ddl
    assert "office_locations" not in ddl

    # Version-2 and version-3 rows for the same posting coexist by design.
    assert "schema_version" in {
        row["name"] for row in conn.execute("PRAGMA table_info(fingerprint)")
    }
    unique = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'fingerprint'").fetchone()[
        "sql"
    ]
    assert "schema_version" in unique.split("UNIQUE")[1]
