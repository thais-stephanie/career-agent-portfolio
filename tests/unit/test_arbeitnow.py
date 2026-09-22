"""The Arbeitnow adapter, against pages captured live on 2026-09-09.

Captured live, unlike Remote OK's and Get on Board's, and the difference is
recorded rather than assumed: this vendor's robots file names no AI crawler and
does not mention `ClaudeBot`, so the agent maintaining these tests is a
permitted reader here and is not there.

The themes are the two things this source can get wrong quietly:

* `created_at` is an epoch in SECONDS while every other vendor here uses
  milliseconds, so passing it through unconverted dates the whole board to
  1970 and makes it invisible to "since you last looked";
* `location` is a CITY, which is an office, and an office is not a hiring
  scope no matter how convenient that would be.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from career_agent.providers.arbeitnow import (
    HOST,
    PER_PAGE,
    ArbeitnowError,
    ArbeitnowProvider,
    advert_text,
    assert_trusted,
    company_of,
    posted_at,
    public_url,
    to_stub,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "arbeitnow"
PAGE1 = json.loads((FIXTURES / "page1.json").read_text(encoding="utf-8"))
PAGE2 = json.loads((FIXTURES / "page2.json").read_text(encoding="utf-8"))
ROWS = PAGE1["data"]


# -- the date, which is the easy one to get wrong -----------------------------


def test_the_epoch_is_read_as_seconds_and_not_milliseconds() -> None:
    """`to_rfc3339_utc` reads a bare integer as MILLISECONDS, which is right
    for every vendor here except this one. Unconverted, 1,788,967,859 lands in
    1970 and a board publishing no usable date is permanently invisible to the
    question "what is new since I last looked"."""
    stamp = posted_at({"created_at": 1788967859})
    assert stamp is not None
    assert stamp.startswith("2026-"), stamp


def test_a_missing_or_wrong_shaped_date_is_nothing_rather_than_1970() -> None:
    assert posted_at({"created_at": None}) is None
    assert posted_at({"created_at": "yesterday"}) is None
    assert posted_at({"created_at": True}) is None
    assert posted_at({}) is None


def test_every_captured_row_dates_to_the_present() -> None:
    for record in ROWS:
        stub = to_stub(record)
        assert stub is not None
        assert stub.posted_at is not None
        assert stub.posted_at.startswith("202"), stub.posted_at


# -- geography ----------------------------------------------------------------


def test_a_city_is_not_a_hiring_scope() -> None:
    """A company with a desk in Berlin is not a company that will employ you
    there. Invariant 3, and the reason `location` is mapped to no dimension."""
    from career_agent.domain.enums import MetadataDimension

    assert ArbeitnowProvider.capabilities.publishes_hiring_scope is False
    assert MetadataDimension.HIRING_LOCATION_HINT not in ArbeitnowProvider.field_map.dimensions()
    assert "location" not in ArbeitnowProvider.field_map.paths()


def test_the_work_model_comes_from_the_vendors_own_boolean() -> None:
    """`remote` is a real field rather than a guess about the body, and it is
    what lets `gates.structured_geography` read the city as a place of work."""
    from career_agent.domain.enums import MetadataDimension

    assert "remote" in ArbeitnowProvider.field_map.paths()
    assert MetadataDimension.WORK_MODEL_HINT in ArbeitnowProvider.field_map.dimensions()
    assert ArbeitnowProvider.capabilities.exposes_remote_flag is True


def test_a_not_remote_boolean_does_not_become_onsite() -> None:
    """The asymmetry the normaliser already holds, asserted rather than hoped.

    `_WORK_MODELS` maps no negative to a positive: a vendor saying a role is
    NOT remote has not said where its desk is. So `false` resolves to nothing
    here, and reading it as ONSITE would be exactly the confident wrong answer
    the whole design exists to prevent.
    """
    from career_agent.domain.provider_values import normalise_work_model

    assert normalise_work_model("false") is None
    assert normalise_work_model("not remote") is None
    assert normalise_work_model("remote") is not None


def test_the_captured_window_is_mostly_office_work() -> None:
    """The fact that got this source refused once, kept as a measurement rather
    than a memory. It is why the source is worth having, not why it is not."""
    remote = [r for r in ROWS if r.get("remote")]
    assert len(remote) < len(ROWS), "if this ever inverts, the row's note is stale"


# -- addressing and the link back --------------------------------------------


def test_every_captured_row_is_addressable() -> None:
    for record in ROWS:
        stub = to_stub(record)
        assert stub is not None
        assert stub.external_id.startswith("arbeitnow-")
        assert stub.title
        assert stub.description_html


@pytest.mark.parametrize("missing", ["slug", "title", "url"])
def test_a_row_missing_a_required_field_is_refused(missing: str) -> None:
    record = dict(ROWS[0])
    record[missing] = None
    assert to_stub(record) is None


def test_the_apply_target_is_any_of_the_vendors_own_country_sites() -> None:
    """THE BUG THAT COST 60% OF THIS SOURCE.

    Arbeitnow serves one feed across three of its own domains. Measured
    2026-09-09 on a page of 250: `www.arbeitnow.com` 100,
    `www.arbeitnow.co.uk` 75, `www.arbeitnow.fr` 75. This function accepted
    only the first, so the first production run reported 600 postings seen,
    300 stored and **300 not addressable** -- and the 300 refused were British
    and French jobs, which is breadth this corpus was short of rather than
    noise.

    The set is CLOSED AND NAMED. A feed response is third-party input, and
    "any host that looks related" is exactly what the guard exists to prevent.
    """
    from career_agent.providers.arbeitnow import POSTING_HOSTS

    for record in ROWS:
        url = public_url(record)
        assert url is not None
        assert urlsplit(url).hostname in POSTING_HOSTS

    for host in POSTING_HOSTS:
        assert public_url({"url": f"https://{host}/jobs/x"}) is not None
        # Still https only, and still nothing else.
        assert public_url({"url": f"http://{host}/jobs/x"}) is None
    assert public_url({"url": "https://evil.example/jobs/1"}) is None
    assert public_url({"url": "https://www.arbeitnow.com.evil.example/jobs/1"}) is None


def test_what_it_links_to_is_not_what_it_fetches_from() -> None:
    """Two questions with different risks, and the fetch side stays one host.

    A posting URL is a link a person clicks. A fetch URL is this program
    following a value out of a third-party response, which is how a vendor
    would point it anywhere it liked."""
    from career_agent.providers.arbeitnow import POSTING_HOSTS

    assert HOST in POSTING_HOSTS
    for host in POSTING_HOSTS - {HOST}:
        with pytest.raises(ArbeitnowError):
            assert_trusted(f"https://{host}/api/job-board-api")


def test_nothing_off_the_host_is_ever_fetched() -> None:
    """`links.next` comes out of the response and is untrusted input."""
    with pytest.raises(ArbeitnowError):
        assert_trusted(f"https://{HOST}.evil.example/api/job-board-api")
    with pytest.raises(ArbeitnowError):
        assert_trusted(f"http://{HOST}/api/job-board-api")


def test_a_page_number_below_one_never_becomes_a_url() -> None:
    provider = ArbeitnowProvider(fetcher=None)  # type: ignore[arg-type]
    with pytest.raises(ArbeitnowError):
        provider.feed_url(0)
    assert provider.feed_url(2).endswith("?page=2")


# -- the terms ----------------------------------------------------------------


def test_the_vendor_states_its_terms_in_every_response() -> None:
    """Recorded per run rather than trusted from a docstring."""
    for page in (PAGE1, PAGE2):
        terms = page["meta"]["terms"]
        assert "link" in terms.lower()
        assert "abuse" in terms.lower()


def test_the_page_size_is_the_vendors_and_not_a_preference() -> None:
    assert PAGE1["meta"]["per_page"] == PER_PAGE


def test_the_feed_declares_its_own_next_page() -> None:
    assert PAGE1["links"]["next"].endswith("page=2")


# -- the advert ---------------------------------------------------------------


def test_the_whole_advert_arrives_in_the_listing() -> None:
    assert ArbeitnowProvider.capabilities.full_description_in_list is True
    assert ArbeitnowProvider.capabilities.obtains_full_description is True
    assert all(len(r.get("description") or "") > 500 for r in ROWS)


def test_no_salary_is_read_because_none_is_stated() -> None:
    assert ArbeitnowProvider.capabilities.exposes_compensation is False
    assert all("salary" not in key for r in ROWS for key in r)


def test_the_employer_comes_from_the_company_name() -> None:
    assert company_of(ROWS[0])
    assert company_of({"company_name": "  "}) is None


def test_list_items_survive_as_separate_lines() -> None:
    """Through the repository's one contract now, so each item carries the
    marker every sibling adapter's items carry."""
    text = advert_text("<ul><li>Deutsch</li><li>English</li></ul>")
    assert "- Deutsch" in text.splitlines()
    assert "- English" in text.splitlines()
