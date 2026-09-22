# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""The engine's honesty invariants, proven on fully synthetic data.

Contributors can validate every safeguard here without any private candidate
material: source strength ceilings, clause-scoped confirmation, certification
conflicts, mixed AND/OR and pure OR semantics, exact credentials, cross-context
composition, named framework gaps, page trimming and recruiter-value selection.
"""

from __future__ import annotations

from resume_tailor.core.matching.matcher import deterministic_match
from resume_tailor.core.models import Bullet, MatchType
from resume_tailor.core.pipeline import trim_to_pages
from resume_tailor.core.validation.validator import check_bullet


def _match(index, text: str):
    from resume_tailor.core.job_analysis.analyzer import build_requirements
    from resume_tailor.core.models import JobAnalysis

    job = build_requirements(JobAnalysis(must_have=[text]), index.all_terms)
    rep = deterministic_match(job, index)
    return next(m for m in rep.matches if m.requirement_text == text)


# ------------------------------------------------------------ source strength


def test_source_strength_tiers(index):
    assert index.strength(index.by_id["hl_billing_001"]).value == "primary"  # case study
    assert (
        index.strength(index.by_id["il_agent_001"]).value == "portfolio_only"
    )  # self-published only
    assert (
        index.strength(index.by_id["hl_general_001"]).value == "user_verified"
    )  # confirmed by the user
    assert index.secondary_only(index.by_id["il_agent_001"])
    assert not index.secondary_only(index.by_id["hl_billing_001"])


def test_secondary_only_evidence_cannot_carry_direct(index):
    m = _match(index, "Experience building AI agents")
    assert m.match_type != MatchType.DIRECT  # only the portfolio vouches for this
    assert "secondary" in m.rationale


# ------------------------------------------------- clause-scoped confirmation


def test_scoped_number_cannot_widen_ownership(index):
    ok = Bullet(
        id="b",
        text="Sole owner of the automation layer connecting HubSpot, Stripe and n8n, within a wider estate of 60 active workflows.",
        evidence_ids=["hl_general_001"],
        origin="manual",
    )
    assert all(c.passed for c in check_bullet(ok, index, index.tenure_years("total_relevant")))
    bad = Bullet(
        id="b",
        text="Sole owner of all 60 workflows across the company.",
        evidence_ids=["hl_general_001"],
        origin="manual",
    )
    checks = check_bullet(bad, index, index.tenure_years("total_relevant"))
    assert any(not c.passed for c in checks)  # "sole ... 60" outside the confirmed scope


# ------------------------------------------------------ certification conflicts


def test_conflicted_certification_never_renders(index, demo_run):
    from resume_tailor.core.resume_generation.certifications import blocked_reason

    cloudline = next(c for c in index.bank.certifications if c.id == "cert_cloudline")
    assert blocked_reason(cloudline, index) is not None  # open conflict, not user-verified
    rendered = {c.issuer for c in demo_run.generated_resume.certifications}
    assert "Cloudline" not in rendered


# --------------------------------------------------------- boolean semantics


def test_mixed_and_or_requires_every_group(index):
    m = _match(index, "HubSpot, SQL, and Salesforce or Dynamics 365")
    assert m.match_type == MatchType.PARTIAL  # the CRM-alternatives group is unevidenced
    assert "Salesforce or Dynamics 365" in m.rationale


def test_pure_or_list_carried_by_strongest_alternative(index):
    m = _match(index, "Experience with n8n or Make or Workato")
    assert m.match_type == MatchType.DIRECT


# ------------------------------------------------------------ exact credentials


def test_platform_exposure_is_not_a_certification(index):
    m = _match(index, "Tableau certification")
    assert m.match_type == MatchType.UNSUPPORTED  # a two-week Tableau POC is not a credential


def test_held_credential_is_direct(index):
    m = _match(index, "FlowStack Automation Professional certification")
    assert m.match_type == MatchType.DIRECT
    assert "named credential held" in m.rationale


# ------------------------------------------------------ cross-context stitching


def test_unrelated_projects_cannot_compose_a_claim(index):
    m = _match(index, "Build and launch billing integrations using AI agents")
    assert (
        m.match_type != MatchType.DIRECT
    )  # billing lives at Harborlight, AI agents in the indie project
    assert "unverified" in m.rationale or m.match_type == MatchType.PARTIAL


# --------------------------------------------------------- named framework gaps


def test_named_frameworks_stay_gaps(index):
    assert _match(index, "Experience with Salesforce Flows").match_type == MatchType.UNSUPPORTED
    m = _match(index, "Experience building APIs with FastAPI or similar frameworks")
    assert m.match_type != MatchType.DIRECT  # REST-API work is not FastAPI


# ---------------------------------------------------------------- page trimming


def test_trimming_protects_heroes_and_openers(demo_run):
    res = demo_run.generated_resume.model_copy(deep=True)
    heroes = set(demo_run.resume_strategy.hero_evidence_ids)
    value = {rid: v["value"] for rid, v in demo_run.resume_strategy.evidence_value.items()}
    trimmed, notes = trim_to_pages(
        res,
        0.5,
        value,
        {p.position_id: 1 for p in demo_run.resume_strategy.position_plans},
        never_trim=heroes,
    )
    assert notes  # something was trimmed
    used = {eid for e in trimmed.experience for b in e.bullets for eid in b.evidence_ids}
    assert heroes <= used
    assert all(e.bullets for e in trimmed.experience)  # every position keeps its opener


# --------------------------------------------------- recruiter-value selection


def test_hero_selection_prefers_owned_measured_direct_work(demo_run):
    heroes = demo_run.resume_strategy.hero_evidence_ids
    assert "hl_billing_001" in heroes  # led, metric-backed, directly relevant, primary-sourced
    assert "cp_tableau_001" not in heroes  # exposure-level POC is never a hero
    assert "il_agent_001" not in heroes  # portfolio-only evidence is never a hero


def test_run_is_validated_and_grounded(demo_run):
    assert demo_run.validation_report.status == "pass"
    assert demo_run.validation_report.replaced == 0 and demo_run.validation_report.rejected == 0
    text = " ".join(b.text.lower() for _, b in demo_run.generated_resume.all_bullets())
    assert "salesforce" not in text  # the Salesforce Flows gap is never written into the resume
    items = {i.lower() for g in demo_run.generated_resume.skills for i in g.items}
    assert (
        "tableau" not in items and "salesforce" not in items
    )  # exposure and gaps never become Skills
