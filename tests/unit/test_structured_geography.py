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


# =========================================================================
# eligible_scopes is not an allowlist
# =========================================================================
#
# WHAT THE FIELD MEANS, traced 2026-09-23 across every surface that shows or
# reads it. Settings labels it "Hiring scopes that include you"; onboarding
# asks "Who is allowed to hire you?"; the ownership registry calls it "Where
# you can be hired from"; the worked example says a hiring scope that
# INCLUDES none of these fails. No surface tells a person that leaving a
# region out declines it. It describes the person, it does not filter.
#
# WHAT THE GATE USED TO DO. A non-empty list that left a region out was read
# as "her explicit answer" and refused it: a Brazil candidate who ticked only
# LATAM was VERIFIED_NOT_ELIGIBLE for `Remote - Worldwide`, with a reason
# saying Worldwide "does not include Brazil". The first correction (e30e534)
# made that UNRESOLVED; this one gives the answer geography already knows.
#
# THE RULE NOW, in `_region_verdict`:
#   * a region that provably contains a country in `eligible_countries`
#     admits, whether or not it is spelled in `eligible_scopes`;
#   * a region that provably contains NONE of her known countries refuses;
#   * anything unknown stays unknown -- including a country the gazetteer
#     cannot place, and including where she LIVES on its own, which is not
#     proof an employer hiring across a broad region can hire her.
#
# `South America` and `Europe` are gazetteer aliases for LATAM and EMEA, not
# region ids of their own; they are exercised here by the words a board or an
# employer actually prints.


def _with_geography(config, *, residence: str, countries: list[str], scopes: list[str]):
    return config.model_copy(
        update={
            "eligibility": config.eligibility.model_copy(
                update={
                    "candidate_country": residence,
                    "candidate_country_label": residence,
                    "eligible_countries": countries,
                    "eligible_scopes": scopes,
                }
            )
        }
    )


@pytest.fixture(scope="module")
def confirmed_latam_only(brazil):
    """Brazil confirmed as a country she can be hired in; only LATAM ticked."""
    return _with_geography(brazil, residence="BR", countries=["BR"], scopes=["LATAM"])


@pytest.fixture(scope="module")
def confirmed_no_scopes(brazil):
    """Brazil confirmed; no scope ticked at all."""
    return _with_geography(brazil, residence="BR", countries=["BR"], scopes=[])


#: Every representation of one hiring statement, and the outcome it must get
#: for a candidate with Brazil confirmed. Structured field, prose clause, prose
#: pattern and declared field must not disagree about the same employer answer.
_BROAD_REGIONS_HOLDING_BRAZIL = {
    "WORLDWIDE": (
        "Remote - Worldwide",
        "We hire people who can work from anywhere in the world.",
        "Anywhere in the World",
    ),
    "AMERICAS": (
        "Remote - Americas",
        "You can work from anywhere in the Americas.",
        "Americas",
    ),
    "SOUTH_AMERICA": (
        "Remote - South America",
        "You can work from anywhere in South America.",
        "South America",
    ),
    "LATAM": (
        "Remote - LATAM",
        "You can work from anywhere in Latin America.",
        "LATAM",
    ),
}


def _geography(config, *, location=None, body="", declared=None):
    result = match_job(
        config,
        JobFacts(
            title="Integration Engineer",
            description=f"{NEUTRAL_BODY} {body}".strip(),
            location_raw=location,
            workplace_type="REMOTE",
            declared_hiring_scope=declared,
        ),
        computed_at="2026-09-07T00:00:00Z",
    )
    gate = next(g for g in result.gates if g.gate == "geography")
    return result, gate


def _three_representations(config, region: str):
    location, prose, declared = _BROAD_REGIONS_HOLDING_BRAZIL[region]
    return {
        "structured": _geography(config, location=location),
        "prose": _geography(config, body=prose),
        "declared": _geography(config, declared=declared),
    }


