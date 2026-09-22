"""The twelve filters section 16 of the corrective report listed as absent.

Section 16 said: "Not implemented: country/region, LatAm/worldwide
availability, hybrid/onsite as distinct from remote, seniority, employment
type, minimum salary, salary currency/period, technologies, preferred/excluded
keywords as filters."

This module is the counter-claim, and it is deliberately paranoid about the
shape of the evidence. A filter is only implemented when ALL of these hold, so
all of them are asserted:

    the database narrows          count() < the unfiltered count
    the page agrees               len(page()) matches count() where it can
    the API parses it             a query string reaches `JobFilter`
    the API validates it          a wrong value is a 400, not an empty list
    the facets are real           a control has counts to render
    the counts are accurate       single-valued facets sum to count()
    combinations work             two filters narrow further than one
    clearing works                removing it restores the population

The last one matters more than it looks. `?totally_unknown=x` returning the
whole corpus is what made the previous round's filters look broken when they
worked; a filter that silently does nothing and a filter that correctly matches
everything are indistinguishable from the outside unless something checks.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from collections.abc import Iterator

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.mvp_repo import SET_VALUED_FACETS
from career_agent.web.api import JOB_QUERY_PARAMS, JobsApi
from career_agent.web.server import ApiError, ServerConfig

#: The committed configuration, copied without the owner's private
#: overrides. A test that read `search.local.yaml` would pass or fail on
#: what is in one person's gitignored file. See `tests/support.py`.
CONFIG_DIR = committed_config_dir()
DEMO_FILE = pathlib.Path("evaluation") / "demo" / "demo_postings.yaml"

#: Every parameter this module claims to have implemented, with a value that
#: matches SOMETHING in the demo corpus and a value that matches nothing.
#:
#: Written as data rather than as twelve near-identical test functions, because
#: the interesting property is "every one of them behaves the same way" and a
#: table makes an omission visible. A parameter added to `JOB_QUERY_PARAMS` and
#: not to this table fails `test_every_accepted_parameter_is_covered_here`.
NARROWING: dict[str, str] = {
    "country": "BR",
    "region": "LATAM",
    "latam_only": "1",
    "worldwide_only": "1",
    "worksite": "REMOTE",
    "seniority": "SENIOR",
    "employment_type": "CONTRACT",
    "salary_currency": "BRL",
    "salary_period": "MONTH",
    "technology": "ipaas",
    "keyword": "lead routing",
    "exclude_keyword": "lead routing",
    # Migration 0027. All three narrow, including `experience_max_years`: it
    # asks for the postings that STATED a minimum at or below the figure, and a
    # posting that stated none does not answer the question. Absence is never
    # permission, pointed at a number instead of at a country.
    "experience_requirement": "NOT_STATED",
    "experience_max_years": "2",
    "entry_signal": "ENTRY_LEVEL",
}

#: `min_salary` is separate: it is the one parameter that is INVALID alone,
#: because nothing in this system converts between currencies.
SALARY_PAIR = "min_salary=100000&salary_currency=USD"

#: Values the API must refuse rather than answer with an empty list.
REFUSED: dict[str, str] = {
    "country": "ZZ",
    "region": "MIDDLE_EARTH",
    "worksite": "nowhere",
    "seniority": "ARCHMAGE",
    "technology": "no_such_signal",
    # A word that is not in either vocabulary. "No rows" and "that is not a
    # value this product has" are different statements a person has to be able
    # to tell apart, which is the same rule every closed vocabulary here
    # follows.
    "experience_requirement": "SOMEWHAT",
    "entry_signal": "VIBES",
}


@pytest.fixture(scope="module")
def api() -> Iterator[JobsApi]:
    """A real API over a freshly seeded demo corpus. No network, no fixtures."""
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="filter-contract")) / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.DEMO, "demo")
        config, _ = load_search_config(CONFIG_DIR)
        seed_demo(conn, config, source=DEMO_FILE)
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR, port=0), quiet=True)


def _query(qs: str) -> dict[str, list[str]]:
    from urllib.parse import parse_qs

    return parse_qs(qs, keep_blank_values=True)


def _jobs(api: JobsApi, qs: str = "") -> dict:
    return api.handle_api("GET", "/api/jobs", _query(qs), {})


@pytest.fixture(scope="module")
def total(api: JobsApi) -> int:
    return int(_jobs(api)["total"])


# =========================================================================
# The table is complete
# =========================================================================


def test_every_accepted_parameter_is_covered_by_a_test_here() -> None:
    """A parameter the API accepts and nothing exercises is a parameter that
    can stop working in silence. The presentation and paging keys are named
    explicitly rather than skipped by pattern, so adding one is a decision."""
    presentation = {"sort", "direction", "limit", "offset", "group_duplicates"}
    # THE SOFT PAIR IS NOT A FILTER, and belongs here rather than in
    # `NARROWING` for the reason that table exists: every entry in it is
    # asserted to satisfy `0 < count < whole`, and these two are built so that
    # `count == whole` ALWAYS. A preference that narrowed would be the defect.
    #
    # They are exercised in `tests/integration/test_keyword_chips.py`, which
    # asserts the opposite property: the page order moves and no count does.
    ordering = {"prefer_keyword", "avoid_keyword"}
    older = {
        "search",
        "company",
        "provider",
        "role_class",
        "status",
        "eligibility",
        "fit_band",
        "signal",
        "min_score",
        "max_score",
        "min_confidence",
        "saved_only",
        "enriched_only",
        "has_salary",
        "remote_only",
        "posted_within_days",
        # Migration 0020. Exercised by `test_employment_context.py`, which is
        # where the property worth protecting lives: the context reading and
        # the eligibility verdict are different questions and a query for one
        # may never return the other.
        "employment_context",
        "contract_regime",
        # Migration 0022. Exercised by `test_partial_content.py`, which is
        # where the property worth protecting lives: an excerpt is recorded
        # and queryable, and it moves no number.
        "content_completeness",
        # Migration 0021. Exercised by `test_user_hidden.py`, which is where
        # the property worth protecting lives: what SHE hid is a different
        # population from what an employer ruled out and from what her search
        # set aside, and no query for one may answer another.
        "user_hidden_only",
    }
    covered = set(NARROWING) | {"min_salary"} | presentation | ordering | older | set(WIDENING)
    assert covered >= JOB_QUERY_PARAMS, sorted(JOB_QUERY_PARAMS - covered)


#: The parameters that make the list LONGER, and the count each puts back.
#:
#: None can live in `NARROWING`, whose test asserts `0 < n < total`: these are
#: only meaningful when the result EXCEEDS the default population.
#:
#: Three, not one, and they are separate on purpose. `include_ineligible` puts
#: back postings whose own text rules this person out; `include_off_target`
#: puts back work this person's own search set aside; `include_user_hidden`
#: puts back the ones she read and set aside herself. An employer rejecting
#: somebody, a search rejecting a kind of work and a person closing a card are
#: three different sentences, and a single toggle covering the first two was
#: the conflation that once printed a rejection notice on three postings that
#: had no blockers at all.
#:
#: `include_user_hidden` carries no count here because nothing in the fixture
#: is hidden until a test hides it; `test_user_hidden.py` owns that half.
#: Every widening at once, for the tests whose subject is a FILTER rather
#: than the default view. Written as a query fragment because that is how
#: `_jobs` takes it here.
WIDEN_ALL = "include_ineligible=1&include_off_target=1&include_unresolved=1"

WIDENING = {
    "include_ineligible": "1",
    "include_off_target": "1",
    # Added 2026-09-07. The posting never said where the employer hires, which
    # is neither a rejection nor a recommendation -- a fourth sentence, and a
    # fourth control.
    "include_unresolved": "1",
    # The fourth, and the only PREFERENCE among them. It reveals nothing here
    # because the committed example excludes no level -- which is the default
    # and is the point: "not preferred" is not "prohibited".
    "include_excluded_seniority": "1",
    "include_user_hidden": "1",
    # The fifth, and the only one whose effect depends on what has been
    # CONFIRMED rather than on the request. It reveals nothing on this fixture
    # because nothing is confirmed there, which is the control correctly doing
    # nothing: there is no evidence to transfer yet.
    "include_transferable": "1",
}


@pytest.mark.parametrize(
    ("parameter", "reported"),
    [
        ("include_ineligible", "hidden_by_eligibility"),
        ("include_unresolved", "hidden_unresolved"),
        ("include_off_target", "hidden_off_target"),
    ],
)
def test_a_widening_filter_puts_back_exactly_what_was_reported_hidden(
    api: JobsApi, parameter: str, reported: str
) -> None:
    """Every other filter here removes rows. These put rows back.

    And the number they put back must be exactly the number the response said
    was hidden. A narrowing that is on by default and reports a different count
    from what it hides is a silent filter wearing a label.

    **The count may legitimately be zero, and then nothing comes back.** With
    three narrowings the populations overlap: every off-target posting in this
    fixture is also unresolved or ineligible, so its own control reveals none
    of them on its own. What must hold either way is the EQUALITY -- the
    button puts back exactly what the number promised, including when the
    number is nought.
    """
    default = _jobs(api)
    widened = _jobs(api, f"{parameter}=1")

    assert widened["total"] - default["total"] == default[reported]
    assert widened[reported] == 0


def test_the_two_widenings_are_independent(api: JobsApi) -> None:
    """Asking for one must not reveal the other.

    The failure this prevents is the interface saying "show me work I set
    aside" and silently also showing postings that rule the person out.
    """
    both = _jobs(api, qs="include_ineligible=1&include_off_target=1")
    only_ineligible = _jobs(api, qs="include_ineligible=1")
    only_off_target = _jobs(api, qs="include_off_target=1")

    assert both["total"] > only_ineligible["total"]
    assert both["total"] > only_off_target["total"]
    assert "VERIFIED_NOT_ELIGIBLE" not in {
        i["eligibility_status"] for i in only_off_target["items"]
    }
    assert "BLOCKED" not in {i["screening_state"] for i in only_ineligible["items"]}


def test_the_default_recommends_only_what_it_can_vouch_for(api: JobsApi) -> None:
    """A DELIBERATE REVERSAL, 2026-09-07.

    This asserted that silence survives the default, on the reasoning that
    hiding an unresolved posting turns a missing sentence into a rejection.
    That is right about what UNRESOLVED means and wrong about what a default
    list is: on the real corpus 12,574 of 19,469 open postings are unresolved,
    and mixed into best matches they buried the ones she can take.

    Silence keeps its meaning, its own count and its own control. It leaves
    the list whose whole claim is "these are your best matches".
    """
    shown = {item["eligibility_status"] for item in _jobs(api)["items"]}
    assert "VERIFIED_NOT_ELIGIBLE" not in shown
    assert "UNRESOLVED" not in shown

    # And one click away, in full.
    assert "UNRESOLVED" in {
        item["eligibility_status"] for item in _jobs(api, qs="include_unresolved=1")["items"]
    }


def test_the_default_hides_off_target_work_without_hiding_a_low_score(api: JobsApi) -> None:
    """A hard conflict is not the same as a weak match.

    `screening_state` is the search saying "this is not the kind of work".
    A low score is the search saying "not much of it". Only the first hides,
    and the second must survive -- a product that hid weak matches would be
    deciding for somebody what is worth their attention.
    """
    default = _jobs(api)
    assert "BLOCKED" not in {item["screening_state"] for item in default["items"]}
    assert any(
        item["match_score"] is not None and item["match_score"] < 40 for item in default["items"]
    ), "every weak match disappeared, which is not what this hides"


def test_the_twelve_are_accepted_at_all() -> None:
    # Parenthesised: `-` binds tighter than `|`, so the unbracketed version
    # subtracted from `{"min_salary"}` alone and reported all twelve missing.
    missing = sorted((set(NARROWING) | {"min_salary"}) - JOB_QUERY_PARAMS)
    assert not missing, f"section 16's filters are still absent: {missing}"


# =========================================================================
# Each one, on its own
# =========================================================================


@pytest.mark.parametrize(("name", "value"), sorted(NARROWING.items()))
def test_each_filter_narrows_and_returns_the_rows_it_counted(
    name: str, value: str, api: JobsApi
) -> None:
    """Narrower than everything, wider than nothing, and the page agrees.

    `0 < n < total` is the assertion that separates a working filter from the
    two ways of failing invisibly: matching everything (which is what an
    ignored parameter does) and matching nothing (which is what an unvalidated
    value does).
    """
    # Asked against the WHOLE corpus rather than the default view.
    #
    # The default sets three populations aside, and after 2026-09-07 what
    # survives them is small and homogeneous: every posting the demo can vouch
    # for is remote, so `worksite=REMOTE` matched 10 of 10 and this test failed
    # for a reason that had nothing to do with the filter. A filter's contract
    # is about the corpus it queries, so it is exercised against all of it.
    payload = _jobs(api, f"{name}={value.replace(' ', '+')}&{WIDEN_ALL}")
    count = int(payload["total"])
    whole = int(_jobs(api, WIDEN_ALL)["total"])
    assert 0 < count < whole, f"{name}={value} returned {count} of {whole}"
    assert len(payload["items"]) == min(count, payload["limit"])


def test_min_salary_narrows_when_it_is_given_its_currency(api: JobsApi, total: int) -> None:
    payload = _jobs(api, SALARY_PAIR)
    assert 0 < int(payload["total"]) < total


def test_a_band_is_matched_on_its_TOP_not_its_floor(api: JobsApi) -> None:
    """Asking for "at least 100,000" must include a 95,000-125,000 posting.

    The band reaches the figure. Filtering on the floor would hide every range
    that straddles the number a person typed, which is most of them.
    """
    payload = _jobs(api, qs="min_salary=100000&salary_currency=USD")
    bands = [item["salary"] for item in payload["items"] if item["salary"]]
    assert any(
        band["min"] is not None and band["min"] < 100000 <= (band["max"] or 0) for band in bands
    ), "no straddling band survived, so the floor is being compared"


def test_a_monthly_figure_is_compared_as_a_year(api: JobsApi) -> None:
    """6,000 a month is 72,000 a year, so it clears 50,000 and not 100,000."""
    assert int(_jobs(api, qs="min_salary=50000&salary_currency=USD")["total"]) > int(
        _jobs(api, qs="min_salary=100000&salary_currency=USD")["total"]
    )


# =========================================================================
# A wrong value is a refusal, never an empty list
# =========================================================================


@pytest.mark.parametrize(("name", "value"), sorted(REFUSED.items()))
def test_a_value_outside_the_vocabulary_is_refused(name: str, value: str, api: JobsApi) -> None:
    """ "No results" and "that is not a value this system has" are different
    statements, and a person has to be able to tell them apart."""
    with pytest.raises(ApiError) as error:
        _jobs(api, f"{name}={value}")
    assert error.value.status == 400


def test_a_country_alias_is_accepted_and_normalised(api: JobsApi) -> None:
    """`UK` is not a country code -- `GB` is -- and a person typing the first
    must not get silence. Same for the Portuguese spelling of Brazil."""
    assert _jobs(api, qs="country=brasil")["total"] == _jobs(api, qs="country=BR")["total"]
    assert _jobs(api, qs="country=UK")["total"] == _jobs(api, qs="country=GB")["total"]


def test_a_minimum_salary_without_a_currency_is_refused_with_the_reason(
    api: JobsApi,
) -> None:
    """Nothing here converts between currencies, so a bare figure would compare
    USD to BRL to INR silently. Refusing names the missing half."""
    with pytest.raises(ApiError) as error:
        _jobs(api, qs="min_salary=100000")
    assert error.value.status == 400
    assert "currency" in str(error.value).lower()


def test_a_negative_minimum_salary_is_refused(api: JobsApi) -> None:
    with pytest.raises(ApiError) as error:
        _jobs(api, qs="min_salary=-5&salary_currency=USD")
    assert error.value.status == 400


def test_an_absurd_number_of_keyword_phrases_is_refused(api: JobsApi) -> None:
    """Each phrase is a search clause, so the list is bounded."""
    with pytest.raises(ApiError) as error:
        _jobs(api, qs="&".join(f"keyword=phrase{n}" for n in range(20)))
    assert error.value.status == 400


# =========================================================================
# Clearing, and combinations
# =========================================================================


@pytest.mark.parametrize(("name", "value"), sorted(NARROWING.items()))
def test_removing_a_filter_restores_the_population(
    name: str, value: str, api: JobsApi, total: int
) -> None:
    """The other half of "it narrows": it has to stop narrowing when removed.

    Cheap, and it is the assertion that would have caught a filter wired to a
    stale copy of the state instead of to the query string.
    """
    _jobs(api, f"{name}={value.replace(' ', '+')}")
    assert int(_jobs(api)["total"]) == total


def test_two_filters_narrow_further_than_either_alone(api: JobsApi) -> None:
    both = int(_jobs(api, qs="region=LATAM&worksite=REMOTE")["total"])
    assert both <= int(_jobs(api, qs="region=LATAM")["total"])
    assert both <= int(_jobs(api, qs="worksite=REMOTE")["total"])
    assert both > 0


def test_a_representative_combination_of_six_still_answers(api: JobsApi) -> None:
    """Six at once, spanning every mechanism the twelve use: a fenced set, a
    scalar column, an inequality over the annualised band, the membership
    index, and a free-text phrase. If the conditional joins forgot a table this
    is where it raises."""
    payload = _jobs(
        api,
        "country=BR&worksite=REMOTE&employment_type=PJ&technology=api_integration"
        "&keyword=integra&min_salary=1000&salary_currency=BRL",
    )
    assert int(payload["total"]) >= 0
    assert "facets" in payload


def test_two_values_of_one_filter_are_an_OR(api: JobsApi, total: int) -> None:
    """Within a dimension, values widen; across dimensions, they narrow."""
    br = int(_jobs(api, qs="country=BR")["total"])
    us = int(_jobs(api, qs="country=US")["total"])
    both = int(_jobs(api, qs="country=BR&country=US")["total"])
    assert both == br + us, "a posting cannot be in both, in this corpus"
    assert both <= total


def test_the_two_keyword_lists_are_complementary(api: JobsApi, total: int) -> None:
    """Must-mention and must-not-mention partition the corpus between them."""
    wanted = int(_jobs(api, qs="keyword=lead+routing")["total"])
    unwanted = int(_jobs(api, qs="exclude_keyword=lead+routing")["total"])
    assert wanted + unwanted == total


def test_the_shorthands_agree_with_the_region_they_stand_for(api: JobsApi) -> None:
    assert _jobs(api, qs="latam_only=1")["total"] == _jobs(api, qs="region=LATAM")["total"]
    assert _jobs(api, qs="worldwide_only=1")["total"] == _jobs(api, qs="region=WORLDWIDE")["total"]


# =========================================================================
# Facets: real counts, for real controls
# =========================================================================

#: Facets whose buckets are mutually exclusive, so they must sum to the result
#: count. `country`, `region`, `signal` and `technology` are SETS -- one posting
#: can be in two buckets -- so they are excluded here and checked differently.
SINGLE_VALUED = (
    "company",
    "provider",
    "title_class",
    "eligibility_status",
    "status",
    "fit_band",
    "worksite",
    "seniority",
    "employment_type",
    "salary_currency",
    "salary_period",
)


@pytest.mark.parametrize("dimension", SINGLE_VALUED)
def test_a_single_valued_facet_sums_to_the_result_count(dimension: str, api: JobsApi) -> None:
    payload = _jobs(api)
    assert sum(payload["facets"][dimension].values()) == payload["total"]


@pytest.mark.parametrize(
    "dimension", ["worksite", "seniority", "employment_type", "salary_currency", "salary_period"]
)
def test_every_new_facet_has_something_to_render(dimension: str, api: JobsApi) -> None:
    """A control with no counts is a control nobody can click.

    The panel offered a technology group for a `signal` facet the server never
    produced, so it only appeared once a value was already chosen -- which is
    the definition of a decorative control.
    """
    assert _jobs(api)["facets"][dimension], f"{dimension} has no buckets"


@pytest.mark.parametrize("dimension", sorted(SET_VALUED_FACETS))
def test_every_set_facet_has_something_to_render(dimension: str, api: JobsApi) -> None:
    assert _jobs(api)["facets"][dimension], f"{dimension} has no buckets"


def test_a_facet_bucket_is_a_value_its_own_filter_accepts(api: JobsApi) -> None:
    """The chip a person clicks and the value the API receives are one string.

    Including `NOT_STATED`, which is stored as NULL and offered as a word --
    the round trip is exactly where that translation could go wrong.
    """
    facets = _jobs(api)["facets"]
    for dimension, parameter in (
        ("worksite", "worksite"),
        ("seniority", "seniority"),
        ("employment_type", "employment_type"),
        ("salary_currency", "salary_currency"),
        ("salary_period", "salary_period"),
        ("country", "country"),
        ("region", "region"),
        ("technology", "technology"),
    ):
        for bucket, expected in facets[dimension].items():
            got = int(_jobs(api, f"{parameter}={bucket}")["total"])
            assert got == expected, f"{parameter}={bucket}: chip said {expected}, filter gave {got}"


def test_the_facets_narrow_with_the_filter(api: JobsApi) -> None:
    """Each facet counts over the FILTERED population, so applying one filter
    must shrink the others rather than leaving them describing the corpus."""
    unfiltered = _jobs(api)["facets"]
    filtered = _jobs(api, qs="region=LATAM")["facets"]
    assert sum(filtered["seniority"].values()) < sum(unfiltered["seniority"].values())


def test_a_technology_facet_is_a_subset_of_the_signal_facet(api: JobsApi) -> None:
    """Two readings of one index, so one cannot contain what the other lacks."""
    facets = _jobs(api)["facets"]
    for key, count in facets["technology"].items():
        assert facets["signal"][key] == count


# =========================================================================
# The stored row and the returned row agree
# =========================================================================


def test_a_filtered_row_actually_carries_what_it_was_filtered_by(api: JobsApi) -> None:
    """The filter reads a column; the card renders `posting_facts`. Both come
    from one `MatchResult`, and this is the assertion that they still do."""
    for item in _jobs(api, qs="worksite=REMOTE")["items"]:
        assert item["work_model"] == "REMOTE"
    for item in _jobs(api, qs="seniority=SENIOR")["items"]:
        assert item["seniority"] == "SENIOR"
    for item in _jobs(api, qs="salary_currency=BRL")["items"]:
        assert item["salary"]["currency"] == "BRL"


def test_a_country_filter_does_not_touch_eligibility(api: JobsApi) -> None:
    """Invariant 3, asserted where it would break.

    `countries` is what the BOARD PRINTED. Filtering by it must not change any
    posting's eligibility, and must not silently become a hiring-scope claim:
    a Brazilian posting whose gates are UNRESOLVED stays UNRESOLVED.
    """
    for item in _jobs(api, qs="country=BR")["items"]:
        again = api.handle_api("GET", f"/api/jobs/{item['job_id']}", {}, {})
        assert again["eligibility_status"] == item["eligibility_status"]
    statuses = {item["eligibility_status"] for item in _jobs(api, qs="country=BR")["items"]}
    assert statuses, "no Brazilian postings to check"


def test_the_unfiltered_corpus_is_not_capped(api: JobsApi) -> None:
    """`total` is the whole matching count, separate from the page.

    Named here because "17 results" was once read as a hidden cap when it was
    the demo database being served. It is asserted rather than assumed.
    """
    payload = _jobs(api, qs="limit=5")
    assert payload["total"] > 5
    assert len(payload["items"]) == 5
    everything = _jobs(api, qs="limit=500")
    assert len(everything["items"]) == everything["total"]


def test_the_response_is_json_serialisable(api: JobsApi) -> None:
    """The facets grew two dicts built in Python; they still have to survive
    the wire."""
    json.dumps(_jobs(api, qs="region=LATAM"))
