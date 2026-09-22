"""A board's structured location, read against the candidate's own permissions.

THE DEFECT THIS FILE EXISTS FOR
-------------------------------
Measured on the real corpus, 2026-09-07, the twenty highest-scoring postings
included `Remote U.S.`, Bangalore, Bengaluru, Pune, a hybrid role in Gurugram,
San Francisco, New York, Boston and Dublin. Every one of them read UNRESOLVED
on eligibility and every one of them was recommended.

The evidence was there the whole time. Ashby persists `location: "Remote U.S."`
beside `workplaceType: "Remote"`; the pipeline archived both and the gate read
neither, because `publishes_hiring_scope` is False for every ATS and that flag
was the only way a location could reach the question.

WHY ONE COLUMN CAN MEAN TWO THINGS
----------------------------------
`location_raw` on an ONSITE or HYBRID posting is an OFFICE, and invariant 3
exists because reading an office as a hiring scope is the most expensive
mistake this system makes.

`location_raw` on a REMOTE posting cannot be an office, because the role has
none. The discriminator is the employer's own structured answer, not a guess
about the string, which is why this is a rule about a PAIR of fields.

EVERY CANDIDATE HERE IS SYNTHETIC
---------------------------------
Two profiles, one in Brazil and one in the United States, built from the
committed worked example. The point of the second is that nothing here is
hard-coded to one country: `Remote U.S.` passes for the American candidate
through the same code path that fails it for the Brazilian one.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import EligibilityStatus, GateResult
from career_agent.match.engine import JobFacts, match_job

ROOT = Path(__file__).resolve().parents[2]

#: A body that describes the work and states nothing about geography. Every
#: geographic conclusion in this file therefore comes from the structured
#: fields, which is what makes the assertions about them meaningful.
NEUTRAL_BODY = (
    "You will build and maintain REST API integrations and webhooks between our "
    "internal systems, own workflow automation in an iPaaS, and maintain data "
    "quality across the stack. You will work with SQL and JSON daily."
)

#: The sentence that made 598 postings wrongly eligible in V1.2, in the shape it
#: keeps coming back in: a cultural paragraph that names regions.
CULTURAL_BODY = NEUTRAL_BODY + (
    " Our team is distributed and we work remotely across the US, Europe, LatAm, and beyond."
)


@pytest.fixture(scope="module")
def brazil():
    """The committed worked example, whose candidate is in Brazil.

    Never `search.local.yaml`: that is the owner's private search, it is
    gitignored, and a test that passes because of what is in it proves nothing
    on anybody else's machine.
    """
    directory = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "config" / "search.worked-example.yaml", directory)
    shutil.copy(ROOT / "config" / "places.yaml", directory)
    config, _ = load_search_config(directory, use_example=True)
    return config


@pytest.fixture(scope="module")
def american(brazil):
    """The same configuration with an American candidate in it.

    Invented, and the whole point: a rule that only produced the right answer
    for one country would be a rule that had that country written into it.
    """
    return brazil.model_copy(
        update={
            "eligibility": brazil.eligibility.model_copy(
                update={
                    "candidate_country": "US",
                    "candidate_country_label": "the United States",
                    "eligible_countries": ["US"],
                    "eligible_scopes": ["WORLDWIDE", "NORTH_AMERICA"],
                }
            )
        }
    )


def _verdict(config, location: str | None, workplace: str | None, body: str = NEUTRAL_BODY):
    result = match_job(
        config,
        JobFacts(
            title="Integration Engineer",
            description=body,
            location_raw=location,
            workplace_type=workplace,
        ),
        computed_at="2026-09-07T00:00:00Z",
    )
    gate = next(g for g in result.gates if g.gate == "geography")
    return result.eligibility_status, gate


# =========================================================================
# a remote role's location IS its hiring region
# =========================================================================


def test_remote_us_is_refused_for_a_candidate_in_brazil(brazil) -> None:
    """The posting that sat at the top of the real list, scoring 85."""
    status, gate = _verdict(brazil, "Remote U.S.", "REMOTE")

    assert status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    assert gate.result is GateResult.FAIL
    # The employer's own words, in the REASON. Never in `quote`: ADR-0002
    # makes that a contiguous substring of the posting text, and a form field
    # beside the posting is not one. See `StructuredGeography`.
    assert "Remote U.S." in gate.reason
    assert gate.quote is None


def test_remote_us_is_accepted_for_a_candidate_in_the_united_states(american) -> None:
    """Nothing here is hard-coded to one country.

    Same posting, same code path, opposite answer -- decided entirely by the
    candidate's own configured permissions.
    """
    status, gate = _verdict(american, "Remote U.S.", "REMOTE")

    assert status is EligibilityStatus.VERIFIED_ELIGIBLE
    assert gate.result is GateResult.PASS


def test_remote_india_is_refused(brazil) -> None:
    assert _verdict(brazil, "Remote - India", "REMOTE")[0] is (
        EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    )


def test_remote_eu_is_refused_for_a_candidate_with_no_eu_permission(brazil) -> None:
    assert _verdict(brazil, "Remote EU", "REMOTE")[0] is EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_remote_worldwide_is_accepted(brazil) -> None:
    status, gate = _verdict(brazil, "Anywhere in the World", "REMOTE")
    assert status is EligibilityStatus.VERIFIED_ELIGIBLE
    assert gate.result is GateResult.PASS


def test_remote_latam_is_accepted(brazil) -> None:
    assert _verdict(brazil, "Remote - LATAM", "REMOTE")[0] is EligibilityStatus.VERIFIED_ELIGIBLE


def test_remote_brazil_is_accepted(brazil) -> None:
    assert _verdict(brazil, "Remote - Brazil", "REMOTE")[0] is EligibilityStatus.VERIFIED_ELIGIBLE


# =========================================================================
# an onsite or hybrid role's location is a place she would have to be
# =========================================================================


def test_a_hybrid_role_in_another_country_is_refused(brazil) -> None:
    """A hybrid role in Gurugram is not a role somebody in Brazil can take, and
    calling it unresolved is a politeness that wastes her afternoon."""
    status, gate = _verdict(brazil, "Gurugram", "HYBRID")

    assert status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    assert "hybrid" in gate.reason.lower()


def test_an_onsite_role_in_another_country_is_refused(brazil) -> None:
    status, gate = _verdict(brazil, "San Francisco, CA", "ONSITE")

    assert status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    assert "on site" in gate.reason.lower()


def test_an_onsite_role_in_her_own_country_is_not_refused_on_geography(brazil) -> None:
    """Being able to reach the office is not permission to be hired, so this is
    not a PASS either -- only a stated scope answers that."""
    status, gate = _verdict(brazil, "Sao Paulo", "ONSITE")

    assert status is not EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    assert gate.result is not GateResult.FAIL


# =========================================================================
# absence, and what may not fill it
# =========================================================================


def test_bare_remote_with_no_geography_stays_unresolved(brazil) -> None:
    """The single commonest posting shape in the corpus. `Remote` is a worksite
    answer and says nothing about hiring."""
    status, gate = _verdict(brazil, "Remote", "REMOTE")

    assert status is EligibilityStatus.UNRESOLVED
    assert gate.result is GateResult.UNRESOLVED


def test_a_sentence_about_where_the_team_works_still_opens_the_gate(brazil) -> None:
    """The boundary V1.2 drew, stated rather than quietly relied on.

    "We work remotely across the US, Europe, LatAm" clears the `HIRING_INTENT`
    anchor, because it IS a sentence about working rather than a product
    paragraph that happens to name a region. With no structured evidence to
    weigh against, the product reads it as scope evidence and passes.

    **The residual risk is named here rather than hidden.** An employer whose
    team spans LATAM does not necessarily hire there. What makes that
    acceptable is the asymmetry V1.2 settled: with nothing else to go on, a
    working sentence is the best evidence available, and the case where it
    would do real damage -- overruling a structured restriction -- is closed
    by the test below.
    """
    status, _ = _verdict(brazil, "Remote", "REMOTE", body=CULTURAL_BODY)

    assert status is EligibilityStatus.VERIFIED_ELIGIBLE


def test_a_paragraph_about_customers_in_a_region_does_not_open_the_gate(brazil) -> None:
    """The V1.2 defect itself, still closed.

    "We have customers across LATAM" names a market, not a hiring permission,
    and it is the exact shape that made 598 postings wrongly eligible.
    """
    body = NEUTRAL_BODY + " Our team is distributed and we have customers across LATAM."

    status, _ = _verdict(brazil, "Remote", "REMOTE", body=body)

    assert status is EligibilityStatus.UNRESOLVED


def test_a_cultural_paragraph_does_not_overrule_a_structured_restriction(brazil) -> None:
    """The precedence that matters most.

    An employer answered a form field saying where this remote role is open.
    A sentence elsewhere in the advert does not overrule an answered form.
    """
    status, gate = _verdict(brazil, "Remote U.S.", "REMOTE", body=CULTURAL_BODY)

    assert status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    assert gate.result is GateResult.FAIL


def test_a_location_that_resolves_to_nothing_stays_unresolved(brazil) -> None:
    """The Jabalpur rule. A place this configuration cannot resolve means the
    configuration cannot resolve it, and the risk is not symmetric."""
    status, _ = _verdict(brazil, "Zzyzx Springs", "REMOTE")

    assert status is EligibilityStatus.UNRESOLVED


def test_a_board_that_states_no_workplace_type_is_left_exactly_as_it_was(brazil) -> None:
    """Most of the corpus. Without the employer's own structured answer there
    is nothing to say whether the location is an office or a region."""
    status, _ = _verdict(brazil, "San Francisco, CA", None)

    assert status is EligibilityStatus.UNRESOLVED


def test_a_salary_in_dollars_establishes_nothing_about_geography(brazil) -> None:
    """USD compensation is not hiring-scope evidence, and never was."""
    result = match_job(
        brazil,
        JobFacts(
            title="Integration Engineer",
            description=NEUTRAL_BODY,
            location_raw="Remote",
            workplace_type="REMOTE",
            salary_min=195000,
            salary_max=229000,
            salary_currency="USD",
            salary_period="YEAR",
        ),
        computed_at="2026-09-07T00:00:00Z",
    )

    assert result.eligibility_status is EligibilityStatus.UNRESOLVED


def test_a_401k_is_context_and_not_a_refusal(brazil) -> None:
    """`DomesticContext` has no member that refuses anybody, and this asserts
    the gate agrees. A benefit strengthens an explanation; it never becomes
    the sole basis for VERIFIED_NOT_ELIGIBLE."""
    body = NEUTRAL_BODY + " We offer medical benefits and a 401(k) plan."

    status, _ = _verdict(brazil, "Remote", "REMOTE", body=body)

    assert status is EligibilityStatus.UNRESOLVED


def test_a_workation_perk_is_not_a_hiring_scope(brazil) -> None:
    """Found on the real corpus, and the most convincing costume the V1.2
    defect has worn.

    An `Account Executive DACH (German Speaking)` based in Berlin came out
    VERIFIED_ELIGIBLE for a candidate in Brazil, on this sentence from its
    perks list:

        "Work from anywhere in the world for 30 days per year"

    It clears `HIRING_INTENT` -- it genuinely is a sentence about working --
    and the phrase it matches is a real worldwide pattern. What makes it not a
    hiring scope is the duration.
    """
    body = NEUTRAL_BODY + " Work from anywhere in the world for 30 days per year."

    status, _ = _verdict(brazil, "Berlin", None, body=body)

    assert status is not EligibilityStatus.VERIFIED_ELIGIBLE


def test_a_workation_counted_in_words_rather_than_digits(brazil) -> None:
    """The same perk, spelled the way two REAL postings spelled it.

    Found on the owner's corpus on 2026-09-09, both VERIFIED_ELIGIBLE and both
    at companies requiring a US base:

        "Flexible PTO and the freedom to work from anywhere in the world for
         up to a month"

    The guard had every spelling of the quantity except the article. `30 days`
    was caught and `a month` was not, which is the same holiday policy and the
    same wrong answer.
    """
    body = (
        NEUTRAL_BODY
        + " Flexible PTO and the freedom to work from anywhere in the world for up to a month."
    )

    status, _ = _verdict(brazil, "San Francisco, CA", None, body=body)

    assert status is not EligibilityStatus.VERIFIED_ELIGIBLE


def test_a_workation_written_with_a_hyphen(brazil) -> None:
    r"""Three postings at one employer, eligible because of ONE CHARACTER.

        "Work From Anywhere: work from anywhere in the world 4-weeks each year"

    `\d+\s*` cannot cross a hyphen, so `4-weeks` read as no quantity at all
    and a four-week perk opened a gate for a candidate in another hemisphere.
    """
    body = NEUTRAL_BODY + " Work From Anywhere: work from anywhere in the world 4-weeks each year."

    status, _ = _verdict(brazil, "New York, New York, United States", None, body=body)

    assert status is not EligibilityStatus.VERIFIED_ELIGIBLE


def test_a_real_hiring_sentence_is_untouched_by_the_widened_guard(brazil) -> None:
    """The direction that matters more. Widening a NEGATION is how a guard
    starts hiding jobs she could take, and the risk is not symmetric: a false
    PASS wastes an afternoon, a false FAIL costs her the job.

    This sentence is from the corpus too, and it is a genuine hiring scope.
    """
    body = NEUTRAL_BODY + " You are located in LATAM or the US and can work within US time zones."

    status, _ = _verdict(brazil, "Remote, LATAM/US", "REMOTE", body=body)

    assert status is EligibilityStatus.VERIFIED_ELIGIBLE


def test_an_unbounded_worldwide_statement_still_opens_the_gate(brazil) -> None:
    """The negation is narrow on purpose. An employer that hires globally has
    said so, and this must not start refusing them."""
    body = NEUTRAL_BODY + " We hire globally and you can work from anywhere."

    status, _ = _verdict(brazil, "Berlin", None, body=body)

    assert status is EligibilityStatus.VERIFIED_ELIGIBLE


# =========================================================================
# seniority as a DISPLAY GATE, which is a different question from a score
# =========================================================================


def test_an_empty_excluded_list_excludes_nothing(brazil) -> None:
    """ "Not preferred" is not "prohibited", and inferring one from the other
    would decide for every candidate that the levels they did not list are
    levels they refuse."""
    assert brazil.preferences.seniority.excluded == []


def test_the_display_gate_hides_only_what_she_named(brazil) -> None:
    """The mechanism, exercised against a synthetic configuration.

    `preferred` only ever moved a score. On the real corpus that meant a
    Director role earned eight of ten seniority points and sat in the top
    twenty of a list built to find mid-to-senior work: not preferred, and
    recommended anyway.
    """
    from career_agent.domain.enums import Seniority
    from career_agent.storage.mvp_repo import JobFilter

    picky = brazil.model_copy(
        update={
            "preferences": brazil.preferences.model_copy(
                update={
                    "seniority": brazil.preferences.seniority.model_copy(
                        update={"excluded": [Seniority.STAFF, Seniority.LEAD]}
                    )
                }
            )
        }
    )
    excluded = tuple(level.value for level in picky.preferences.seniority.excluded)

    assert excluded == ("STAFF", "LEAD")
    # The filter carries the list; it never reads the configuration itself.
    narrowed = JobFilter(excluded_seniorities=excluded, include_excluded_seniority=False)
    neutral = JobFilter()

    assert narrowed.excluded_seniorities == ("STAFF", "LEAD")
    assert neutral.excluded_seniorities == ()
    assert neutral.include_excluded_seniority is True


# =========================================================================
# nothing configured is not "nothing permitted"
# =========================================================================
#
# THE DEFECT, MEASURED 2026-09-18 ON THE DEMO CORPUS
# ---------------------------------------------------
# A fresh install (the starter: `candidate_country: ''`, `eligible_countries:
# []`, `eligible_scopes: []`) read 13 of 21 postings VERIFIED_NOT_ELIGIBLE and
# 0 eligible. With `candidate_country: BR` and the two lists still empty, a
# remote posting located "Sao Paulo, Brazil" read "That does not include
# Brazil", and an on-site posting in Sao Paulo read "you are in Brazil" as a
# refusal. Three readers built "where she may work" three different ways:
# `_scope_verdict` returned None for "nothing configured" and
# `structured_geography` folded that None into FAIL; the ONSITE / HYBRID
# branch and `_required_attendance` compared against `eligible_countries`
# alone and never looked at `candidate_country`.
#
# The rule, in the words of invariant 2 turned around: absence is never
# permission, and it is never a refusal either. Unknown is UNRESOLVED.


@pytest.fixture(scope="module")
def fresh(brazil):
    """The starter's eligibility: a person who has typed nothing yet."""
    return brazil.model_copy(
        update={
            "eligibility": brazil.eligibility.model_copy(
                update={
                    "candidate_country": "",
                    "candidate_country_label": "",
                    "eligible_countries": [],
                    "eligible_scopes": [],
                }
            )
        }
    )