@pytest.mark.parametrize("region", sorted(_BROAD_REGIONS_HOLDING_BRAZIL))
@pytest.mark.parametrize("fixture", ["confirmed_latam_only", "confirmed_no_scopes"])
def test_a_region_holding_a_confirmed_country_admits_in_every_representation(
    request, fixture: str, region: str
) -> None:
    """eligible_countries=[BR]: WORLDWIDE, AMERICAS, South America and LATAM
    all hold Brazil, and none of them needs to be ticked to say so."""
    config = request.getfixturevalue(fixture)
    for kind, (result, gate) in _three_representations(config, region).items():
        assert gate.result is GateResult.PASS, (region, kind, gate.reason)
        assert result.eligibility_status is EligibilityStatus.VERIFIED_ELIGIBLE, (region, kind)


@pytest.mark.parametrize(
    ("kind", "statement"),
    [
        ("structured", {"location": "Remote - Europe"}),
        ("prose", {"body": "You can work from anywhere in Europe."}),
        ("declared", {"declared": "Europe"}),
        ("structured", {"location": "Remote - EMEA"}),
    ],
)
def test_a_european_only_scope_refuses_a_confirmed_brazil_candidate(
    confirmed_latam_only, kind: str, statement: dict
) -> None:
    """Explicit negative evidence is kept: Europe provably excludes Brazil."""
    result, gate = _geography(confirmed_latam_only, **statement)
    assert gate.result is GateResult.FAIL, (kind, gate.reason)
    assert result.eligibility_status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    assert "does not include" in (gate.reason or "")


@pytest.mark.parametrize("region", sorted(_BROAD_REGIONS_HOLDING_BRAZIL))
def test_no_reason_says_a_region_holding_brazil_does_not_include_brazil(
    confirmed_latam_only, brazil_bare, region: str
) -> None:
    """The old refusal printed a false sentence about geography. Whatever the
    verdict -- PASS with Brazil confirmed, UNRESOLVED on residence alone --
    no representation may claim a region that holds Brazil excludes it."""
    for config in (confirmed_latam_only, brazil_bare):
        for kind, (_, gate) in _three_representations(config, region).items():
            assert gate.result is not GateResult.FAIL, (region, kind, gate.reason)
            assert "does not include" not in (gate.reason or ""), (region, kind, gate.reason)


@pytest.mark.parametrize("region", sorted(_BROAD_REGIONS_HOLDING_BRAZIL))
def test_residence_alone_never_admits_a_broad_region(brazil_bare, region: str) -> None:
    """candidate_country=BR, eligible_countries=[], no scopes. Living in Brazil
    is not proof that an employer hiring across a region can hire her."""
    for kind, (result, gate) in _three_representations(brazil_bare, region).items():
        assert gate.result is GateResult.UNRESOLVED, (region, kind, gate.reason)
        assert result.eligibility_status is EligibilityStatus.UNRESOLVED, (region, kind)


def test_residence_alone_still_matches_a_directly_named_home_country(brazil_bare) -> None:
    """The direct-country rule is unchanged: an employer naming Brazil named her."""
    result, gate = _geography(brazil_bare, location="Remote - Brazil")
    assert gate.result is GateResult.PASS, gate.reason
    assert result.eligibility_status is EligibilityStatus.VERIFIED_ELIGIBLE


def test_residence_is_not_promoted_into_eligible_countries(brazil_bare) -> None:
    """Nothing in the verdict writes back to the configuration."""
    _geography(brazil_bare, location="Remote - Worldwide")
    assert brazil_bare.eligibility.eligible_countries == []
    assert brazil_bare.eligibility.candidate_country == "BR"


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Remote - LATAM", GateResult.PASS),
        ("Remote - South America", GateResult.PASS),
        ("Remote - Worldwide", GateResult.UNRESOLVED),
        ("Remote - Americas", GateResult.UNRESOLVED),
    ],
)
def test_a_selected_scope_holding_her_residence_still_admits(
    brazil, location: str, expected: GateResult
) -> None:
    """Kept from before: a region she explicitly SELECTED as including her,
    that provably holds where she lives, admits. That is her answer plus
    geography, not residence alone -- so an unselected WORLDWIDE stays
    unknown for the same person."""
    config = _with_geography(brazil, residence="BR", countries=[], scopes=["LATAM"])
    _, gate = _geography(config, location=location)
    assert gate.result is expected, (location, gate.reason)


