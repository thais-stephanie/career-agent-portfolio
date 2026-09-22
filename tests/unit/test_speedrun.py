"""The Speedrun adapter: pagination, normalisation, and what it must never do.

Every test here runs offline against `httpx.MockTransport` or against the
payloads recorded in `tests/fixtures/providers/speedrun/`, which were captured
from the live API. Nothing in this file opens a socket.

The fixtures are real responses rather than invented ones on purpose. A
hand-written fixture tests the adapter against the author's belief about the
API; a recorded one tests it against the API.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import FetchError, FetchErrorCategory, HttpFetcher
from career_agent.providers.base import BoardRef, ProviderKind
from career_agent.providers.speedrun import (
    API_BASE,
    DECLARED_PAGE_SIZE,
    MAX_PAGE,
    SOURCE_TAG,
    FeedPage,
    SpeedrunProvider,
    origin_kind,
    origin_url,
    read_compensation,
    recognise_posting_url,
    validate_contract,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "speedrun"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


BOARD = BoardRef(company_slug="abridge", provider="speedrun", board_identifier="Abridge")


def serving(handler: Any) -> SpeedrunProvider:
    """A provider whose every request is answered by `handler`."""
    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_seconds=0.0,
        max_attempts=2,
        backoff_seconds=0.0,
        sleep=lambda _s: None,
    )
    return SpeedrunProvider(fetcher)


def paged(pages: list[dict[str, Any]]) -> Any:
    """A handler that serves `pages[n]` for `?page=n`, clamping like the real API."""

    def handler(request: httpx.Request) -> httpx.Response:
        raw = request.url.params.get("page", "0")
        index = min(int(raw), len(pages) - 1)
        body = dict(pages[index])
        body["page"] = index
        return httpx.Response(200, json=body)

    return handler


def page_body(entries: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    body = {
        "jobs": entries,
        "total": 500,
        "page": 0,
        "page_size": DECLARED_PAGE_SIZE,
        "total_pages": 10,
        "source": SOURCE_TAG,
    }
    body.update(overrides)
    return body


def entry(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "228d2bfe-cada-406a-a4c0-99c4ec13a242",
        "title": "Staff IT Engineer",
        "company": "Abridge",
        "company_slug": "abridge",
        "company_url": "https://speedrun-talent-network.com/companies/abridge",
        "url": "https://speedrun-talent-network.com/jobs/staff-it-engineer-abridge-228d2bfe",
        "location": "SF Office",
        "workplace_type": "Hybrid",
        "employment_type": "FullTime",
        "function": "engineering",
        "seniority": "staff",
        "remote": False,
        "comp_min": 200000,
        "comp_max": 240000,
        "comp_currency": "USD",
        "comp_period": None,
        "published_at": "2026-09-05T01:23:24.024+00:00",
        "stealth": False,
        "cohort": None,
        "tier": "a16z",
    }
    base.update(overrides)
    return base


# =========================================================================
# what this adapter is
# =========================================================================


def test_it_declares_itself_an_aggregator_not_an_ats() -> None:
    """The whole provenance story hangs off this one attribute.

    An aggregator republishes text the employer did not write in that form. If
    this said ATS, `access_method_for` would tell a person on every card that
    they are looking at the employer's own record.
    """
    assert SpeedrunProvider.kind is ProviderKind.AGGREGATOR


def test_the_access_method_says_aggregator() -> None:
    from career_agent.storage.mvp_repo import ACCESS_METHOD_AGGREGATOR, access_method_for

    assert access_method_for("speedrun") == ACCESS_METHOD_AGGREGATOR


def test_the_ats_providers_are_still_called_ats() -> None:
    from career_agent.storage.mvp_repo import ACCESS_METHOD_ATS, access_method_for

    for name in ("ashby", "greenhouse", "lever"):
        assert access_method_for(name) == ACCESS_METHOD_ATS


def test_the_list_response_is_declared_not_to_carry_a_description() -> None:
    """A capability that lied here would store postings with no text.

    `full_description_in_list=False` is what makes the collector fetch a detail
    for every posting it intends to keep.
    """
    assert SpeedrunProvider.capabilities.full_description_in_list is False


def test_the_boolean_remote_flag_is_deliberately_not_mapped() -> None:
    """Same decision, same reason, as Ashby's `isRemote`.

    `remote` collapses hybrid into remote. Mapping it would import someone
    else's judgement as data, against the product's founding complaint.
    """
    paths = SpeedrunProvider.field_map.paths()
    assert "workplace_type" in paths
    assert "remote" not in paths


# =========================================================================
# it never writes, and it never sends anything about the candidate
# =========================================================================


WRITE_ACTIONS = ("join_network", "express_interest", "join-network", "express-interest")


def test_no_write_action_is_named_anywhere_in_the_adapter() -> None:
    """The consent-required actions must not appear even as a string.

    Not a substitute for the request-level test below; a second lock on the
    same door, and the one that catches somebody adding a helper "for later".
    """
    source = (
        Path(__file__).resolve().parents[2] / "src" / "career_agent" / "providers" / "speedrun.py"
    ).read_text(encoding="utf-8")
    # The module docstring names them in order to say they are never called.
    body = source.split('"""', 2)[2]
    for action in WRITE_ACTIONS:
        assert action not in body, f"{action} appears in adapter code"


