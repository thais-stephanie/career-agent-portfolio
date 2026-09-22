"""The Get on Board adapter, against fixtures BUILT FROM THE CONTRACT.

**These fixtures are not recorded responses, and that difference is stated
rather than glossed.** Every other provider fixture in this repository is a
real response trimmed down, because a recorded fixture tests the adapter
against the feed while a hand-written one tests it against the author's belief
about the feed. That is the better kind and it is not available here.

`www.getonbrd.com/robots.txt` names `ClaudeBot` with `Disallow: /`, and the
agent that wrote this adapter is Claude. So the fixtures below are constructed
from the JSON:API contract in `providers/getonbrd.py` -- the endpoint, the
`expand[]=company` parameter and the field names, all read from Career-Ops's
MIT-licensed implementation rather than from a live call.

**What that means for these tests, honestly.** They prove the adapter behaves
correctly GIVEN that shape. They cannot prove the shape. The owner's first
bounded retrieval is what settles that, and it is the moment to replace these
with recorded ones.

Nothing here opens a socket.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.providers.base import ProviderKind, RetrievalMode
from career_agent.providers.getonbrd import (
    DEFAULT_CATEGORIES,
    MAX_CATEGORIES,
    PER_PAGE,
    CategoryError,
    GetonbrdProvider,
    company_of,
    public_url,
    resolve_categories,
    to_stub,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "getonbrd"
PAGE1 = json.loads((FIXTURES / "category-programming-page1.json").read_text(encoding="utf-8"))
MALFORMED = json.loads((FIXTURES / "malformed-rows.json").read_text(encoding="utf-8"))
EMPTY = json.loads((FIXTURES / "empty-category.json").read_text(encoding="utf-8"))


def _fixture_attributes(external_id: str) -> dict:
    """One fixture row's attributes, by the vendor id it carries."""
    for resource in PAGE1["data"]:
        if resource["id"] == external_id:
            return dict(resource["attributes"])
    raise AssertionError(f"no fixture row {external_id!r}")


def provider(
    body: object = PAGE1,
    *,
    status: int = 200,
    seen: list[httpx.Request] | None = None,
    max_pages: int | None = None,
) -> GetonbrdProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GetonbrdProvider(
        HttpFetcher(client=client, request_delay_seconds=0, sleep=lambda _s: None),
        max_pages=max_pages,
    )


# -- declaration -----------------------------------------------------------


def test_it_is_an_aggregator_feed_not_an_ats() -> None:
    """The canonical URL is the board's, so the text is not the employer's record."""
    assert GetonbrdProvider.kind is ProviderKind.AGGREGATOR
    assert GetonbrdProvider.retrieval_mode is RetrievalMode.AGGREGATOR_FEED


def test_a_category_is_not_a_company() -> None:
    """`board_identifier` names a feed, so a company slug is the wrong question."""
    assert GetonbrdProvider.addresses_boards_by_company is False


def test_it_does_not_claim_to_publish_a_hiring_scope() -> None:
    """The single most consequential declaration in the adapter.

    `countries` is where a posting says it is. Reading it as the employer's
    answer to "where may I hire" is the conflation invariant 3 forbids, and the
    asymmetry decides it: a false PASS shows a job she cannot take.
    """
    assert GetonbrdProvider.capabilities.publishes_hiring_scope is False


def test_it_claims_the_description_the_owners_retrieval_proved_exists() -> None:
    """Corrected 2026-09-07, against 409 postings the owner actually collected.

    The adapter shipped declaring False on both, and said in its own docstring
    that the first live retrieval finding bodies would make this a two-line
    change. It found them. What made the earlier reading wrong is that this
    vendor splits one posting across five fields, so reading the key named
    `description` returned a requirements list that looked like an excerpt.
    """
    assert GetonbrdProvider.capabilities.full_description_in_list is True
    assert GetonbrdProvider.capabilities.obtains_full_description is True


