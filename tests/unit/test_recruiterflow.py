"""The Recruiterflow adapter, against a board captured live on 2026-09-09.

Captured live, and the permission is stronger than for any other source here:
`recruiterflow.com/robots.txt` is `User-agent: *` with `Allow: /` and then names
this reader twice more --

    User-agent: Anthropic
    Allow: /

    User-agent: Claude
    Allow: /

-- which is the exact opposite of Remote OK and Get on Board, whose robots
files name `ClaudeBot` with `Disallow: /` and which are therefore never called
from this repository.

Four themes, and each is a way this source can be wrong quietly:

* the LIST BLOB groups the same postings THREE ways, so reading more than one
  grouping returns every posting several times;
* the ADVERT is not in the list, and its JSON-LD needs `strict=False`;
* `details` is where the work is and never a declared hiring scope;
* a board page with no blob is a CHANGED CONTRACT and not an empty board.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_agent.net.fetcher import FetchError
from career_agent.providers.base import BoardRef
from career_agent.providers.recruiterflow import (
    HOST,
    RecruiterflowError,
    RecruiterflowProvider,
    assert_trusted,
    board_slug,
    posting_url,
    read_jobs_list,
    read_posting_ld,
    strip_html,
    to_stub,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "recruiterflow"
PAGE = (FIXTURES / "board-page.html").read_text(encoding="utf-8")
DETAIL = json.loads((FIXTURES / "posting-detail.json").read_text(encoding="utf-8"))
ROWS = read_jobs_list(PAGE)


class _Fetcher:
    """Serves the captured board page and a detail page, and records the URLs."""

    def __init__(self, detail_html: str | None = None) -> None:
        self.urls: list[str] = []
        self._detail = detail_html

    def get_text(self, url: str, use_cache: bool = True) -> str:
        del use_cache
        self.urls.append(url)
        if url.endswith("/jobs"):
            return PAGE
        if self._detail is None:
            raise AssertionError("a detail page was requested and none was provided")
        return self._detail


def _detail_page(posting: dict) -> str:
    return (
        '<html><head><script type="application/ld+json">'
        + json.dumps(posting, ensure_ascii=False)
        + "</script></head><body></body></html>"
    )


# -- the list blob -----------------------------------------------------------


def test_the_whole_board_costs_one_request() -> None:
    provider = RecruiterflowProvider(_Fetcher())  # type: ignore[arg-type]
    stubs = list(provider.list_postings(BoardRef("south", "recruiterflow", "south")))

    assert len(stubs) == len(ROWS)
    assert provider._fetcher.urls == [f"https://{HOST}/south/jobs"]  # type: ignore[attr-defined]


def test_only_one_grouping_is_read_so_no_posting_arrives_twice() -> None:
    """THE TRAP THIS BLOB SETS. It holds the same postings grouped by
    department, by group AND by location. Reading two groupings would return
    every posting several times, and each copy would look perfectly valid."""
    ids = [record["job_id"] for _department, record in ROWS]
    assert len(ids) == len(set(ids))


def test_a_posting_carries_the_department_it_sat_under() -> None:
    """The grouping is read for the NAME as much as for the rows: it is the
    only place this source states a department."""
    departments = {department for department, _record in ROWS}
    assert "Accounting" in departments
    stub = to_stub("south", "Accounting", ROWS[0][1])
    assert stub is not None
    assert stub.department == "Accounting"
    assert stub.payload["department"] == "Accounting"


def test_a_board_page_with_no_blob_is_a_changed_contract_and_not_an_empty_board() -> None:
    """The difference every ATS adapter here turns on. An empty board is real
    and collectable and lets the collector close what disappeared; a page that
    stopped defining the blob says nothing about what exists, and reading it as
    emptiness would close a whole agency's postings."""
    with pytest.raises(RecruiterflowError):
        read_jobs_list("<html><body>no blob here</body></html>")

    class _Broken(_Fetcher):
        def get_text(self, url: str, use_cache: bool = True) -> str:
            del use_cache
            self.urls.append(url)
            return "<html><body></body></html>"

    provider = RecruiterflowProvider(_Broken())  # type: ignore[arg-type]
    with pytest.raises(FetchError):
        list(provider.list_postings(BoardRef("south", "recruiterflow", "south")))