def test_a_selected_scope_that_cannot_hold_her_admits_nothing(brazil) -> None:
    """ADR-0023, unchanged: NORTH_AMERICA ticked by somebody in Brazil is a
    settings mistake and must not open `Remote (United States | Canada)`."""
    config = _with_geography(
        brazil, residence="BR", countries=["BR"], scopes=["NORTH_AMERICA", "WORLDWIDE"]
    )
    _, gate = _geography(config, location="Remote (United States | Canada)")
    assert gate.result is GateResult.FAIL, gate.reason


@pytest.mark.parametrize("country", ["MK", "XK"])
@pytest.mark.parametrize("location", ["Remote - EMEA", "Remote - Europe"])
def test_a_country_the_gazetteer_cannot_place_stays_unresolved(
    brazil, country: str, location: str
) -> None:
    """North Macedonia and Kosovo are not in `places.yaml`, so `region_contains`
    answers None -- "nobody here knows" -- and that must not become a refusal."""
    from career_agent.match.places import region_contains

    assert region_contains("EMEA", country) is None, "the fixture needs an unplaced country"
    config = _with_geography(brazil, residence=country, countries=[country], scopes=["EMEA"])
    _, gate = _geography(config, location=location)
    assert gate.result is GateResult.UNRESOLVED, (country, location, gate.reason)


def test_one_unknown_country_keeps_a_region_unknown_when_the_other_is_excluded(brazil) -> None:
    """BR is provably outside EMEA; MK is unknown. `any()` over a list holding
    False and None is falsy, and that is exactly how None used to be read as
    "no". Refusal needs EVERY known country provably excluded."""
    from career_agent.match.gates import _region_verdict
    from career_agent.match.places import region_contains

    assert region_contains("EMEA", "BR") is False
    assert region_contains("EMEA", "MK") is None
    config = _with_geography(brazil, residence="BR", countries=["BR", "MK"], scopes=[])
    assert _region_verdict(config, "EMEA") is None
    _, gate = _geography(config, location="Remote - EMEA")
    assert gate.result is GateResult.UNRESOLVED, gate.reason


def test_an_unplaced_country_is_still_refused_by_a_list_that_omits_it(brazil) -> None:
    """Control: an exhaustive country list is an answer without the gazetteer."""
    config = _with_geography(brazil, residence="MK", countries=["MK"], scopes=["EMEA"])
    _, gate = _geography(config, location="Remote (Germany, France)")
    assert gate.result is GateResult.FAIL, gate.reason


def test_an_incompatible_country_list_still_refuses_a_confirmed_candidate(
    confirmed_latam_only,
) -> None:
    """Control: `Remote (United States | Canada)` names two countries, not her."""
    _, gate = _geography(confirmed_latam_only, location="Remote (United States | Canada)")
    assert gate.result is GateResult.FAIL, gate.reason


def test_a_missing_hiring_scope_is_still_unresolved(confirmed_latam_only) -> None:
    """Control: silence is not permission, whatever her settings say."""
    result, gate = _geography(confirmed_latam_only)
    assert gate.result is GateResult.UNRESOLVED, gate.reason
    assert result.eligibility_status is EligibilityStatus.UNRESOLVED


def test_search_fit_does_not_move_with_the_geography_verdict(
    confirmed_latam_only, brazil_bare
) -> None:
    """The same posting PASSES geography for one candidate and stays UNRESOLVED
    for the other. Search Fit reads components and penalties, never the gate,
    so the score and every component must be identical."""
    admitted, admitted_gate = _geography(confirmed_latam_only, location="Remote - Worldwide")
    unknown, unknown_gate = _geography(brazil_bare, location="Remote - Worldwide")
    assert admitted_gate.result is GateResult.PASS
    assert unknown_gate.result is GateResult.UNRESOLVED
    assert admitted.match_score == unknown.match_score
    assert admitted.components == unknown.components


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
