"""The Avlis Talent adapter, against the board captured live on 2026-09-09.

Captured live: `avlistalent.com/robots.txt` allows `/jobs` by name and excludes
only an admin, an internal, a marketing and a scorecard area. No AI crawler is
named.

The three themes here are all things this source CANNOT do, and each is
measured rather than assumed:

* there is no per-posting URL, so the ADR-0013 CANONICAL_URL rule is switched
  off for this source alone;
* `location` is a work model and a TIMEZONE, never a place;
* `salary` is free text with no currency and no period.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_agent.providers.avlis import (
    AGENCY,
    BOARD_URL,
    HOST,
    AvlisError,
    AvlisProvider,
    assert_trusted,
    is_open,
    posted_at,
    strip_html,
    to_stub,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "avlis"
ROWS = json.loads((FIXTURES / "jobs.json").read_text(encoding="utf-8"))


class _Fetcher:
    def __init__(self, body: object) -> None:
        self.body = body
        self.urls: list[str] = []

    def get_json(self, url: str, use_cache: bool = True) -> object:
        del use_cache
        self.urls.append(url)
        return self.body


# -- the one page ------------------------------------------------------------


def test_every_posting_shares_the_board_url_because_there_is_only_one_page() -> None:
    """MEASURED, not assumed. `/jobs/26`, `/job/26`, `/jobs?id=26` and
    `/jobs#26` all return the same 5,932-byte application shell.

    Inventing a per-posting URL would send somebody to a page that does not
    read it, which is worse than sending them to the board.
    """
    urls = {to_stub(row).url for row in ROWS}  # type: ignore[union-attr]
    assert urls == {BOARD_URL}
    assert f"https://{HOST}/jobs" == BOARD_URL


def test_the_external_id_still_tells_the_postings_apart() -> None:
    """The URL is shared and identity is not. That is the whole reason this
    source is collectable at all."""
    ids = [to_stub(row).external_id for row in ROWS]  # type: ignore[union-attr]
    assert len(set(ids)) == len(ids) == len(ROWS)
    assert all(i.startswith("avlis-") for i in ids)


def test_the_collector_does_not_use_the_canonical_url_rule() -> None:
    """Asserted STRUCTURALLY, because the consequence is silent: six postings
    sharing a URL would file five of them as sightings of the first."""
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "career_agent"
        / "pipeline"
        / "avlis_collect.py"
    ).read_text(encoding="utf-8")
    assert "_job_by_canonical_url" not in source
    assert "CANONICAL_URL" in source, "and it says why, rather than just omitting it"


# -- geography and pay, which this source does not publish --------------------


def test_a_timezone_is_not_a_place() -> None:
    """`Remote (US Timezone)` says when somebody works, not where they may
    live. Invariant 3 keeps those apart, and this is the field a reflex would
    map."""
    from career_agent.domain.enums import MetadataDimension
    from career_agent.domain.provider_values import normalise_work_model

    assert AvlisProvider.capabilities.publishes_hiring_scope is False
    assert AvlisProvider.capabilities.exposes_remote_flag is False
    assert "location" not in AvlisProvider.field_map.paths()
    assert MetadataDimension.HIRING_LOCATION_HINT not in AvlisProvider.field_map.dimensions()
    assert MetadataDimension.WORK_MODEL_HINT not in AvlisProvider.field_map.dimensions()
    # And it would not resolve even if somebody mapped it.
    assert normalise_work_model("Remote (US Timezone)") is None


def test_the_location_string_still_reaches_the_matcher() -> None:
    """Unmapped is not discarded. The word `Remote` reaches the matcher through
    `location_raw`, the way it does for any board that writes it in prose."""
    stub = to_stub(ROWS[0])
    assert stub is not None
    assert stub.location_raw == "Remote (US Timezone)"


def test_free_text_pay_is_carried_and_never_read() -> None:
    """`$75-$100k` and `US$75k-US$90k` in the same feed. No currency field and
    no period field, so a number read out of it would have an invented unit --
    which is worse than no number."""
    assert AvlisProvider.capabilities.exposes_compensation is False
    salaries = {row.get("salary") for row in ROWS}
    assert any(s for s in salaries), "the fixture must keep a row that states pay"
    stub = to_stub(ROWS[0])
    assert stub is not None
    assert "salary" in stub.payload, "carried in the payload, where a later decision can reach it"


def test_the_employment_type_is_the_one_field_that_is_read() -> None:
    from career_agent.domain.enums import MetadataDimension
    from career_agent.domain.provider_values import normalise_employment_type

    assert AvlisProvider.field_map.paths() == ("type",)
    assert MetadataDimension.EMPLOYMENT_TYPE_HINT in AvlisProvider.field_map.dimensions()
    assert normalise_employment_type("Full-time") == "FULL_TIME"
    assert normalise_employment_type("Contract") == "CONTRACT"


# -- the feed -----------------------------------------------------------------


def test_the_whole_board_is_one_request() -> None:
    fetcher = _Fetcher(ROWS)
    read = AvlisProvider(fetcher).read_feed()  # type: ignore[arg-type]
    assert len(read.records) == len(ROWS)
    assert fetcher.urls == [f"https://{HOST}/api/jobs"]


def test_a_role_the_agency_closed_is_not_collected_and_is_counted() -> None:
    """`active: false` is the AGENCY speaking. A posting vanishing from a feed
    is not, and the two must not read alike."""
    rows = [dict(ROWS[0]), dict(ROWS[1])]
    rows[1]["active"] = False
    read = AvlisProvider(_Fetcher(rows)).read_feed()  # type: ignore[arg-type]
    assert len(read.records) == 1
    assert read.closed == 1
    assert is_open(rows[0]) and not is_open(rows[1])


def test_a_body_that_is_not_a_list_raises_rather_than_reading_as_empty() -> None:
    """An empty board and a broken response are different outcomes, and
    conflating them is how a network blip closes a whole posting history."""
    with pytest.raises(AvlisError):
        AvlisProvider(_Fetcher({"jobs": []})).read_feed()  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [{"title": "no id"}, {"id": "26", "title": "t"}, {"id": 1}])
def test_a_row_that_cannot_be_addressed_is_refused(bad: dict) -> None:
    assert to_stub(bad) is None


def test_every_captured_row_is_addressable_and_carries_its_advert() -> None:
    for row in ROWS:
        stub = to_stub(row)
        assert stub is not None
        assert stub.title and stub.department
        assert stub.description_html and len(stub.description_html) > 200
        assert stub.posted_at and stub.posted_at.startswith("202")


def test_the_employer_is_the_agency_and_never_guessed_from_the_advert() -> None:
    """Avlis places people at client startups and the feed does not name the
    clients. Reading one out of the advert is the fuzzy identity ADR-0008
    forbids."""
    assert AGENCY == "Avlis Talent"
    assert all("company" not in key.lower() for row in ROWS for key in row)


def test_nothing_off_the_host_is_ever_fetched() -> None:
    with pytest.raises(AvlisError):
        assert_trusted(f"https://{HOST}.evil.example/api/jobs")
    with pytest.raises(AvlisError):
        assert_trusted(f"http://{HOST}/api/jobs")


def test_asking_this_adapter_about_one_employer_is_refused() -> None:
    from career_agent.providers.base import BoardRef

    assert AvlisProvider.addresses_boards_by_company is False
    with pytest.raises(AvlisError):
        list(AvlisProvider(_Fetcher(ROWS)).list_postings(BoardRef("x", "avlis", "x")))  # type: ignore[arg-type]


def test_the_date_is_read_and_a_missing_one_is_nothing() -> None:
    assert posted_at({"createdAt": "2026-03-16T21:00:31.597Z"}) is not None
    assert posted_at({"createdAt": None}) is None
    assert posted_at({}) is None


def test_markdown_bullets_survive_as_separate_lines() -> None:
    """This feed writes markdown rather than HTML, so the whitespace handling
    is what matters and the tag stripping finds nothing to do."""
    text = strip_html("Requirements\n* React\n* TypeScript")
    assert "* React" in text.splitlines()