def test_unreadable_json_in_the_blob_raises_rather_than_yielding_nothing() -> None:
    with pytest.raises(RecruiterflowError):
        read_jobs_list("<script>window.jobsList = {not json};</script>")


# -- one posting -------------------------------------------------------------


def test_every_captured_row_is_addressable() -> None:
    for department, record in ROWS:
        stub = to_stub("south", department, record)
        assert stub is not None
        assert stub.external_id.startswith("recruiterflow-south-")
        assert stub.url.startswith(f"https://{HOST}/south/jobs/")
        assert stub.title
        assert stub.posted_at and stub.posted_at.startswith("202")


def test_the_external_id_carries_the_agency_and_not_only_the_number() -> None:
    """`job_id` is per AGENCY. Two agencies on this vendor both have a job 852,
    and an id that dropped the slug would file one as the other."""
    first = to_stub("south", "", ROWS[0][1])
    second = to_stub("hirelatam", "", ROWS[0][1])
    assert first is not None and second is not None
    assert first.external_id != second.external_id


@pytest.mark.parametrize("job_id", [None, "1117", True, 1.5])
def test_a_row_without_an_integer_job_id_is_refused(job_id: object) -> None:
    assert posting_url("south", job_id) is None
    record = dict(ROWS[0][1])
    record["job_id"] = job_id
    assert to_stub("south", "", record) is None


def test_a_row_without_a_title_is_refused() -> None:
    record = dict(ROWS[0][1])
    record["job_name"] = "   "
    assert to_stub("south", "", record) is None


# -- geography ---------------------------------------------------------------


def test_the_location_is_where_the_work_is_and_not_a_declared_scope() -> None:
    """`Brasilia, Brazil, San Salvador, El Salvador, Bogota, Colombia` on a
    REMOTE posting really is an employer naming where it will hire, and this
    adapter still declares no hiring scope.

    The same field on a non-remote posting is an office, and one column cannot
    mean two things. `gates.structured_geography` decides exactly this, from
    the work model and the resolved place, and it was built for boards in this
    shape in V1.5.
    """
    from career_agent.domain.enums import MetadataDimension

    assert RecruiterflowProvider.capabilities.publishes_hiring_scope is False
    assert (
        MetadataDimension.HIRING_LOCATION_HINT not in RecruiterflowProvider.field_map.dimensions()
    )
    assert "details" not in RecruiterflowProvider.field_map.paths()

    stub = to_stub("south", "Accounting", ROWS[0][1])
    assert stub is not None and stub.location_raw
    assert "Brazil" in stub.location_raw


def test_the_work_model_comes_from_the_vendors_own_field() -> None:
    from career_agent.domain.enums import MetadataDimension
    from career_agent.domain.provider_values import normalise_work_model

    assert "remote_type" in RecruiterflowProvider.field_map.paths()
    assert MetadataDimension.WORK_MODEL_HINT in RecruiterflowProvider.field_map.dimensions()
    assert normalise_work_model("Remote") is not None


def test_a_row_that_states_no_work_model_has_none_invented_for_it() -> None:
    """Some rows carry no `remote_type` at all, and it must resolve to nothing.

    The first version of this test asserted that TALENT-POOL rows are the ones
    with no work model. They are not: one of the two in the fixture is marked
    `Remote` and the other states nothing, so the department says nothing about
    the field. What matters is the field itself, which is what this asserts now.

    Talent pools are collected either way. `Apply to our Talent Pool` is not an
    opening, and deciding what a person wants to see belongs to the filter
    layer rather than to a collector -- the same call Gupy's
    `vacancy_type_talent_pool` gets, for the same reason.
    """
    from career_agent.domain.provider_values import normalise_work_model

    silent = [r for _department, r in ROWS if r.get("remote_type") is None]
    assert silent, "the fixture must keep a row that states no work model"

    for row in silent:
        stub = to_stub("south", "Apply to Our Talent Pool", row)
        assert stub is not None, "a row is still collectable without a work model"
        assert stub.payload.get("remote_type") is None

    stated = [r for _d, r in ROWS if r.get("remote_type")]
    assert stated and all(normalise_work_model(r["remote_type"]) is not None for r in stated)