@pytest.fixture(scope="module")
def brazil_bare(brazil):
    """A person in Brazil who has set her country and nothing else. This is
    the state a settings action left a file in on 2026-09-18, and 59 of 60
    Brazilian postings turned VERIFIED_NOT_ELIGIBLE."""
    return brazil.model_copy(
        update={
            "eligibility": brazil.eligibility.model_copy(
                update={
                    "candidate_country": "BR",
                    "candidate_country_label": "Brazil",
                    "eligible_countries": [],
                    "eligible_scopes": [],
                }
            )
        }
    )


@pytest.mark.parametrize(
    ("location", "workplace"),
    [
        ("Sao Paulo, Brazil", "REMOTE"),
        ("Sao Paulo, Brazil", "ONSITE"),
        ("Sao Paulo, Brazil", "HYBRID"),
        ("Remote - LATAM", "REMOTE"),
        ("Anywhere in the World", "REMOTE"),
        ("Remote U.S.", "REMOTE"),
        ("Gurugram, India", "ONSITE"),
    ],
)
def test_a_fresh_user_is_refused_by_nothing(fresh, location, workplace) -> None:
    """No country, no list, no scope: no place is compatible or incompatible."""
    status, gate = _verdict(fresh, location, workplace)
    assert status is EligibilityStatus.UNRESOLVED
    assert gate.result is GateResult.UNRESOLVED


