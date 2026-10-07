"""Reading a job ad into quoted requirements (Tailor V2, `resume_doc.jd`). Synthetic ads."""

from __future__ import annotations

import pytest
from tests.support_tailor import MIXED_AD, NUMBERS_AD, PT_AD, SENIOR_AD, TOOLS_AD

from career_agent.resume_doc.jd import analyse, named_terms, tokens


def quotes(text: str) -> dict[str, object]:
    return {r.source_quote: r for r in analyse(text).requirements}


@pytest.mark.parametrize("ad", [SENIOR_AD, PT_AD, MIXED_AD, NUMBERS_AD, TOOLS_AD])
def test_every_requirement_quotes_the_ad_exactly(ad: str) -> None:
    found = analyse(ad).requirements
    assert found
    for r in found:
        assert ad[r.start : r.start + len(r.source_quote)] == r.source_quote
        for other in r.also_quoted:
            assert other in ad


def test_sections_hardness_and_kinds_in_english() -> None:
    found = quotes(SENIOR_AD)
    assert "We value curious people." not in found  # about the company, not an ask
    assert "Health insurance" not in found  # a benefit
    build = found["Build workflow automation across HubSpot and Salesforce"]
    assert build.kind == "RESPONSIBILITY" and build.hardness == "UNKNOWN"  # never REQUIRED by place
    assert found["Salesforce Apex is a must"].hardness == "REQUIRED"
    assert found["Workato certification"].kind == "CERTIFICATION"
    assert found["Workato certification"].hardness == "PREFERRED"
    assert found["Fluent English"].kind == "LANGUAGE"
    assert found["10+ years of revenue operations experience"].kind == "EXPERIENCE"
    auth = found["Must be authorized to work in the United States"]
    assert auth.kind == "WORK_AUTHORIZATION" and auth.eligibility


@pytest.mark.parametrize(
    ("line", "eligibility"),
    [
        ("You must be able to work in the EU", True),
        ("Must be eligible to work in Canada", True),
        ("Legally able to work in Brazil", True),
        ("Able to work in a fast-paced team", False),
        ("Able to work independently", False),
        # Found by the Beta 3 release review: a tool or a way of working is an
        # ask to answer, never "check the job's eligibility".
        ("Able to work in Salesforce and HubSpot daily.", False),
        ("You must be able to work in Python and SQL.", False),
        ("Able to work in Agile teams.", False),
        ("Able to work from the United States", True),
        ("Able to work in Sweden", True),
        # Round 2: a time zone, hours or a setting is never a work permit.
        ("Able to work in US-based healthcare compliance (HIPAA).", False),
    ],
)
def test_where_someone_may_work_is_eligibility_never_a_gap(line: str, eligibility: bool) -> None:
    """Found in the Beta 3 dogfood: "able to work in the EU" read as a gap."""
    (found,) = analyse(f"Requirements\n- {line}\n").requirements
    assert found.eligibility is eligibility, found.kind


def test_the_same_ask_twice_is_one_requirement_with_both_quotes() -> None:
    found = quotes(SENIOR_AD)
    hubspot = found["Experience with HubSpot"]
    assert hubspot.also_quoted == ("HubSpot experience required",)
    assert hubspot.hardness == "REQUIRED" and hubspot.importance == 4
    assert "HubSpot experience required" not in found


def test_different_products_of_one_vendor_stay_apart() -> None:
    found = quotes("Requirements\n- HubSpot administration\n- HubSpot Marketing Hub campaigns\n")
    assert len(found) == 2


def test_a_flattened_portuguese_ad_is_cut_at_its_inline_headings() -> None:
    found = quotes(PT_AD)
    assert "Especialista Sênior em Operações de Receita." not in found  # a title, not an ask
    assert found["Construir automações de processos comerciais no HubSpot"].kind == "RESPONSIBILITY"
    assert found["Experiência com HubSpot e n8n"].hardness == "REQUIRED"
    assert set(found["Experiência com HubSpot e n8n"].named) == {"hubspot", "n8n"}
    assert found["Experiência com Salesforce é obrigatória."].hardness == "REQUIRED"
    assert found["Certificação Workato"].hardness == "PREFERRED"
    assert found["Inglês avançado"].kind == "LANGUAGE"


