"""The Torre adapter: built, tested, and refused before a request is sent.

Everything here runs offline, and for Torre that is not a testing convenience
-- it is the whole design. `torre.ai/robots.txt` answers `User-agent: *` with
`Disallow: /api/`, so the collection path raises rather than fetching, and what
remains is a normaliser, an identity rule and a query builder that are complete
and reviewable today.

**The fixtures are built from the contract, not recorded**, for the same reason
Get on Board's are: no request was made. The shape comes from Career-Ops's
MIT-licensed `providers/torre.mjs`, whose measurements of the three API quirks
this module encodes rather than rediscovers.

The most important test in this file is the one asserting that a collection
attempt raises WITHOUT touching the transport. If that ever passes by fetching,
the boundary is gone.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.providers.base import BoardRef, ProviderKind, RetrievalMode
from career_agent.providers.torre import (
    DEFAULT_EXPERIENCE,
    EXPERIENCE_LEVELS,
    PAGE_SIZE,
    ROBOTS_DISALLOWS_API,
    SearchRead,
    TorreCollectionRefused,
    TorreProvider,
    TorreQueryError,
    TorreSearch,
    company_of,
    looks_unfiltered,
    recognise_posting_url,
    to_stub,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "torre"
SEARCH = json.loads((FIXTURES / "opportunities-search.json").read_text(encoding="utf-8"))
UNFILTERED = json.loads((FIXTURES / "unfiltered-catalogue.json").read_text(encoding="utf-8"))


def provider(seen: list[httpx.Request] | None = None) -> TorreProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=SEARCH)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return TorreProvider(HttpFetcher(client=client, request_delay_seconds=0, sleep=lambda _s: None))


# =========================================================================
# the boundary
# =========================================================================


def test_the_recorded_observation_still_says_the_api_is_disallowed() -> None:
    """A canary on the gate itself.

    If somebody flips `ROBOTS_DISALLOWS_API` this test fails, which is the
    point: unblocking Torre should be a deliberate, reviewed change with this
    assertion updated in the same commit, never a quiet edit.
    """
    assert ROBOTS_DISALLOWS_API is True


def test_collecting_raises_without_opening_a_socket() -> None:
    """THE test. The refusal must happen before a request, not after one fails."""
    seen: list[httpx.Request] = []
    board = BoardRef(company_slug="", provider="torre", board_identifier="automation")

    with pytest.raises(TorreCollectionRefused):
        list(provider(seen).list_postings(board))

    assert seen == [], "a request was sent despite the refusal"


def test_searching_raises_without_opening_a_socket() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(TorreCollectionRefused):
        provider(seen).search(TorreSearch(text="workflow automation"))
    assert seen == []


def test_the_refusal_names_the_line_it_is_honouring() -> None:
    """A refusal nobody can act on is an outage. This one carries its reason."""
    with pytest.raises(TorreCollectionRefused) as raised:
        provider().search(TorreSearch(text="revenue operations"))
    message = str(raised.value)
    assert "torre.ai" in message
    assert "Disallow: /api/" in message
    assert "2026-09-07" in message
    assert "search.torre.co" in message


def test_reading_an_archived_payload_is_not_refused() -> None:
    """A rescore must still read rows already stored.

    Refusing here would make stored Torre rows unreadable, which is a different
    thing from not collecting new ones.
    """
    stub = to_stub(SEARCH["results"][0])
    assert stub is not None
    raw = provider().fetch_posting(
        BoardRef(company_slug="", provider="torre", board_identifier="x"), stub
    )
    assert raw.payload == SEARCH["results"][0]


# =========================================================================
# declaration
# =========================================================================


def test_coverage_is_a_property_of_the_questions_asked() -> None:
    """Twenty results per search is a ceiling, never the LATAM market."""
    assert TorreProvider.retrieval_mode is RetrievalMode.QUERY_DRIVEN
    assert TorreProvider.kind is ProviderKind.AGGREGATOR


def test_it_does_not_claim_to_publish_a_hiring_scope() -> None:
    """`locations` is where a posting says it is, not where it may hire."""
    assert TorreProvider.capabilities.publishes_hiring_scope is False


def test_it_does_not_claim_a_description() -> None:
    assert TorreProvider.capabilities.obtains_full_description is False


# =========================================================================
# the query
# =========================================================================


def test_the_required_experience_companion_is_always_emitted() -> None:
    """Omitting it is a hard 500, invisible to a mocked test and fatal live."""
    body = TorreSearch(text="workflow automation").body()
    assert body["skill/role"]["experience"] == DEFAULT_EXPERIENCE
    assert body["skill/role"]["text"] == "workflow automation"


def test_an_empty_search_is_refused_rather_than_sent() -> None:
    """An empty body returns the entire unfiltered catalogue with a 200.

    That reads as a successful broad result rather than as a missing filter,
    which is the worst possible failure mode.
    """
    with pytest.raises(TorreQueryError):
        TorreSearch(text="   ")


@pytest.mark.parametrize("bad", ["senior", "1-year", "", "5-plus-year"])
def test_an_unrecognised_experience_is_refused_locally(bad: str) -> None:
    """The API refuses it server-side; refusing here saves a wasted request."""
    with pytest.raises(TorreQueryError, match="experience"):
        TorreSearch(text="automation", experience=bad)


@pytest.mark.parametrize("level", sorted(EXPERIENCE_LEVELS))
def test_every_documented_experience_level_is_accepted(level: str) -> None:
    assert (
        TorreSearch(text="automation", experience=level).body()["skill/role"]["experience"] == level
    )


def test_remote_only_expresses_the_positive_case_only() -> None:
    """`{"remote": {"term": false}}` is not a verified filter.

    Quirk 1 says an unverified filter is silently ignored, so a falsy value
    sends no key rather than one that looks effective and is not.
    """
    assert "remote" not in TorreSearch(text="ops", remote_only=False).body()
    assert TorreSearch(text="ops", remote_only=True).body()["remote"] == {"term": True}


def test_a_search_is_derived_from_work_rather_than_from_a_title() -> None:
    """Invariant 7 applied to retrieval.

    Nothing enforces this at the type level -- a title is a string like any
    other -- so this is the documented intent with a worked example, and the
    reason `TorreSearch` takes `text` rather than `role`.
    """
    search = TorreSearch(text="workflow automation integrations")
    assert search.body()["skill/role"]["text"] == "workflow automation integrations"


# =========================================================================
# the twenty-result ceiling, and the ignored-filter trap
# =========================================================================


def test_a_full_result_set_is_reported_as_a_ceiling_not_as_an_answer() -> None:
    read = SearchRead(
        search=TorreSearch(text="ops"),
        opportunities=tuple({"id": f"id{n:04d}"} for n in range(PAGE_SIZE)),
        claimed_total=39_771,
    )
    assert read.ceiling_reached is True


def test_a_short_result_set_is_not_a_ceiling() -> None:
    read = SearchRead(
        search=TorreSearch(text="ops"),
        opportunities=tuple(SEARCH["results"][:3]),
        claimed_total=39_771,
    )
    assert read.ceiling_reached is False


def test_a_catalogue_sized_total_is_flagged_as_a_probably_ignored_filter() -> None:
    """Quirk 1 cannot be seen in the twenty rows. The total is the only tell."""
    assert looks_unfiltered(UNFILTERED["total"]) is True
    assert looks_unfiltered(SEARCH["total"]) is False
    assert looks_unfiltered(None) is False


def test_the_suspicion_is_surfaced_on_the_read_rather_than_logged() -> None:
    """A caller that cannot see it will present the whole board as a search."""
    read = SearchRead(
        search=TorreSearch(text="ops"),
        opportunities=(),
        claimed_total=UNFILTERED["total"],
    )
    assert read.filter_suspected_ignored is True


# =========================================================================
# normalisation
# =========================================================================


def test_an_opportunity_becomes_a_stub_with_a_built_url() -> None:
    """The URL is BUILT from a validated id, never taken from the payload.

    A payload URL is attacker-controlled input this product renders as a link.
    """
    stub = to_stub(SEARCH["results"][0])
    assert stub is not None
    assert stub.external_id == "NwBp2Axr"
    assert stub.url == "https://torre.ai/post/NwBp2Axr"
    assert stub.title == "Revenue Operations Manager"


def test_remote_is_not_folded_into_the_location() -> None:
    """Unlike the upstream normaliser, and deliberately.

    Invariant 3 keeps work model and geography apart, and `remote` already has
    its own field mapping. Writing it into the location too would let one
    assertion be counted twice by two different readers.
    """
    stub = to_stub(SEARCH["results"][0])
    assert stub is not None
    assert stub.location_raw == "Colombia, Mexico"
    assert "Remote" not in (stub.location_raw or "")


def test_a_closed_posting_is_dropped() -> None:
    assert to_stub(SEARCH["results"][2]) is None


def test_an_absent_status_is_treated_as_open() -> None:
    """The field is on every observed row; a missing one must not empty the feed."""
    stub = to_stub(SEARCH["results"][5])
    assert stub is not None
    assert stub.external_id == "NoStatus1"


def test_an_id_that_could_inject_a_path_is_refused() -> None:
    assert to_stub(SEARCH["results"][4]) is None


def test_an_unparseable_date_stays_absent() -> None:
    stub = to_stub(SEARCH["results"][5])
    assert stub is not None
    assert stub.posted_at is None


def test_a_real_date_is_normalised_to_rfc3339_utc() -> None:
    stub = to_stub(SEARCH["results"][0])
    assert stub is not None
    assert stub.posted_at == "2026-09-01T10:15:00+00:00"


def test_a_solo_poster_gets_no_invented_employer() -> None:
    """Naming that company "Torre" would assert an employer this adapter made up."""
    assert company_of(SEARCH["results"][0]) == "Torre Labs"
    assert company_of(SEARCH["results"][1]) is None


def test_a_blank_organisation_name_is_skipped_for_the_next_real_one() -> None:
    assert company_of(SEARCH["results"][5]) == "Second Org"


# =========================================================================
# identity and deduplication
# =========================================================================


def test_a_torre_permalink_resolves_to_an_exact_provider_and_id() -> None:
    """Deterministic cross-source identity: no fuzzy matching anywhere near it."""
    from career_agent.providers.registry import identify_posting_url

    assert identify_posting_url("https://torre.ai/post/NwBp2Axr") == ("torre", "NwBp2Axr")


def test_the_recogniser_returns_exactly_what_to_stub_stores() -> None:
    """The contract `_URL_RECOGNISERS` depends on: byte-identical ids."""
    stub = to_stub(SEARCH["results"][0])
    assert stub is not None
    assert recognise_posting_url(stub.url) == stub.external_id


@pytest.mark.parametrize(
    "url",
    [
        "https://torre.ai/post/",
        "https://torre.ai/post/ab",
        "https://torre.ai/search/jobs",
        "https://evil.example/post/NwBp2Axr",
        "https://torre.ai/post/NwBp2Axr/extra",
    ],
)
def test_the_recogniser_claims_nothing_that_is_not_its_own(url: str) -> None:
    assert recognise_posting_url(url) is None


def test_a_repeated_row_in_one_response_is_normalised_once() -> None:
    """The fixture repeats `NwBp2Axr`. One search, one row.

    Not ADR-0013 deduplication: this is one response carrying one posting
    twice, resolved by exact vendor id.
    """
    stubs = TorreProvider.normalise(tuple(SEARCH["results"]))
    ids = [s.external_id for s in stubs]
    assert ids == ["NwBp2Axr", "Kq7Rt1Zv", "NoStatus1"]
    assert len(ids) == len(set(ids))


def test_normalisation_is_deterministic_over_an_archived_payload() -> None:
    """What makes a rescore reconstruct rather than re-derive."""
    first = TorreProvider.normalise(tuple(SEARCH["results"]))
    second = TorreProvider.normalise(tuple(json.loads(json.dumps(SEARCH["results"]))))
    assert first == second


# =========================================================================
# provenance
# =========================================================================


def test_the_whole_opportunity_is_archived_verbatim() -> None:
    stub = to_stub(SEARCH["results"][0])
    assert stub is not None
    assert stub.payload == SEARCH["results"][0]


def test_the_human_search_route_stays_open() -> None:
    """`BLOCKED` is about automated API access, never about a person browsing."""
    url = provider().board_url(BoardRef(company_slug="", provider="torre", board_identifier="x"))
    assert url.startswith("https://torre.ai/")


# =========================================================================
# the path behind the gate
# =========================================================================
#
# These are what make "built and switched off" a true sentence rather than a
# flattering one. They set `ROBOTS_DISALLOWS_API` to False for the duration of
# one test, through monkeypatch, and drive the REAL request path against the
# fixture.
#
# Without them this adapter would be STUBBED and switched off, and the owner
# who ever flips that flag would discover the difference at the worst possible
# moment. A gate in front of an empty room is not a gate.


@pytest.fixture
def unblocked(monkeypatch):
    """Torre, as it would behave if the access question were answered yes.

    Deliberately a fixture rather than a module-level flip: the default must
    stay refused for every other test in this file, and the canary above must
    keep failing if anyone changes the real constant.
    """
    monkeypatch.setattr("career_agent.providers.torre.ROBOTS_DISALLOWS_API", False)


def test_unblocked_it_sends_exactly_one_request(unblocked) -> None:
    """Quirk 2: no pagination form advances, so a loop could only refetch."""
    seen: list[httpx.Request] = []
    provider(seen).search(TorreSearch(text="workflow automation"))
    assert len(seen) == 1


def test_unblocked_the_request_carries_the_required_experience(unblocked) -> None:
    """Omitting it is a hard 500, and only a live call would ever show that."""
    seen: list[httpx.Request] = []
    provider(seen).search(TorreSearch(text="revenue operations"))
    body = json.loads(seen[0].content.decode())
    assert body["skill/role"]["text"] == "revenue operations"
    assert body["skill/role"]["experience"] == DEFAULT_EXPERIENCE


def test_unblocked_it_posts_to_the_documented_endpoint_at_the_capped_size(unblocked) -> None:
    seen: list[httpx.Request] = []
    provider(seen).search(TorreSearch(text="ops"))
    assert seen[0].method == "POST"
    assert str(seen[0].url).startswith("https://search.torre.co/opportunities/_search")
    assert f"size={PAGE_SIZE}" in str(seen[0].url)


def test_unblocked_the_read_carries_the_total_and_the_unaddressable_count(unblocked) -> None:
    read = provider().search(TorreSearch(text="ops"))
    assert read.claimed_total == 39_771
    assert read.filter_suspected_ignored is False
    # The fixture holds one row with an unusable id and one closed posting.
    assert read.unaddressable == 2


def test_unblocked_a_catalogue_sized_total_is_flagged_on_the_read(unblocked) -> None:
    """The ignored-filter trap, end to end rather than on a constructed object."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=UNFILTERED)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    prov = TorreProvider(HttpFetcher(client=client, request_delay_seconds=0, sleep=lambda _s: None))
    read = prov.search(TorreSearch(text="ops"))
    assert read.filter_suspected_ignored is True


def test_unblocked_an_unexpected_shape_raises_rather_than_reading_zero(unblocked) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"opportunities": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    prov = TorreProvider(HttpFetcher(client=client, request_delay_seconds=0, sleep=lambda _s: None))
    with pytest.raises(ValueError, match="unexpected shape"):
        prov.search(TorreSearch(text="ops"))


def test_unblocked_list_postings_yields_deduplicated_stubs(unblocked) -> None:
    """The search text is the board identifier. Torre has no boards."""
    board = BoardRef(company_slug="", provider="torre", board_identifier="workflow automation")
    stubs = list(provider().list_postings(board))
    assert [s.external_id for s in stubs] == ["NwBp2Axr", "Kq7Rt1Zv", "NoStatus1"]


def test_the_gate_closes_again_after_the_fixture(unblocked) -> None:
    """A guard on the fixture itself: monkeypatch must not leak between tests.

    If it did, every refusal test in this file would silently start fetching.
    """
    assert provider().search(TorreSearch(text="ops")) is not None