def test_every_request_this_adapter_makes_is_a_get_to_a_read_path() -> None:
    """Drive the whole surface and inspect what actually went out.

    This is the assertion that matters, because it tests behaviour rather than
    text: list a board, walk a feed, fetch a posting, validate the contract,
    and then look at every request that was issued.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/openapi.json"):
            return httpx.Response(200, json=fixture("openapi"))
        if "/jobs/" in request.url.path:
            return httpx.Response(200, json={"job": fixture("job_details")["jobs"][0]})
        return httpx.Response(200, json=page_body([entry()], total_pages=1))

    provider = serving(handler)
    validate_contract(provider._fetcher)
    list(provider.list_postings(BOARD))
    provider.walk_feed(scope="portfolio", max_pages=1)
    stub = provider._to_stub(entry())
    assert stub is not None
    provider.fetch_posting(BOARD, stub)

    assert seen, "the probe issued no requests at all"
    for request in seen:
        assert request.method == "GET", f"{request.method} {request.url}"
        assert request.url.path.startswith("/api/v1/"), request.url.path
        for action in WRITE_ACTIONS:
            assert action not in str(request.url)
        assert not request.content, "a GET carried a body"


def test_no_candidate_data_reaches_the_api() -> None:
    """Only the documented query parameters are ever sent.

    The failure this guards against is a future convenience: passing the
    person's preferences through as filters would put their job search, and
    eventually their profile, into somebody else's request log.
    """
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json=page_body([entry()], total_pages=1))

    provider = serving(handler)
    provider.walk_feed(scope="everywhere", max_pages=1)
    list(provider.list_postings(BOARD))

    allowed = {"page", "source", "scope", "company"}
    for url in seen:
        assert set(url.params.keys()) <= allowed, f"unexpected parameter in {url}"
        assert url.params.get("source") == SOURCE_TAG


def test_the_attribution_tag_is_sent_on_every_request() -> None:
    """The developer page asks callers to self-identify; this is that promise."""
    provider = serving(lambda _r: httpx.Response(200, json=page_body([], total_pages=1)))
    assert f"source={SOURCE_TAG}" in provider.jobs_url(page=0)
    assert f"source={SOURCE_TAG}" in provider.detail_url("abc")


# =========================================================================
# pagination
# =========================================================================


def test_it_walks_every_page_until_the_feed_ends() -> None:
    pages = [
        page_body([entry(id=f"id-{n}-{i}") for i in range(3)], total_pages=3, total=9)
        for n in range(3)
    ]
    walk = serving(paged(pages)).walk_feed(scope="portfolio")
    assert len(walk.pages) == 3
    assert walk.retrieved == 9
    assert walk.stopped_early is False


def test_a_page_limit_stops_the_walk_and_says_so() -> None:
    """A bounded run must be distinguishable from a complete one."""
    pages = [
        page_body([entry(id=f"id-{n}-{i}") for i in range(2)], total_pages=5, total=10)
        for n in range(5)
    ]
    walk = serving(paged(pages)).walk_feed(scope="portfolio", max_pages=2)
    assert len(walk.pages) == 2
    assert walk.retrieved == 4
    assert walk.stopped_early is True


def test_the_silent_clamp_above_page_200_ends_the_walk() -> None:
    """The measured behaviour that would otherwise be an infinite loop.

    The live API answers any page above 200 with HTTP 200 carrying page 200
    again, echoing `page: 200`. A walk that trusted `total_pages` would re-read
    that page until it ran out of patience and would report the same fifty
    postings hundreds of times.
    """
    requested: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked = int(request.url.params.get("page", "0"))
        requested.append(asked)
        served = min(asked, 2)
        return httpx.Response(
            200,
            json=page_body(
                [entry(id=f"clamped-{i}") for i in range(2)],
                page=served,
                total_pages=900,
                total=45000,
            ),
        )

    walk = serving(handler).walk_feed(scope="everywhere")
    assert requested == [0, 1, 2, 3], "the walk must stop at the first clamped answer"
    assert len(walk.pages) == 4


def test_an_empty_page_ends_the_walk_without_being_an_error() -> None:
    """HTTP 200 with no jobs is an answer, not a failure. Same rule as a board.

    It IS a truncation when the feed's own `total_pages` says there was more,
    which is what `truncated` records: the walk still stops, and the run still
    says so rather than reporting a complete pass. See the test below.
    """
    walk = serving(lambda _r: httpx.Response(200, json=page_body([], total_pages=9))).walk_feed()
    assert walk.retrieved == 0
    assert len(walk.pages) == 1


def test_a_walk_that_stops_on_an_empty_page_mid_feed_says_it_was_truncated() -> None:
    """Finding 6 of an independent functional review.

    This API is documented in `speedrun.py` as returning intermittent 500s, so
    a 200 carrying `jobs: []` on page 3 of 40 is a thing that happens. The walk
    ended there and reported:

        retrieved: 150, claimed_total: 4000, beyond_reach: 0,
        stopped_early: False, failures: []   ->  PipelineRunStatus.OK

    That is a failure reading as an empty result, one level above the fetcher,
    and `FeedWalk`'s own docstring names it as the thing the class exists to
    prevent.
    """
    pages = {
        0: [entry(), entry(id="b")],
        1: [entry(id="c")],
        2: [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(dict(request.url.params).get("page", 0))
        return httpx.Response(
            200, json=page_body(pages.get(page, []), total=400, total_pages=8, page=page)
        )

    walk = serving(handler).walk_feed()
    assert walk.truncated is True
    assert walk.stopped_early is False, "a bound we chose and a truncation we did not are different"
    assert walk.as_dict()["truncated"] is True


def test_a_walk_that_reads_the_whole_feed_is_not_called_truncated() -> None:
    """The other side of it, so `truncated` cannot just be always True."""

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(dict(request.url.params).get("page", 0))
        return httpx.Response(
            200, json=page_body([entry()] if page < 2 else [], total=2, total_pages=2, page=page)
        )

    walk = serving(handler).walk_feed()
    assert walk.truncated is False


def test_a_page_limit_is_a_bound_rather_than_a_truncation() -> None:
    """`--max-pages 1` is a deliberate stop and must not read as a defect."""
    walk = serving(
        lambda _r: httpx.Response(200, json=page_body([entry()], total=400, total_pages=8, page=0))
    ).walk_feed(max_pages=1)
    assert walk.stopped_early is True
    assert walk.truncated is False


def test_a_walk_reports_what_the_api_will_not_serve() -> None:
    """The anti-silent-truncation number.

    "We retrieved everything" and "we retrieved 100 of 48,129" are different
    outcomes and the caller has to be able to tell them apart.
    """
    walk = serving(
        lambda _r: httpx.Response(
            200, json=page_body([entry()], total=48129, total_pages=963, page=0)
        )
    ).walk_feed(scope="everywhere", max_pages=1)
    assert walk.claimed_total == 48129
    assert walk.servable_total == (MAX_PAGE + 1) * DECLARED_PAGE_SIZE
    assert walk.beyond_reach == 48129 - (MAX_PAGE + 1) * DECLARED_PAGE_SIZE
    assert walk.as_dict()["beyond_reach"] == walk.beyond_reach


def test_a_small_scope_has_nothing_beyond_reach() -> None:
    walk = serving(
        lambda _r: httpx.Response(200, json=page_body([entry()], total=153, total_pages=1))
    ).walk_feed(scope="speedrun")
    assert walk.beyond_reach == 0


def test_a_failure_is_raised_and_never_returned_as_an_empty_page() -> None:
    """The distinction the whole fetcher exists to preserve.

    A 500 that survived its retries must not look like a feed with no jobs.
    """
    provider = serving(lambda _r: httpx.Response(500, json={"error": {"code": "internal"}}))
    with pytest.raises(FetchError) as caught:
        provider.walk_feed(scope="portfolio", max_pages=1)
    assert caught.value.category is FetchErrorCategory.RETRY_EXHAUSTED


def test_a_body_that_is_not_the_expected_envelope_is_a_failure() -> None:
    provider = serving(lambda _r: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(FetchError) as caught:
        provider.walk_feed(max_pages=1)
    assert caught.value.category is FetchErrorCategory.MALFORMED


def test_has_more_is_false_once_the_declared_last_page_is_read() -> None:
    def at(page_number: int) -> FeedPage:
        return FeedPage(
            url="x",
            jobs=(entry(),),
            page=page_number,
            page_size=50,
            total=150,
            total_pages=3,
            echoed_source=None,
        )

    # Pages are zero-based, so page 2 of `total_pages: 3` is the last one.
    assert at(1).has_more(1) is True
    assert at(2).has_more(2) is False


def test_a_page_that_echoes_a_different_number_ends_the_walk() -> None:
    """The clamp detector, isolated from the walk that uses it."""
    clamped = FeedPage(
        url="x",
        jobs=(entry(),),
        page=200,
        page_size=50,
        total=48129,
        total_pages=963,
        echoed_source=None,
    )
    assert clamped.has_more(201) is False
    assert clamped.has_more(200) is False  # the ceiling itself is also the end


# =========================================================================
# normalisation
# =========================================================================


def test_a_listing_entry_becomes_a_stub_with_no_description() -> None:
    provider = serving(lambda _r: httpx.Response(200, json=page_body([])))
    stub = provider._to_stub(entry())
    assert stub is not None
    assert stub.external_id == "228d2bfe-cada-406a-a4c0-99c4ec13a242"
    assert stub.title == "Staff IT Engineer"
    assert stub.location_raw == "SF Office"
    # `function` is the coarse org unit this source exposes.
    assert stub.department == "engineering"
    assert stub.posted_at == "2026-09-05T01:23:24+00:00"
    assert not stub.description_html


def test_the_posted_date_is_normalised_to_the_shared_contract() -> None:
    """Offset-aware, UTC, second resolution, 25 characters. Same as every provider."""
    provider = serving(lambda _r: httpx.Response(200, json=page_body([])))
    stub = provider._to_stub(entry(published_at="2026-03-01T08:15:00.500-05:00"))
    assert stub is not None
    assert stub.posted_at == "2026-03-01T13:15:00+00:00"
    assert len(stub.posted_at) == 25


def test_an_entry_missing_its_identity_is_skipped_not_fabricated() -> None:
    provider = serving(lambda _r: httpx.Response(200, json=page_body([])))
    assert provider._to_stub(entry(id=None)) is None
    assert provider._to_stub(entry(title="")) is None
    assert provider._to_stub(entry(url=None)) is None


def test_a_missing_optional_field_becomes_none_not_an_empty_string() -> None:
    provider = serving(lambda _r: httpx.Response(200, json=page_body([])))
    stub = provider._to_stub(entry(location=None, function=None, published_at=None))
    assert stub is not None
    assert stub.location_raw is None
    assert stub.department is None
    assert stub.posted_at is None


def test_fetching_a_posting_reads_the_detail_endpoint_and_keeps_its_text() -> None:
    detail = fixture("job_details")["jobs"][0]
    provider = serving(lambda _r: httpx.Response(200, json={"job": detail}))
    stub = provider._to_stub(entry())
    assert stub is not None
    posting = provider.fetch_posting(BOARD, stub)
    assert posting.has_description
    assert posting.description_text == detail["description_text"]
    # The DETAIL payload is archived, not the listing entry: it is a strict
    # superset, and archiving both would let two records of one posting differ.
    assert posting.payload == detail
    assert "apply" in posting.payload


def test_employer_text_keeps_its_own_punctuation() -> None:
    """The reason the punctuation gate excludes employer text.

    A real description retrieved from this feed contains an em dash. Nothing in
    this pipeline may normalise it away: evidence is verified as a contiguous
    substring of exactly these bytes.
    """
    texts = [job.get("description_text") or "" for job in fixture("job_details")["jobs"]]
    assert any(chr(0x2014) in text or chr(0x2013) in text for text in texts), (
        "no recorded description carries a long dash; if that is now true of the "
        "source, the punctuation exclusion for employer text should be revisited"
    )


def test_a_detail_response_without_a_job_key_is_a_failure() -> None:
    provider = serving(lambda _r: httpx.Response(200, json={"nope": 1}))
    stub = provider._to_stub(entry())
    assert stub is not None
    with pytest.raises(FetchError):
        provider.fetch_posting(BOARD, stub)


def test_listing_one_company_addresses_it_by_display_key() -> None:
    """Measured: `company=Abridge` returns 40 postings, `company=abridge` returns 0."""
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json=page_body([entry()], total_pages=1))

    list(serving(handler).list_postings(BOARD))
    assert seen[0].params.get("company") == "Abridge"


def test_the_board_url_points_at_the_company_profile() -> None:
    provider = serving(lambda _r: httpx.Response(200, json=page_body([])))
    assert (
        provider.board_url(
            BoardRef(company_slug="abridge", provider="speedrun", board_identifier="Abridge")
        )
        == "https://speedrun-talent-network.com/companies/abridge"
    )


# =========================================================================
# the origin pointer
# =========================================================================


def test_the_origin_url_is_read_from_the_apply_block() -> None:
    for job in fixture("job_details")["jobs"]:
        assert origin_url(job), "every recorded detail payload carries an apply link"
        assert origin_kind(job) in ("external", "onsite")


def test_recorded_origin_links_resolve_to_a_provider_we_already_collect() -> None:
    """The finding that makes deduplication possible at all.

    Every apply link in the recorded sample points at an ATS this project has
    an adapter for, and `identify_posting_url` turns it into an exact
    `(provider, external_id)` with no fuzzy matching anywhere.
    """
    from career_agent.providers.registry import identify_posting_url

    links = [origin_url(job) or "" for job in fixture("job_details")["jobs"]]
    resolved = [identify_posting_url(link) for link in links]
    assert all(item is not None for item in resolved), resolved
    assert {item[0] for item in resolved if item}, "no provider recognised any link"


def test_a_closed_role_has_no_apply_block_and_that_is_not_an_error() -> None:
    assert origin_url({"apply": None}) is None
    assert origin_kind({"apply": None}) is None
    assert origin_url({}) is None


def test_it_recognises_its_own_canonical_urls() -> None:
    url = "https://speedrun-talent-network.com/jobs/staff-it-engineer-abridge-228d2bfe"
    assert recognise_posting_url(url) == "228d2bfe"
    assert recognise_posting_url(url + "?utm_source=career-agent") == "228d2bfe"
    assert recognise_posting_url("https://jobs.ashbyhq.com/Abridge/x") is None


# =========================================================================
# compensation
# =========================================================================


def test_a_stated_band_is_read_with_its_currency() -> None:
    hint = read_compensation(entry())
    assert hint is not None
    assert (hint.min_value, hint.max_value) == (200000.0, 240000.0)
    assert hint.currency == "USD"


def test_a_null_period_means_annual_because_the_contract_says_so() -> None:
    """Not the absence-is-never-permission case: a documented encoding.

    The published schema says "null = annual by convention". Reading that as
    unknown would discard a number the source did state.
    """
    assert read_compensation(entry(comp_period=None)).period == "YEAR"
    assert read_compensation(entry(comp_period="month")).period == "MONTH"
    assert read_compensation(entry(comp_period="hour")).period == "HOUR"


def test_a_period_outside_the_shared_vocabulary_is_unknown_not_guessed() -> None:
    """A weekly rate compared against a monthly target is a wrong number."""
    assert read_compensation(entry(comp_period="week")).period is None
    assert read_compensation(entry(comp_period="fortnight")).period is None


def test_a_posting_with_no_pay_at_all_returns_nothing() -> None:
    assert read_compensation(entry(comp_min=None, comp_max=None, comp_summary=None)) is None


def test_an_all_zero_band_is_read_as_no_band() -> None:
    hint = read_compensation(entry(comp_min=0, comp_max=0, comp_summary="see posting"))
    assert hint is not None
    assert hint.min_value is None and hint.max_value is None


def test_this_source_never_states_more_than_one_currency() -> None:
    hint = read_compensation(entry())
    assert hint is not None
    assert hint.alternate_bands == ()


# =========================================================================
# the contract check
# =========================================================================


def test_the_recorded_specification_satisfies_what_the_adapter_reads() -> None:
    provider = serving(lambda _r: httpx.Response(200, json=fixture("openapi")))
    check = validate_contract(provider._fetcher)
    assert check.ok, check.describe()
    assert check.version == "3.1.0"
    assert check.declared_max_page == MAX_PAGE
    assert check.declared_page_size == DECLARED_PAGE_SIZE


def test_the_published_specification_contains_no_write_operation() -> None:
    """Recorded as an observation with a date, never assumed.

    This is a claim about somebody else's service, and it can stop being true
    without notice. Counting is what turns it into something we would notice.
    """
    provider = serving(lambda _r: httpx.Response(200, json=fixture("openapi")))
    assert validate_contract(provider._fetcher).write_operations == ()


def test_a_renamed_field_fails_the_contract_check_rather_than_going_quiet() -> None:
    spec = fixture("openapi")
    del spec["components"]["schemas"]["Job"]["properties"]["workplace_type"]
    provider = serving(lambda _r: httpx.Response(200, json=spec))
    check = validate_contract(provider._fetcher)
    assert not check.ok
    assert "workplace_type" in check.missing_job_fields
    assert "workplace_type" in check.describe()


def test_a_removed_path_fails_the_contract_check() -> None:
    spec = fixture("openapi")
    del spec["paths"]["/api/v1/jobs/{id}"]
    provider = serving(lambda _r: httpx.Response(200, json=spec))
    assert not validate_contract(provider._fetcher).ok


def test_an_empty_specification_is_every_field_missing_not_a_pass() -> None:
    """A check that passes on an empty document checks nothing."""
    provider = serving(lambda _r: httpx.Response(200, json={}))
    check = validate_contract(provider._fetcher)
    assert not check.ok
    assert check.missing_paths


def test_a_write_operation_appearing_in_the_specification_is_reported() -> None:
    spec = fixture("openapi")
    spec["paths"]["/api/v1/join_network"] = {"post": {"operationId": "joinNetwork"}}
    provider = serving(lambda _r: httpx.Response(200, json=spec))
    check = validate_contract(provider._fetcher)
    assert check.write_operations == ("POST /api/v1/join_network",)
    # Still ok: the adapter reads what it needs. What changed is that we would
    # now SEE the write surface appear, which is the point of counting it.
    assert check.ok


def test_the_api_base_is_the_documented_one() -> None:
    assert API_BASE == "https://speedrun-talent-network.com/api/v1"


# =========================================================================
# One adapter must never claim another vendor's posting URL
# =========================================================================


def test_no_adapter_claims_a_url_that_belongs_to_another_vendor() -> None:
    """Found by an independent functional review, with four live examples.

    Two independent looseneses compounded into one wrong answer:

      * Greenhouse tested `netloc.endswith("greenhouse.io")`, which is a
        SUFFIX test rather than a domain test, so `notgreenhouse.io` and
        `mygreenhouse.io` -- hostnames anybody can register -- were ours;
      * Ashby's and Lever's patterns rejected any query string, while the
        comments above both of them said "and any query string".

    The second is what disarmed the safety net. A Lever apply link carrying a
    stray `gh_jid` was claimed by the Greenhouse recogniser and by nobody
    else, so `identify_posting_url` saw no tie to refuse, resolved to a
    Greenhouse posting, and the collector recorded a duplicate and returned
    BEFORE rule 3 could find the right posting by URL.
    """
    from career_agent.providers.registry import identify_posting_url

    uuid = "228d2bfe-cada-406a-a4c0-99c4ec13a242"

    # A Lever URL with a Greenhouse parameter is now claimed by two adapters,
    # and a tie is refused rather than resolved to whoever answered.
    assert identify_posting_url(f"https://jobs.lever.co/site/{uuid}?gh_jid=1234567") is None

    # A hostname that merely ENDS with the vendor's domain is not the vendor.
    assert identify_posting_url("https://notgreenhouse.io/acme/jobs/1234567") is None
    assert identify_posting_url("https://mygreenhouse.io/jobs/998877") is None

    # And the URLs that really are ours still resolve, query string and all.
    assert identify_posting_url(f"https://jobs.ashbyhq.com/Abridge/{uuid}?utm=x") == (
        "ashby",
        uuid,
    )
    assert identify_posting_url(f"https://jobs.lever.co/site/{uuid}?lever-source=x") == (
        "lever",
        uuid,
    )
    assert identify_posting_url("https://boards.greenhouse.io/acme/jobs/1234567") == (
        "greenhouse",
        "1234567",
    )
    # `gh_jid` on an employer's own domain stays claimed, and it should: that
    # is an embedded Greenhouse board, and the parameter is Greenhouse's own.
    assert identify_posting_url("https://acme.com/careers?gh_jid=1234567") == (
        "greenhouse",
        "1234567",
    )