def test_a_fresh_user_never_sees_a_reason_with_an_empty_country(fresh) -> None:
    """The old FAIL reason ended in "does not include ." because the label was
    empty. No reason about geography may be composed for a person with none."""
    _, gate = _verdict(fresh, "Remote U.S.", "REMOTE")
    assert "does not include" not in (gate.reason or "")


def test_her_own_country_counts_when_the_list_is_empty(brazil_bare) -> None:
    """`candidate_country: BR` beside `eligible_countries: []` still knows she
    is in Brazil: a remote posting open in Brazil is a PASS, and an office in
    Sao Paulo is not a refusal."""
    status, gate = _verdict(brazil_bare, "Sao Paulo, Brazil", "REMOTE")
    assert status is EligibilityStatus.VERIFIED_ELIGIBLE
    assert gate.result is GateResult.PASS

    for workplace in ("ONSITE", "HYBRID"):
        status, gate = _verdict(brazil_bare, "Sao Paulo, Brazil", workplace)
        assert status is EligibilityStatus.UNRESOLVED, workplace
        assert gate.result is not GateResult.FAIL, workplace


def test_an_empty_scope_list_is_not_an_explicit_refusal_of_a_containing_region(
    brazil_bare,
) -> None:
    """`Remote - LATAM` holds Brazil. A person who has not said which scopes
    she accepts has not refused LATAM; she has not answered. Unknown."""
    for location in ("Remote - LATAM", "Anywhere in the World"):
        status, gate = _verdict(brazil_bare, location, "REMOTE")
        assert status is EligibilityStatus.UNRESOLVED, location
        assert gate.result is GateResult.UNRESOLVED, location