def test_a_mixed_language_ad_keeps_tool_names() -> None:
    found = quotes(MIXED_AD)
    assert "hubspot" in found["Build HubSpot workflows e automações de processo"].named
    assert set(found["Experiência com n8n ou Zapier"].named) == {"n8n", "zapier"}


def test_names_are_what_may_not_be_claimed_without_evidence() -> None:
    assert named_terms("Workato certification") == {"workato"}
    assert named_terms("Build workflows in HubSpot and dbt") == {"hubspot", "dbt"}
    assert named_terms("Managed a $10M pipeline") == set()  # numbers are their own guard
    assert "crm" in named_terms("Cleaned up the CRM")
    assert tokens("Automações de processos") == {"automation", "process"}


def test_a_wrapped_ad_with_an_unheaded_role_and_eligibility_reads_right() -> None:
    """The shape the demo corpus has: text wrapped at a fixed width, the work
    described before any heading, and where the job hires at the end."""
    ad = (
        "Own the revenue systems behind our go to market motion. You will build workflow\n"
        "automation, own lead routing, and maintain the REST API integrations between our\n"
        "CRM and the data warehouse.\n\nRequirements\n- Strong HubSpot experience.\n\n"
        "Location: this role is remote, but candidates must be located within the\n"
        "United States. We do not sponsor visas."
    )
    found = analyse(ad).requirements
    kinds = {r.kind for r in found}
    duty = next(r for r in found if r.source_quote.startswith("You will build"))
    assert duty.kind == "RESPONSIBILITY" and duty.source_quote.endswith("data warehouse.")
    assert {"LOCATION", "WORK_AUTHORIZATION"} <= kinds
    place = next(r for r in found if r.kind == "LOCATION")
    assert place.source_quote.endswith("United States.") and place.eligibility


def test_eligibility_cues_the_review_found() -> None:
    found = quotes(
        "Requirements\n- Must be able to obtain a US security clearance\n"
        "- Brazil-based candidates only\n- Experience designing hybrid cloud architectures\n"
        "- Experience with C#\n"
    )
    assert found["Must be able to obtain a US security clearance"].eligibility
    assert found["Brazil-based candidates only"].eligibility
    assert not found["Experience designing hybrid cloud architectures"].eligibility
    assert "c#" in found["Experience with C#"].named


def test_names_are_read_strictly_from_evidence() -> None:
    assert named_terms("Excel at coaching new account executives.", strict=True) == set()
    assert named_terms("Built dashboards in Power BI for sales.", strict=True) == {"power bi"}
    assert "excel" in named_terms("Excel at coaching", strict=False)  # an ad is read generously


def test_a_huge_padded_ad_is_read_in_bounded_time() -> None:
    import time

    ad = "Requirements\n- Experience with HubSpot\n" + ("x" * 49 + " \n") * 40_000
    started = time.perf_counter()
    analyse(ad)
    assert time.perf_counter() - started < 3.0


@pytest.mark.parametrize(
    "line",
    [
        "Able to work in US time zones.",
        "Able to work in Europe hours.",
        "Able to work in EMEA business hours.",
        "Able to work in EU-friendly hours",
        "Must overlap with US time zones.",
    ],
)
def test_a_time_zone_or_hours_is_a_schedule_never_a_work_permit(line: str) -> None:
    """Invariant 3: a time zone is not geography (Beta 3 release review)."""
    (found,) = analyse(f"Requirements\n- {line}\n").requirements
    assert found.kind == "SCHEDULE" and found.eligibility, (line, found.kind)


def test_a_based_team_is_never_a_work_permit() -> None:
    (found,) = analyse("Requirements\n- Able to work in India-based teams.\n").requirements
    assert found.kind != "WORK_AUTHORIZATION"
