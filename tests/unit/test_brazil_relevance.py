"""Brazil relevance (2026-10-10): the same words, the territory she can work
from, and silence that neither pays nor costs. Synthetic postings only."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from career_agent.config.search_config import LexiconSignal, load_search_config
from career_agent.domain.enums import EligibilityStatus, EmploymentRelationship, GateResult
from career_agent.match import JobFacts, match_job
from career_agent.match.employment import read_employment
from career_agent.match.gates import _resolve_list, country_label
from career_agent.match.lexicon import find_hits
from career_agent.match.phrase_forms import find_forms, is_wizard_shaped, readings
from career_agent.match.text import fold_field

ROOT = Path(__file__).resolve().parents[2]

#: The shape of the advert that started this, written for the test.
FULL_STACK = (
    "About the Role\n"
    "Build internal tools for a marketing agency.\n"
    "Develop:\n"
    "Marketing dashboards\n"
    "Workflow automation\n"
    "Required: strong experience with system integrations and automation workflows.\n"
    "You're comfortable integrating APIs and automating complex workflows.\n"
    "Nice to have: Zapier, HubSpot.\n"
)


def wizard(label: str) -> LexiconSignal:
    return LexiconSignal(label=label, patterns=[label.lower()])


# =========================================================================
# the same words, written another way
# =========================================================================


def test_a_coordination_is_read_the_way_people_mean_it() -> None:
    assert readings("CRM implementation and optimization") == (("crm", "impl"), ("crm", "optim"))
    assert readings("Systems and API integration") == (("system", "integr"), ("api", "integr"))
    assert readings("Workflow automation") == (("workflow", "autom"),)


def test_inflection_and_order_inside_one_sentence() -> None:
    field = fold_field(FULL_STACK)
    found = [
        field.original[field.offsets[a] : field.offsets[b - 1] + 1]
        for a, b in find_forms(field, "Workflow automation")
    ]
    assert "Workflow automation" in found
    assert "automation workflows" in found
    assert "automating complex workflows" in found


def test_a_reading_never_crosses_a_line() -> None:
    """A bullet list folds to one line; the words must share a sentence."""
    field = fold_field("Marketing dashboards\nWorkflow tools\nAutomation of reports")
    assert find_forms(field, "Workflow automation") == []


def test_every_hit_quotes_text_the_posting_contains() -> None:
    hits = find_hits("w", wizard("Systems and API integration"), "description", FULL_STACK)
    assert hits
    for hit in hits:
        assert hit.quote in FULL_STACK
        assert (
            FULL_STACK[hit.char_start : hit.char_end]
            .lower()
            .startswith(("system", "integrat", "api"))
        )


def test_a_curated_entry_is_matched_exactly_as_written() -> None:
    """Several explicit patterns, or one that is not the label: not expanded."""
    curated = LexiconSignal(
        label="Workflow automation", patterns=["workflow automation", "zapier flows"]
    )
    assert not is_wizard_shaped(curated.label, tuple(curated.patterns))
    hits = find_hits("w", curated, "description", "We are automating complex workflows.")
    assert hits == []


def test_one_word_is_never_expanded() -> None:
    assert not is_wizard_shaped("Zapier", ("zapier",))


# =========================================================================
# the contract, when the body labels it
# =========================================================================


@pytest.mark.parametrize(
    ("text", "relationship"),
    [
        ("Position Type: Full-Time, Remote", EmploymentRelationship.EMPLOYEE),
        ("Employment type - Contractor", EmploymentRelationship.CONTRACTOR_B2B),
        ("Tipo de contratação: PJ", EmploymentRelationship.CONTRACTOR_B2B),
        ("Tipo de vaga: Efetivo", EmploymentRelationship.EMPLOYEE),
        ("Join our full-time team of builders.", EmploymentRelationship.UNRESOLVED),
    ],
)
def test_a_labelled_contract_line_is_a_statement(text: str, relationship) -> None:
    reading = read_employment(text)
    assert reading.relationship is relationship
    if relationship is not EmploymentRelationship.UNRESOLVED:
        assert reading.evidence and reading.evidence in text


# =========================================================================
# territory
# =========================================================================


@pytest.fixture(scope="module")
def brazil():
    directory = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "config" / "search.worked-example.yaml", directory)
    shutil.copy(ROOT / "config" / "places.yaml", directory)
    base, _ = load_search_config(directory, use_example=True)
    eligibility = base.eligibility.model_copy(
        update={
            "candidate_country": "BR",
            "candidate_country_label": "",  # what the first-run form writes
            "authorized_countries": ["BR"],
            "eligible_countries": [],
            "eligible_scopes": [],
            "not_authorized_countries": [],
            "sponsorship_needed": [],
            "blockers": [],
        }
    )
    return base.model_copy(update={"eligibility": eligibility})


def judged(config, *, location: str, body: str, workplace: str | None = "REMOTE"):
    result = match_job(
        config,
        JobFacts(
            title="Automation Specialist",
            description=f"You will automate workflows across our systems. {body}",
            location_raw=location,
            workplace_type=workplace,
        ),
        computed_at="2026-10-10T00:00:00Z",
    )
    return result.eligibility_status, {g.gate: g for g in result.gates}


def test_an_empty_label_still_names_her_country(brazil) -> None:
    assert country_label(brazil) == "Brazil"
    _, gates = judged(brazil, location="Brazil", body="")
    assert gates["geography"].result is GateResult.PASS
    assert "includes Brazil" in gates["geography"].reason


def test_the_latam_region_is_a_region() -> None:
    assert "LATAM" in _resolve_list("the LATAM region").stated_regions


def test_the_employers_own_latam_statement_admits_her_beside_an_office(brazil) -> None:
    status, gates = judged(
        brazil, location="Buenos Aires, AR", body="Candidates must be based in Latin America."
    )
    assert gates["geography"].result is GateResult.PASS
    assert status is EligibilityStatus.VERIFIED_ELIGIBLE


@pytest.mark.parametrize("location", ["London, UK (Remote)", "United States", "Remote U.S."])
def test_a_published_location_elsewhere_is_to_confirm_not_a_refusal(brazil, location) -> None:
    status, gates = judged(
        brazil, location=location, body="Work from anywhere, our team is global."
    )
    assert gates["geography"].result is GateResult.UNRESOLVED
    assert status is EligibilityStatus.UNRESOLVED


def test_an_explicit_restriction_still_refuses(brazil) -> None:
    status, gates = judged(
        brazil, location="Remote", body="This role is open to US residents only."
    )
    assert status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    assert gates["geography"].quote


def test_no_sponsorship_on_a_remote_role_she_does_from_home(brazil) -> None:
    status, gates = judged(
        brazil, location="Brazil", body="We are unable to sponsor visas for this position."
    )
    assert gates["work_authorization"].result is GateResult.PASS
    assert status is EligibilityStatus.VERIFIED_ELIGIBLE


# =========================================================================
# silence neither pays nor costs
# =========================================================================


def test_unknown_pay_and_an_unasked_contract_leave_the_score(brazil) -> None:
    config = brazil.model_copy(
        update={
            "preferences": brazil.preferences.model_copy(
                update={
                    "contract": brazil.preferences.contract.model_copy(
                        update={"preferred": [], "unwanted": []}
                    )
                }
            )
        }
    )
    result = match_job(
        config,
        JobFacts(title="Automation Specialist", description=FULL_STACK, location_raw="Brazil"),
        computed_at="2026-10-10T00:00:00Z",
    )
    pay = next(c for c in result.components if c.component_id == "compensation_contract")
    assert pay.points == 0 and pay.max_points == 0
    assert [c.signal_id for c in pay.contributions] == ["salary_unknown"]