def test_a_non_empty_scope_list_that_leaves_a_region_out_still_refuses(brazil) -> None:
    """The explicit answer is unchanged: a candidate who accepts WORLDWIDE and
    LATAM and not EUROPE is refused by `Remote EU`, and a scope list that
    cannot contain Brazil is inert (ADR-0023) and refuses LATAM."""
    only_north_america = brazil.model_copy(
        update={
            "eligibility": brazil.eligibility.model_copy(
                update={"eligible_scopes": ["NORTH_AMERICA"]}
            )
        }
    )
    assert _verdict(only_north_america, "Remote - LATAM", "REMOTE")[0] is (
        EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    )
    assert _verdict(brazil, "Remote EU", "REMOTE")[0] is EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_explicit_incompatible_restrictions_are_still_refused_with_only_a_country(
    brazil_bare,
) -> None:
    """Knowing only that she is in Brazil is enough to refuse a posting that
    names another country and no region containing hers."""
    for location, workplace in (
        ("Remote U.S.", "REMOTE"),
        ("Remote (United States | Canada)", "REMOTE"),
        ("Remote - India", "REMOTE"),
        ("Gurugram, India", "ONSITE"),
        ("New York, NY", "HYBRID"),
    ):
        status, gate = _verdict(brazil_bare, location, workplace)
        assert status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE, location
        assert gate.result is GateResult.FAIL, location
        assert "Brazil" in gate.reason


