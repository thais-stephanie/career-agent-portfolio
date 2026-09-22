"""The Workable adapter, against pages captured live on 2026-09-09.

Captured live, and the difference from Remote OK's fixtures is recorded rather
than assumed: `jobs.workable.com/robots.txt` names no AI crawler and does not
mention `ClaudeBot`, so the agent maintaining these tests is a permitted reader
here and is not there.

Four themes, and each is a way this source can be wrong quietly:

* the PAGE TOKEN. The response field is `nextPageToken`, the request parameter
  is `pageToken`, and sending the first name returns page one with HTTP 200
  forever;
* the ADVERT, which arrives split across three fields, so reading the one
  literally named `description` returns something that looks complete and is
  not;
* the LOCATION, which is where the work is and never where the employer hires;
* the QUERY, which is absent by default, because a search API is the easiest
  place in this system to encode one person's preferences without noticing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_agent.providers.workable import (
    HOST,
    PAGE_TOKEN_PARAM,
    PER_PAGE,
    WorkableError,
    WorkableProvider,
    assert_trusted,
    company_of,
    company_website,
    compose_description,
    location_of,
    posted_at,
    public_url,
    strip_html,
    to_stub,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "workable"
PAGE1 = json.loads((FIXTURES / "search-page1.json").read_text(encoding="utf-8"))
PAGE2 = json.loads((FIXTURES / "search-page2.json").read_text(encoding="utf-8"))
ROWS = PAGE1["jobs"]


class _Fetcher:
    """Serves the captured pages by page token and counts what was asked for."""

    def __init__(self, pages: list[dict]) -> None:
        self._pages = pages
        self.urls: list[str] = []

    def get_json(self, url: str, use_cache: bool = True) -> dict:
        del use_cache
        self.urls.append(url)
        if f"{PAGE_TOKEN_PARAM}=" not in url:
            return self._pages[0]
        index = min(len(self.urls) - 1, len(self._pages) - 1)
        return self._pages[index]


# -- the page token, which is the trap ---------------------------------------


def test_the_request_parameter_is_not_what_the_response_calls_it() -> None:
    """`nextPageToken` comes back; `pageToken` goes out.

    Sending the response's own field name returns HTTP 200 and page one, every
    time. A first walk of twelve pages collected 240 rows holding 20 distinct
    postings, and nothing about that looked wrong from the outside.
    """
    assert PAGE_TOKEN_PARAM == "pageToken"
    assert "nextPageToken" in PAGE1

    provider = WorkableProvider(_Fetcher([PAGE1]))  # type: ignore[arg-type]
    url = provider.page_url(token="abc123")
    assert f"{PAGE_TOKEN_PARAM}=abc123" in url
    assert "nextPageToken=" not in url


def test_a_feed_that_stops_advancing_is_recorded_and_not_walked_forever() -> None:
    """The consequence of that trap, defended against rather than trusted.

    A page holding twenty postings this walk already has is what a token that
    did not take looks like from here. Walking on would spend the whole budget
    re-reading page one and report twenty-five pages of work.
    """
    provider = WorkableProvider(_Fetcher([PAGE1, PAGE1, PAGE1]), max_pages=5)  # type: ignore[arg-type]
    read = provider.read_feed()

    assert read.repeated_itself is True
    assert read.stopped_early is False, "this is the feed repeating, not our budget"
    assert len(read.records) == len(ROWS)
    assert read.pages == 2, "one page of postings, one page that added nothing"


def test_a_real_second_page_is_kept_and_the_walk_continues() -> None:
    provider = WorkableProvider(_Fetcher([PAGE1, PAGE2]), max_pages=2)  # type: ignore[arg-type]
    read = provider.read_feed()

    assert read.repeated_itself is False
    assert len(read.records) == len(ROWS) + len(PAGE2["jobs"])
    assert len({r["id"] for r in read.records}) == len(read.records)


def test_a_page_token_out_of_a_response_is_never_trusted_blindly() -> None:
    provider = WorkableProvider(_Fetcher([PAGE1]))  # type: ignore[arg-type]
    with pytest.raises(WorkableError):
        provider.page_url(token="../../etc/passwd")
    with pytest.raises(WorkableError):
        provider.page_url(token="tok en with spaces")


def test_the_page_size_is_the_vendors_ceiling_and_not_a_preference() -> None:
    """`limit=100` answers `400 {"limit":"Must be less than or equal to 20"}`."""
    assert PER_PAGE == 20
    provider = WorkableProvider(_Fetcher([PAGE1]))  # type: ignore[arg-type]
    assert f"limit={PER_PAGE}" in provider.page_url()


# -- no query is the design --------------------------------------------------


def test_the_default_walk_asks_for_nothing_in_particular() -> None:
    """THE MOST IMPORTANT TEST IN THIS FILE.

    This is a search API. A collector built around `query=customer success
    manager` would decide, at the ingestion layer, what work exists -- and the
    corpus has to serve a lawyer moving into an executive assistant role,
    somebody starting in sales, and a customer success manager in Utah, from
    the same rows.
    """
    provider = WorkableProvider(_Fetcher([PAGE1]))  # type: ignore[arg-type]
    assert "query=" not in provider.page_url()


def test_a_query_is_possible_and_has_to_be_asked_for() -> None:
    provider = WorkableProvider(_Fetcher([PAGE1]))  # type: ignore[arg-type]
    assert "query=executive%20assistant" in provider.page_url(query="executive assistant")


def test_a_day_range_narrows_by_time_rather_than_by_content() -> None:
    """The second-run tool. A fact about the feed, not about a person."""
    provider = WorkableProvider(_Fetcher([PAGE1]))  # type: ignore[arg-type]
    assert "day_range=1" in provider.page_url(day_range=1)
    with pytest.raises(WorkableError):
        provider.page_url(day_range=0)


# -- the advert --------------------------------------------------------------


def test_the_advert_is_reassembled_from_the_fields_it_arrives_split_across() -> None:
    """The Get on Board lesson, applied in advance.

    That adapter read the key literally named `description`, got a requirements
    list that reads exactly like an excerpt, and stored 409 postings as
    METADATA_ONLY for a whole milestone. This feed splits an advert three ways.
    """
    record = ROWS[0]
    composed = compose_description(record)
    assert len(composed) > len(record["description"])
    assert record["description"] in composed
    assert record["requirementsSection"] in composed


def test_the_companys_marketing_blurb_is_left_out() -> None:
    """`company.description` is identical on every posting an employer
    publishes. Folding it in makes every advert longer, more similar and no
    more informative -- which is the V1.2 defect, in a new source."""
    record = next(r for r in ROWS if (r.get("company") or {}).get("description"))
    blurb = record["company"]["description"]
    assert blurb not in compose_description(record)


def test_the_whole_advert_arrives_in_the_listing() -> None:
    assert WorkableProvider.capabilities.full_description_in_list is True
    assert WorkableProvider.capabilities.obtains_full_description is True
    assert all(len(compose_description(r)) > 500 for r in ROWS)


def test_list_items_survive_as_separate_lines() -> None:
    text = strip_html("<ul><li>Forklift licence</li><li>Nights and weekends</li></ul>")
    assert "Forklift licence" in text.splitlines()
    assert "Nights and weekends" in text.splitlines()


# -- geography ---------------------------------------------------------------


def test_a_place_of_work_is_not_a_hiring_scope() -> None:
    """The opposite of Himalayas, in the same column. What makes `Belgrade,
    Beograd, Serbia` readable at all is `workplace`, through
    `gates.structured_geography`."""
    from career_agent.domain.enums import MetadataDimension

    assert WorkableProvider.capabilities.publishes_hiring_scope is False
    assert MetadataDimension.HIRING_LOCATION_HINT not in WorkableProvider.field_map.dimensions()
    assert "location" not in WorkableProvider.field_map.paths()


def test_the_workplace_field_is_the_employers_own_structured_answer() -> None:
    from career_agent.domain.enums import MetadataDimension
    from career_agent.domain.provider_values import normalise_work_model

    assert "workplace" in WorkableProvider.field_map.paths()
    assert MetadataDimension.WORK_MODEL_HINT in WorkableProvider.field_map.dimensions()
    # `on_site` folds to `onsite`, which is why no alias had to be added.
    assert normalise_work_model("on_site") is not None
    assert normalise_work_model("remote") is not None
    assert normalise_work_model("hybrid") is not None


def test_the_three_parts_of_a_place_are_composed_and_never_deduplicated_away() -> None:
    """`CA` is California AND Canada; `San Jose` is in four countries. The
    resolver does better with the whole string than with a bare city."""
    assert location_of(
        {"location": {"city": "Houston", "subregion": "Texas", "countryName": "United States"}}
    ) == ("Houston, Texas, United States")
    # A city-state writes the same word twice and must not reach the resolver
    # as a pair.
    assert (
        location_of({"location": {"city": "Singapore", "countryName": "Singapore"}}) == "Singapore"
    )
    assert location_of({"location": {}}) is None
    assert location_of({}) is None


# -- employment type ---------------------------------------------------------


def test_an_unlabelled_or_other_employment_type_yields_nothing() -> None:
    """`Other` and an empty string both appear in the live index -- 76 empty
    and one `Other` in the first 500 rows. Neither may become a value this
    system invented."""
    from career_agent.domain.provider_values import normalise_employment_type

    assert normalise_employment_type("Full-time") == "FULL_TIME"
    assert normalise_employment_type("Part-time") == "PART_TIME"
    assert normalise_employment_type("Contract") == "CONTRACT"
    assert normalise_employment_type("Temporary") == "TEMPORARY"
    assert normalise_employment_type("Other") is None
    assert normalise_employment_type("") is None


def test_no_salary_is_read_because_none_is_stated() -> None:
    assert WorkableProvider.capabilities.exposes_compensation is False
    assert all("salary" not in key for r in ROWS for key in r)


# -- addressing and the link back --------------------------------------------


def test_every_captured_row_is_addressable() -> None:
    for record in ROWS:
        stub = to_stub(record)
        assert stub is not None
        assert stub.external_id.startswith("workable-")
        assert stub.title
        assert stub.description_html
        assert stub.posted_at and stub.posted_at.startswith("202")


@pytest.mark.parametrize("missing", ["id", "title", "url"])
def test_a_row_missing_a_required_field_is_refused(missing: str) -> None:
    record = dict(ROWS[0])
    record[missing] = None
    assert to_stub(record) is None


def test_the_apply_target_is_the_vendors_own_posting_page() -> None:
    for record in ROWS:
        url = public_url(record)
        assert url is not None
        assert url.startswith(f"https://{HOST}/view/")
    assert public_url({"url": "https://evil.example/view/1"}) is None
    assert public_url({"url": f"http://{HOST}/view/1"}) is None


def test_the_employers_own_site_is_carried_and_is_not_the_apply_target() -> None:
    """More provenance than Remote OK or Himalayas publishes, and still not an
    apply link. Sending somebody to a marketing homepage instead of to the
    advert they were reading is worse than useless."""
    record = next(r for r in ROWS if (r.get("company") or {}).get("website"))
    assert company_website(record)
    assert company_website(record) != public_url(record)


def test_nothing_off_the_host_is_ever_fetched() -> None:
    with pytest.raises(WorkableError):
        assert_trusted(f"https://{HOST}.evil.example/api/v1/jobs")
    with pytest.raises(WorkableError):
        assert_trusted(f"http://{HOST}/api/v1/jobs")


def test_the_employer_comes_from_the_company_block() -> None:
    assert company_of(ROWS[0])
    assert company_of({"company": {"title": "  "}}) is None
    assert company_of({}) is None


def test_the_created_date_is_used_and_never_the_updated_one() -> None:
    """ "Since you last looked" asks when a posting FIRST appeared, and an
    employer correcting a typo does not make a job new again."""
    stamp = posted_at(
        {"created": "2026-09-01T10:00:00.000Z", "updated": "2026-09-09T10:00:00.000Z"}
    )
    assert stamp is not None
    assert stamp.startswith("2026-09-01")


def test_asking_this_adapter_about_one_employer_is_refused() -> None:
    """An AGGREGATOR has no board per employer, and Speedrun's silent
    `VALID_EMPTY` is why this raises rather than returning nothing."""
    from career_agent.providers.base import BoardRef

    provider = WorkableProvider(_Fetcher([PAGE1]))  # type: ignore[arg-type]
    assert WorkableProvider.addresses_boards_by_company is False
    with pytest.raises(WorkableError):
        list(provider.list_postings(BoardRef("acme", "workable", "acme")))