# -- the advert --------------------------------------------------------------


def test_the_advert_comes_from_the_detail_pages_json_ld() -> None:
    provider = RecruiterflowProvider(_Fetcher(_detail_page(DETAIL)))  # type: ignore[arg-type]
    board = BoardRef("south", "recruiterflow", "south")
    stub = next(iter(provider.list_postings(board)))
    posting = provider.fetch_posting(board, stub)

    assert posting.has_description
    assert len(posting.description_text) > 500
    assert posting.payload["posting"]["@type"] == "JobPosting"
    # The listing row survives underneath, which is what the field map reads.
    assert posting.payload["remote_type"] == "Remote"


def test_the_json_ld_is_parsed_with_raw_newlines_in_it() -> None:
    """`strict=False`, and it is required rather than defensive. This vendor
    writes real newlines inside `description`, and a strict read finds nothing
    on a page that has everything -- exactly the trap Programathor set first."""
    posting = dict(DETAIL)
    posting["description"] = "line one\nline two"
    html = (
        '<script type="application/ld+json">'
        + json.dumps(posting, ensure_ascii=False).replace("\\n", "\n")
        + "</script>"
    )
    read = read_posting_ld(html)
    assert read is not None
    assert "line two" in read["description"]


def test_a_page_with_no_job_posting_block_yields_no_advert_and_does_not_raise() -> None:
    assert read_posting_ld("<html></html>") is None
    assert (
        read_posting_ld('<script type="application/ld+json">{"@type":"WebPage"}</script>') is None
    )


def test_the_listing_carries_no_advert_and_the_capabilities_say_so() -> None:
    assert RecruiterflowProvider.capabilities.full_description_in_list is False
    assert RecruiterflowProvider.capabilities.obtains_full_description is True
    assert all(to_stub("south", d, r).description_html is None for d, r in ROWS)  # type: ignore[union-attr]


def test_no_salary_is_read_because_none_is_published() -> None:
    assert RecruiterflowProvider.capabilities.exposes_compensation is False
    assert all("salary" not in key for _d, r in ROWS for key in r)
    assert not any("salary" in key.lower() for key in DETAIL)


def test_list_items_survive_as_separate_lines() -> None:
    text = strip_html("<ul><li>Portuguese and English</li><li>LATAM residency</li></ul>")
    assert "Portuguese and English" in text.splitlines()
    assert "LATAM residency" in text.splitlines()


# -- addressing --------------------------------------------------------------


def test_it_is_a_board_per_agency_and_says_so() -> None:
    """The reason this is a family rather than a scraper: `discover-boards`
    gains a fourth provider to probe, so every further agency on this vendor
    costs a registry row rather than a module."""
    assert RecruiterflowProvider.addresses_boards_by_company is True
    from career_agent.providers.registry import board_providers

    assert "recruiterflow" in board_providers()


@pytest.mark.parametrize("slug", ["../evil", "south/jobs", "", "   ", "a" * 200])
def test_a_slug_that_could_not_be_one_is_refused_without_a_request(slug: str) -> None:
    """A registry row is still a value somebody typed, and `../` in a path
    segment is how a typo becomes a different site."""
    provider = RecruiterflowProvider(_Fetcher())  # type: ignore[arg-type]
    board = BoardRef("x", "recruiterflow", slug)
    assert provider.validate_board(board) is not None
    with pytest.raises(RecruiterflowError):
        board_slug(board)


def test_nothing_off_the_host_is_ever_fetched() -> None:
    with pytest.raises(RecruiterflowError):
        assert_trusted(f"https://{HOST}.evil.example/south/jobs")
    with pytest.raises(RecruiterflowError):
        assert_trusted(f"http://{HOST}/south/jobs")
    assert assert_trusted(f"https://{HOST}/south/jobs")


def test_a_valid_slug_passes_validation_without_opening_a_socket() -> None:
    provider = RecruiterflowProvider(_Fetcher())  # type: ignore[arg-type]
    assert provider.validate_board(BoardRef("south", "recruiterflow", "south")) is None
    assert provider._fetcher.urls == []  # type: ignore[attr-defined]