# -- categories ------------------------------------------------------------


def test_the_default_categories_are_not_just_programming() -> None:
    """The board splits leadership, data and ML OUT of `programming`.

    A default of `programming` alone would make a candidate whose work is
    automation, operations and integrations largely invisible to this source,
    which is invariant 7 losing to a convenient default.
    """
    assert "programming" in DEFAULT_CATEGORIES
    assert len(DEFAULT_CATEGORIES) > 1
    assert "operations-management" in DEFAULT_CATEGORIES


@pytest.mark.parametrize(
    "bad",
    ["../admin", "Programming", "a b", "prog?x=1", "//evil.example", "", "trailing-", 42],
)
def test_an_invalid_category_is_refused_loudly(bad: object) -> None:
    """A typo that silently reads a different feed is worse than one that stops.

    The first produces a plausible smaller corpus nobody questions. `None` is
    not in this list because it is not a bad value: it means "unconfigured" and
    takes the default set, which the test below asserts.
    """
    with pytest.raises(CategoryError):
        resolve_categories([bad])


def test_unconfigured_takes_the_default_set() -> None:
    assert resolve_categories(None) == DEFAULT_CATEGORIES


def test_categories_deduplicate_and_keep_configuration_order() -> None:
    assert resolve_categories(["data-science-analytics", "programming", "programming"]) == (
        "data-science-analytics",
        "programming",
    )


def test_too_many_categories_is_refused() -> None:
    """Each costs up to max_pages requests on somebody else's server."""
    with pytest.raises(CategoryError, match="cap"):
        resolve_categories([f"cat-{n}" for n in range(MAX_CATEGORIES + 1)])


# -- URL construction ------------------------------------------------------


def test_the_feed_url_carries_the_company_expansion() -> None:
    url = provider().feed_url("programming", 1)
    assert url.startswith("https://www.getonbrd.com/api/v0/categories/programming/jobs?")
    assert "expand[]=company" in url
    assert f"per_page={PER_PAGE}" in url
    assert "page=1" in url


def test_the_host_is_asserted_on_every_built_url() -> None:
    """The second gate. The slug pattern is the first; neither alone suffices."""
    hostile = GetonbrdProvider(
        HttpFetcher(
            client=httpx.Client(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []}))
            )
        ),
        api_host="https://evil.example",
    )
    with pytest.raises(CategoryError, match="untrusted"):
        hostile.feed_url("programming", 1)


# -- normalisation ---------------------------------------------------------


def test_a_row_becomes_a_stub_with_its_own_identity() -> None:
    stub = to_stub(PAGE1["data"][0])
    assert stub is not None
    assert stub.external_id == "40219"
    assert stub.title == "Revenue Operations Analyst"
    assert stub.url == "https://www.getonbrd.com/jobs/revenue-operations-analyst-fintual"


def test_the_country_list_is_joined_and_remote_is_not_folded_in() -> None:
    """Invariant 3: work model and geography are separate questions.

    Writing "Remote" into the location too would let one assertion be counted
    twice by two different readers.
    """
    stub = to_stub(PAGE1["data"][0])
    assert stub is not None
    assert stub.location_raw == "Chile, Argentina"
    assert "Remote" not in (stub.location_raw or "")


def test_published_at_is_read_as_epoch_seconds() -> None:
    """Seconds, not milliseconds, pinned to the exact instant.

    The shared normaliser takes MILLISECONDS, which is what Lever's epoch is,
    and this vendor states SECONDS. Getting the factor of a thousand wrong does
    not raise: it silently puts every posting in 1970 or in the far future,
    which then reads as a freshness result rather than as a parsing bug. So the
    assertion is the whole timestamp rather than its shape.
    """
    stub = to_stub(PAGE1["data"][0])
    assert stub is not None
    assert stub.posted_at == "2026-09-07T00:00:00+00:00"