def test_the_configured_candidate_is_unchanged(brazil) -> None:
    """Every verdict the worked example produced before this fix, after it."""
    expected = {
        ("Remote - Brazil", "REMOTE"): EligibilityStatus.VERIFIED_ELIGIBLE,
        ("Remote - LATAM", "REMOTE"): EligibilityStatus.VERIFIED_ELIGIBLE,
        ("Anywhere in the World", "REMOTE"): EligibilityStatus.VERIFIED_ELIGIBLE,
        ("Remote U.S.", "REMOTE"): EligibilityStatus.VERIFIED_NOT_ELIGIBLE,
        ("Remote EU", "REMOTE"): EligibilityStatus.VERIFIED_NOT_ELIGIBLE,
        ("Gurugram, India", "ONSITE"): EligibilityStatus.VERIFIED_NOT_ELIGIBLE,
        ("Sao Paulo, Brazil", "ONSITE"): EligibilityStatus.UNRESOLVED,
        ("Remote", "REMOTE"): EligibilityStatus.UNRESOLVED,
    }
    for (location, workplace), status in expected.items():
        assert _verdict(brazil, location, workplace)[0] is status, location


def test_a_required_office_in_her_own_country_is_not_incompatible(brazil_bare) -> None:
    """`_required_attendance` compared against the list alone too. With only
    her country set, an office she can reach is a requirement to confirm,
    never a proven incompatibility; an office she cannot reach still is one."""
    body = (
        NEUTRAL_BODY
        + " You must be able to work from our Sao Paulo, Brazil office three days a week."
    )
    assert _verdict(brazil_bare, "Sao Paulo, Brazil", "HYBRID", body)[0] is (
        EligibilityStatus.UNRESOLVED
    )

    body_away = (
        NEUTRAL_BODY
        + " You must be able to work from our Gurugram, India office three days a week."
    )
    assert _verdict(brazil_bare, "Gurugram, India", "HYBRID", body_away)[0] is (
        EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    )


def test_a_fresh_user_with_a_required_office_is_asked_not_refused(fresh) -> None:
    body = (
        NEUTRAL_BODY
        + " You must be able to work from our Gurugram, India office three days a week."
    )
    status, _ = _verdict(fresh, "Gurugram, India", "HYBRID", body)
    assert status is EligibilityStatus.UNRESOLVED
