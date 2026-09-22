"""The Programathor adapter, against a real captured listing and real postings.

Two of these tests exist because the source cost this session a wrong answer
before it gave a right one:

* the JSON-LD block must be parsed with `strict=False`, because the vendor
  emits raw newlines inside `description` and a strict parser silently finds
  nothing on a page that carries everything;
* a listing page links to `/jobs-front-end` and `/jobs-front-end/remoto`, which
  are categories rather than postings, so "contains /jobs/" is not a test for
  a posting.

The third theme is the source's defining cost: a material fraction of its
posting pages answer HTTP 500 -- measured three times at 3 of 6, 12 of 15 and
106 of 112 -- and that must read as an absence to be counted rather than as a
failure that aborts a walk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_agent.providers.programathor import (
    PER_PAGE,
    ProgramathorError,
    ProgramathorProvider,
    assert_trusted,
    company_domain,
    company_of,
    compose_location,
    job_paths,
    job_posting,
    posting_id,
    read_compensation,
    strip_html,
    to_stub,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "programathor"
LISTING = (FIXTURES / "listing-page1.html").read_text(encoding="utf-8")
POSTINGS = json.loads((FIXTURES / "postings.json").read_text(encoding="utf-8"))["postings"]


# -- reading the listing -----------------------------------------------------


def test_the_captured_listing_yields_a_full_page_of_postings() -> None:
    paths = job_paths(LISTING)
    assert len(paths) == PER_PAGE


def test_a_category_link_is_not_a_posting() -> None:
    """`/jobs-front-end` and `/jobs-front-end/remoto` are on every listing page.

    Reading them as postings would send this collector to fetch a category
    index once per page and file it as a job.
    """
    html = (
        '<a href="/jobs/33685-x">a</a>'
        '<a href="/jobs-front-end">b</a>'
        '<a href="/jobs-front-end/remoto">c</a>'
        '<a href="/jobs">d</a>'
    )
    assert job_paths(html) == ("/jobs/33685-x",)


def test_the_same_posting_listed_twice_is_returned_once() -> None:
    """The listing repeats a promoted posting, and a walk that kept both would
    fetch one advert twice on a source where a fetch is the expensive part."""
    html = '<a href="/jobs/1-a">x</a><a href="/jobs/1-a">x again</a>'
    assert job_paths(html) == ("/jobs/1-a",)


def test_the_id_is_the_number_and_never_the_slug() -> None:
    """A slug changes when somebody edits a title. An id does not."""
    assert posting_id("/jobs/33685-desenvolvedor-back-end") == "33685"
    assert posting_id("https://programathor.com.br/jobs/12-x") == "12"
    assert posting_id("/jobs-front-end") is None
    assert posting_id("/jobs") is None


# -- reading a posting -------------------------------------------------------


def test_every_captured_posting_is_addressable() -> None:
    for record in POSTINGS:
        stub = to_stub(record["_url"], record)
        assert stub is not None
        assert stub.external_id.startswith("programathor-")
        assert stub.title
        assert stub.description_html


def test_the_structured_block_needs_a_lenient_parser() -> None:
    """THE DEFECT THIS SOURCE OPENED WITH.

    The vendor writes raw carriage returns and newlines inside the description
    string. `json.loads` on that raises "Invalid control character", so a
    strict read finds no `JobPosting` on a page that carries one, and the
    source looks empty rather than broken.
    """
    payload = json.dumps({"@type": "JobPosting", "title": "x", "description": "a\nb"})
    with_control = payload.replace("a\\nb", "a\nb")
    html = f'<script type="application/ld+json">{with_control}</script>'

    assert json.loads(json.dumps({"x": 1})) == {"x": 1}  # the parser itself is fine
    with pytest.raises(json.JSONDecodeError):
        json.loads(with_control)
    assert job_posting(html) is not None


def test_a_breadcrumb_block_is_not_mistaken_for_the_posting() -> None:
    """Every page carries two blocks and the posting is not always first."""
    crumbs = '{"@type": "BreadcrumbList", "itemListElement": []}'
    posting = '{"@type": "JobPosting", "title": "Real"}'
    html = (
        f'<script type="application/ld+json">{crumbs}</script>'
        f'<script type="application/ld+json">{posting}</script>'
    )
    found = job_posting(html)
    assert found is not None
    assert found["title"] == "Real"


def test_a_page_with_no_structured_data_reads_as_nothing() -> None:
    assert job_posting("<html><body>sorry</body></html>") is None


# -- geography ---------------------------------------------------------------


def test_the_location_skips_the_vendors_placeholder_region() -> None:
    """`addressRegion` arrives as a literal `-` on real rows. Passed through,
    it reaches the gazetteer as a token to resolve."""
    posting = {
        "jobLocation": {
            "address": {
                "addressLocality": "Sao Paulo",
                "addressRegion": "-",
                "addressCountry": "BR",
            }
        }
    }
    assert compose_location(posting) == "Sao Paulo, BR"


def test_the_composed_location_resolves_to_brazil_and_latam() -> None:
    from career_agent.match.places import resolve_place

    place = resolve_place("Sao Paulo, BR")
    assert "BR" in place.countries
    assert "LATAM" in place.regions


def test_this_adapter_does_not_claim_to_publish_a_hiring_scope() -> None:
    assert ProgramathorProvider.capabilities.publishes_hiring_scope is False


def test_the_country_is_not_declared_as_a_hiring_location_hint() -> None:
    """The one mapping this field map does NOT carry, and the reason it does
    not: `HIRING_LOCATION_HINT` means "where the employer may hire", and
    `addressCountry` means "where the work is". Declaring it would break
    invariant 3 for every Brazilian posting on the board at once."""
    from career_agent.domain.enums import MetadataDimension

    dimensions = ProgramathorProvider.field_map.dimensions()
    assert MetadataDimension.HIRING_LOCATION_HINT not in dimensions
    assert MetadataDimension.EMPLOYMENT_TYPE_HINT in dimensions


# -- what makes this source worth its request cost ---------------------------


def test_a_salary_needs_a_currency_before_it_is_read_at_all() -> None:
    """Nothing here converts between currencies, so a bare number would be
    compared against a target in another one."""
    assert read_compensation({"baseSalary": {"value": {"value": 8000}}}) is None
    assert read_compensation({"baseSalary": {"currency": "BRL"}}) is None


def test_a_stated_salary_carries_its_currency_and_its_period() -> None:
    hint = read_compensation(
        {
            "baseSalary": {
                "currency": "BRL",
                "value": {"value": 18000, "unitText": "MONTH"},
            }
        }
    )
    assert hint is not None
    assert hint.currency == "BRL"
    assert hint.period == "MONTH"
    assert hint.min_value == 18000


def test_at_least_one_captured_posting_states_pay() -> None:
    """The claim the catalogue row makes about this source, checked against
    what was actually captured rather than against a hope."""
    priced = [record for record in POSTINGS if read_compensation(record) is not None]
    assert priced, "no captured posting states a salary; the row overstates the source"


def test_the_captured_postings_carry_a_brazilian_contract_signal() -> None:
    """`CONTRACTOR` on this board is PJ and `FULL_TIME` is the CLT-shaped
    answer. `contract_regime` has been NULL on every scored row in this corpus
    because no source stated it; this one does."""
    kinds = {record.get("employmentType") for record in POSTINGS}
    assert kinds & {"CONTRACTOR", "FULL_TIME"}


def test_the_employer_domain_is_reduced_to_a_hostname() -> None:
    """`sameAs` arrives bare on some rows and as a URL on others."""
    assert company_domain({"hiringOrganization": {"sameAs": "convolut.ai"}}) == "convolut.ai"
    assert (
        company_domain({"hiringOrganization": {"sameAs": "https://www.accenture.com/br-pt"}})
        == "accenture.com"
    )
    assert company_domain({"hiringOrganization": {"sameAs": "not a domain"}}) is None
    assert company_domain({}) is None


def test_the_employer_name_comes_from_the_hiring_organisation() -> None:
    assert company_of(POSTINGS[0])
    assert company_of({"hiringOrganization": {"name": "  "}}) is None


# -- refusals ----------------------------------------------------------------


def test_nothing_off_the_host_is_ever_fetched() -> None:
    with pytest.raises(ProgramathorError):
        assert_trusted("https://programathor.com.br.evil.example/jobs")
    with pytest.raises(ProgramathorError):
        assert_trusted("http://programathor.com.br/jobs")


def test_a_path_that_is_not_a_posting_never_becomes_a_url() -> None:
    provider = ProgramathorProvider(fetcher=None)  # type: ignore[arg-type]
    with pytest.raises(ProgramathorError):
        provider.posting_url("/jobs-front-end")
    with pytest.raises(ProgramathorError):
        provider.posting_url("https://evil.example/jobs/1-x")


def test_the_first_listing_page_carries_no_page_parameter() -> None:
    provider = ProgramathorProvider(fetcher=None)  # type: ignore[arg-type]
    assert provider.listing_url(1).endswith("/jobs")
    assert provider.listing_url(2).endswith("/jobs?page=2")


# -- the advert --------------------------------------------------------------


def test_the_description_is_not_in_the_listing_and_the_capability_says_so() -> None:
    """The whole cost model of this source, asserted rather than described."""
    assert ProgramathorProvider.capabilities.full_description_in_list is False
    assert ProgramathorProvider.capabilities.obtains_full_description is True


def test_list_items_survive_as_separate_lines() -> None:
    text = strip_html("<p>Habilidades</p><ul><li>Docker</li><li>Python</li></ul>")
    assert "Docker" in text.splitlines()
    assert "Python" in text.splitlines()
    assert "<" not in text