def test_an_absent_date_stays_absent() -> None:
    """A date this adapter cannot read is an absent date, never today's."""
    stub = to_stub(PAGE1["data"][2])
    assert stub is not None
    assert stub.posted_at is None


def test_the_embedded_company_is_read_and_never_invented() -> None:
    assert company_of(PAGE1["data"][0]) == "Fintual"
    assert company_of(PAGE1["data"][1]) == "Nubank"
    # `company.data` is null: no employer, and no placeholder either.
    assert company_of(PAGE1["data"][2]) is None


def test_an_empty_country_list_is_no_location_not_an_empty_string() -> None:
    stub = to_stub(PAGE1["data"][2])
    assert stub is not None
    assert stub.location_raw is None


# -- untrusted input -------------------------------------------------------


@pytest.mark.parametrize("index", [0, 1, 2, 3, 4])
def test_unaddressable_rows_are_dropped_rather_than_half_invented(index: int) -> None:
    """No id, off-host URL, non-HTTPS, blank title, missing id: all dropped.

    Counting them is more honest than inventing the missing half.
    """
    assert to_stub(MALFORMED["data"][index]) is None


def test_an_off_host_public_url_is_refused() -> None:
    """A payload URL is untrusted input this product renders as a clickable link."""
    assert public_url(MALFORMED["data"][1]) is None
    assert public_url(MALFORMED["data"][2]) is None


def test_a_string_country_is_tolerated_and_a_negative_date_is_not_invented() -> None:
    stub = to_stub(MALFORMED["data"][5])
    assert stub is not None
    assert stub.location_raw == "Peru"
    assert stub.posted_at is None


# -- walking ---------------------------------------------------------------


def test_a_short_page_ends_the_walk_in_one_request() -> None:
    """Three rows against a page size of 100 is the end of the feed."""
    seen: list[httpx.Request] = []
    read = provider(seen=seen).read_category("programming")
    assert len(seen) == 1
    assert len(read.resources) == 3
    assert read.walk.complete is True


def test_an_empty_category_is_a_successful_read_and_not_a_failure() -> None:
    """An empty feed must never be a reason to close jobs."""
    read = provider(EMPTY).read_category("programming")
    assert read.resources == ()
    assert read.walk.truncated is False


def test_a_failed_request_raises_and_never_becomes_an_empty_feed() -> None:
    """An empty board and a broken request are different outcomes.

    Conflating them lets a network blip close a company's posting history.
    """
    with pytest.raises(FetchError):
        provider(status=503).read_category("programming")


def test_an_unexpected_shape_raises_rather_than_reading_zero_postings() -> None:
    with pytest.raises(ValueError, match="unexpected shape"):
        provider({"jobs": []}).read_category("programming")


def test_the_claimed_total_is_recorded_and_never_used_as_a_stop_condition() -> None:
    read = provider().read_category("programming")
    assert read.walk.claimed_total == 3


def test_the_page_budget_bounds_the_walk_and_says_so() -> None:
    """A full page means there is more; the budget is why we stopped."""
    full = {
        "data": [dict(PAGE1["data"][0], id=str(n)) for n in range(PER_PAGE)],
        "meta": {"total": 9999},
    }
    seen: list[httpx.Request] = []
    read = provider(full, seen=seen, max_pages=2).read_category("programming")
    assert len(seen) == 2
    assert read.walk.hit_page_limit is True
    assert read.walk.complete is False


# -- deduplication ---------------------------------------------------------


def test_a_posting_listed_twice_in_one_walk_is_yielded_once() -> None:
    """Not ADR-0013 deduplication: this is one feed repeating one row."""
    doubled = {"data": [PAGE1["data"][0], PAGE1["data"][0], PAGE1["data"][1]], "meta": {"total": 3}}
    from career_agent.providers.base import BoardRef

    stubs = list(
        provider(doubled).list_postings(
            BoardRef(company_slug="", provider="getonbrd", board_identifier="programming")
        )
    )
    assert [s.external_id for s in stubs] == ["40219", "40220"]


