"""What the posting asks for, beside what has been confirmed.

The two rules under every test: a gap is shown rather than hidden, and nothing
is asserted on either side without a quote behind it.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from career_agent.config.search_config import load_search_config
from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimSource, ClaimType
from career_agent.match.engine import JobFacts, match_job
from career_agent.match.preparation import Readiness, prepare

ROOT = Path(__file__).resolve().parents[2]

POSTING = """About the role.

You will own the HubSpot platform and design business process flows across
revenue operations. Experience with REST APIs and webhooks is required, and
you will build integrations between finance and CRM systems.

We are hiring across LATAM.
"""


@pytest.fixture(scope="module")
def config():
    directory = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "config" / "search.worked-example.yaml", directory)
    shutil.copy(ROOT / "config" / "places.yaml", directory)
    loaded, _ = load_search_config(directory, use_example=True)
    return loaded


@pytest.fixture(scope="module")
def result(config):
    return match_job(
        config,
        JobFacts(title="Business Systems Engineer", description=POSTING, provider="greenhouse"),
        computed_at="2026-09-06T00:00:00Z",
    )


def claim(text: str, claim_type: ClaimType, *, verified: bool = True) -> VerifiedClaim:
    return VerifiedClaim(
        claim_key=text.lower().replace(" ", "-")[:60],
        claim_type=claim_type,
        text=text,
        source=ClaimSource.RESUME,
        verified=verified,
        evidence_ref=f"- {text}",
    )


# =========================================================================
# 1. A GAP IS SHOWN
# =========================================================================


def test_with_no_evidence_every_requirement_is_a_gap(config, result) -> None:
    """The honest starting state. Somebody who has confirmed nothing has
    evidence for nothing, and a view that softened that would be flattering
    them on the way to an interview."""
    plan = prepare(config, result, [])
    assert plan.requirements
    assert all(r.readiness is Readiness.GAP for r in plan.requirements)
    assert not plan.of(Readiness.MATCHED)


def test_a_gap_carries_no_invented_evidence(config, result) -> None:
    plan = prepare(config, result, [])
    for requirement in plan.of(Readiness.GAP):
        assert requirement.evidence_text is None
        assert requirement.evidence_key is None
        assert requirement.matched_on is None


def test_a_requirement_never_disappears_for_being_unmet(config, result) -> None:
    """§20's rule, asserted directly: the count of requirements does not depend
    on how much the candidate can answer."""
    with_nothing = prepare(config, result, [])
    with_something = prepare(
        config, result, [claim("Owned the HubSpot platform end to end", ClaimType.EMPLOYMENT)]
    )
    assert len(with_nothing.requirements) == len(with_something.requirements)


# =========================================================================
# 2. A MATCH IS QUOTED ON BOTH SIDES
# =========================================================================


def test_a_match_shows_the_employers_sentence_and_the_candidates(config, result) -> None:
    plan = prepare(
        config, result, [claim("Owned the HubSpot platform end to end", ClaimType.EMPLOYMENT)]
    )
    matched = plan.of(Readiness.MATCHED)
    assert matched, "an employment claim naming HubSpot answered nothing"
    for requirement in matched:
        assert requirement.posting_quote, "no sentence from the posting"
        assert requirement.evidence_text, "no line from the candidate"
        assert requirement.matched_on, "no phrase connecting the two"
        assert requirement.matched_on in requirement.evidence_text.lower()


def test_having_used_a_tool_is_weaker_than_having_done_the_work(config, result) -> None:
    """A tool named on a CV and a job that used it are different answers to the
    same question. Collapsing them would overstate the weaker one."""
    as_tool = prepare(config, result, [claim("HubSpot", ClaimType.TOOL)])
    as_work = prepare(
        config, result, [claim("Owned the HubSpot platform end to end", ClaimType.EMPLOYMENT)]
    )
    assert as_tool.of(Readiness.PARTIAL)
    assert as_work.of(Readiness.MATCHED)


def test_an_unconfirmed_claim_answers_nothing(config, result) -> None:
    """Otherwise the review step is decorative: a proposal read off a CV would
    answer a requirement without anybody having stood behind it."""
    draft = claim("Owned the HubSpot platform end to end", ClaimType.EMPLOYMENT, verified=False)
    plan = prepare(config, result, [draft])
    assert not plan.of(Readiness.MATCHED)
    assert not plan.of(Readiness.PARTIAL)


def test_tools_on_a_claim_are_searched_too(config, result) -> None:
    structured = VerifiedClaim(
        claim_key="acme",
        claim_type=ClaimType.EMPLOYMENT,
        text="Systems analyst at Acme",
        source=ClaimSource.RESUME,
        verified=True,
        tools=["HubSpot", "n8n"],
    )
    plan = prepare(config, result, [structured])
    assert plan.of(Readiness.MATCHED)


# =========================================================================
# 3. CONCERNS ARE GATHERED, NEVER STRENGTHENED
# =========================================================================


def test_a_domestic_reading_stays_a_likelihood(config) -> None:
    """Gathered from the matcher and restated no more strongly than it was
    made. A 401(k) is not a refusal here either."""
    result = match_job(
        config,
        JobFacts(
            title="Engineer",
            description="We offer a competitive 401(k). You will own the HubSpot platform.",
            provider="greenhouse",
        ),
        computed_at="2026-09-06T00:00:00Z",
    )
    plan = prepare(config, result, [])
    employment = [c for c in plan.concerns if c.kind == "employment"]
    assert employment
    assert "Not a refusal" in employment[0].detail
    assert "not eligible" not in employment[0].detail.lower()


def test_an_unstated_hiring_scope_is_something_to_ask_about(config) -> None:
    result = match_job(
        config,
        JobFacts(title="Engineer", description="You will own the HubSpot platform."),
        computed_at="2026-09-06T00:00:00Z",
    )
    plan = prepare(config, result, [])
    eligibility = [c for c in plan.concerns if c.kind == "eligibility"]
    assert eligibility
    assert "does not state where it hires" in eligibility[0].detail


def test_a_stated_scope_raises_no_eligibility_concern(config, result) -> None:
    """The posting says "We are hiring across LATAM", so there is nothing to
    settle and a concern would be noise."""
    plan = prepare(config, result, [])
    assert not [
        c for c in plan.concerns if c.kind == "eligibility" and "does not state" in c.detail
    ]


def test_an_unstated_level_is_reported(config, result) -> None:
    plan = prepare(config, result, [])
    assert [c for c in plan.concerns if c.kind == "seniority"]


# =========================================================================
# 4. IT REPORTS, AND RECOMMENDS NOTHING
# =========================================================================


def test_there_is_no_readiness_score(config, result) -> None:
    """A single figure over requirements would be a fourth measurement, and
    ADR-0004 keeps three. `counts` is a tally, not a verdict."""
    plan = prepare(config, result, [])
    assert isinstance(plan.counts, dict)
    assert not hasattr(plan, "score")
    assert not hasattr(plan, "recommendation")
    assert not hasattr(plan, "should_apply")


def test_unresolved_is_not_a_quieter_gap() -> None:
    """A gap means the posting asked and nothing is confirmed. Unresolved means
    this system cannot tell. Reporting the second as the first would invent a
    shortcoming the candidate does not have."""
    assert Readiness.UNRESOLVED is not Readiness.GAP
    assert {r.value for r in Readiness} == {"MATCHED", "PARTIAL", "GAP", "UNRESOLVED"}