# -- provenance ------------------------------------------------------------


def test_the_whole_resource_is_archived_verbatim() -> None:
    """Later stages must be able to ask what the provider actually returned."""
    stub = to_stub(PAGE1["data"][0])
    assert stub is not None
    assert stub.payload == PAGE1["data"][0]


def test_fetch_posting_makes_no_second_request_and_still_stores_the_body() -> None:
    """One page carries the whole posting, so there is nothing to ask twice for."""
    from career_agent.providers.base import BoardRef

    seen: list[httpx.Request] = []
    prov = provider(seen=seen)
    board = BoardRef(company_slug="", provider="getonbrd", board_identifier="programming")
    stub = next(iter(prov.list_postings(board)))
    before = len(seen)

    raw = prov.fetch_posting(board, stub)

    assert len(seen) == before
    assert raw.has_description is True
    assert "revenue reporting pipeline" in raw.description_text
    assert "4+ years in revenue or sales operations" in raw.description_text
    assert raw.payload == stub.payload


def test_a_posting_with_a_body_is_full_content_and_one_without_is_still_metadata_only() -> None:
    """Two inputs, and neither is inferred from the other.

    The adapter answers whether the whole description is obtainable; the JOB
    answers whether any text was stored. A source that supplies everything
    still holds nothing for a row collected before its body arrived.
    """
    from career_agent.domain.enums import ContentCompleteness
    from career_agent.providers.registry import content_completeness_for

    assert content_completeness_for("getonbrd", has_text=True) is ContentCompleteness.FULL_CONTENT
    assert content_completeness_for("getonbrd", has_text=False) is ContentCompleteness.METADATA_ONLY


def test_the_composed_body_leaves_the_companys_own_marketing_out() -> None:
    """The V1.2 defect, at the one place it could re-enter.

    `projects` is company and product prose -- "from anywhere in the world" is
    exactly the sentence that made 598 postings wrongly eligible. It stays in
    the archived payload and out of the text the matcher scores.
    """
    from career_agent.providers.getonbrd import compose_description

    attributes = _fixture_attributes("40219")
    html, text = compose_description(attributes)

    assert "anywhere in the world" in str(attributes["projects"])
    assert "anywhere in the world" not in html
    assert "anywhere in the world" not in text


def test_the_composition_is_ordered_and_headed_by_the_employers_own_words() -> None:
    """Deterministic order, and no heading this system wrote.

    A rescore rebuilds the body from the archived payload, so the order may
    never come from iterating a dict. And a heading absent from the payload
    produces no heading at all rather than one invented here.
    """
    from career_agent.providers.getonbrd import compose_description

    text = compose_description(_fixture_attributes("40219"))[1]
    assert text.index("Funciones del cargo") < text.index("Requerimientos indispensables")
    assert text.index("Requerimientos indispensables") < text.index("Deseable")

    # 40220 states no `description_headline`, and none is supplied for it.
    without = compose_description(_fixture_attributes("40220"))[1]
    assert "Experiencia com integracoes" in without
    assert "Requerimientos" not in without


def test_a_posting_with_no_section_at_all_composes_to_nothing() -> None:
    """Absence stays absence. An empty body is not an empty string dressed up."""
    from career_agent.providers.getonbrd import compose_description

    assert compose_description(_fixture_attributes("40221")) == ("", "")
    assert compose_description({}) == ("", "")
    assert compose_description(None) == ("", "")


# -- rescore reconstruction ------------------------------------------------


def test_normalisation_is_deterministic_over_an_archived_payload() -> None:
    """The same payload must yield the same stub, every time, with no clock.

    This is what makes a rescore reconstruct rather than re-derive: nothing in
    the path reads the current time or any state outside the payload.
    """
    first = to_stub(PAGE1["data"][0])
    second = to_stub(json.loads(json.dumps(PAGE1["data"][0])))
    assert first == second
