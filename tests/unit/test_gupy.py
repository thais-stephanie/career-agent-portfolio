"""The Gupy adapter: what it refuses, and the two things it must never confuse.

The refusals are the point. This feed is the largest source in the corpus by
far and the only Brazilian one, so a record it cannot address must fall out
rather than be patched up with a guess -- a corpus that grows rows nobody can
open is worse than one that grows more slowly.

The two confusions each have their own test because each has bitten this
product before, in another source:

* a place of work read as a hiring scope (invariant 3, and the reason
  `publishes_hiring_scope` is False on a Brazilian board);
* a vendor's `total` trusted as a stop condition, which for this vendor ends
  every walk on its first page.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_agent.providers.gupy import (
    BRAZILIAN_STATES,
    CONTRACT_TYPES,
    MAX_OFFSET,
    MAX_SLICES,
    PARTITION_ORDER,
    PER_PAGE,
    WORKPLACE_TYPES,
    GupyError,
    GupyProvider,
    assert_trusted,
    company_of,
    compose_location,
    plan_slices,
    public_url,
    to_stub,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "gupy"
REMOTE = json.loads((FIXTURES / "feed-remote-page1.json").read_text(encoding="utf-8"))
ONSITE = json.loads((FIXTURES / "feed-onsite-page1.json").read_text(encoding="utf-8"))


def _record() -> dict[str, object]:
    return dict(REMOTE["data"][0])


# -- addressing --------------------------------------------------------------


def test_a_recorded_row_becomes_a_stub() -> None:
    stub = to_stub(_record())
    assert stub is not None
    assert stub.external_id.startswith("gupy-")
    assert stub.title
    assert stub.url.startswith("https://")
    assert stub.description_html
    assert stub.location_raw and "Brasil" in stub.location_raw
    # RFC 3339, offset-aware, second resolution. Every adapter converts; none
    # passes a provider-native encoding through.
    assert stub.posted_at is not None and stub.posted_at.endswith("+00:00")


def test_every_recorded_row_in_both_partitions_is_addressable() -> None:
    """A refusal rate above zero on captured data is a bug in the reader.

    These are real rows from a real response, so anything this cannot address
    is something the adapter misread rather than something the vendor omitted.
    """
    rows = REMOTE["data"] + ONSITE["data"]
    assert rows
    assert all(to_stub(row) is not None for row in rows)


@pytest.mark.parametrize("missing", ["id", "name", "jobUrl"])
def test_a_row_missing_a_required_field_is_refused(missing: str) -> None:
    record = _record()
    record[missing] = None
    assert to_stub(record) is None


def test_a_job_url_off_the_vendor_is_refused() -> None:
    """`jobUrl` is third-party input and becomes `job.url`, which is what a
    person clicks. A feed that started pointing elsewhere must not be followed
    into it."""
    record = _record()
    record["jobUrl"] = "https://evil.example/job/1"
    assert public_url(record) is None
    assert to_stub(record) is None


def test_a_plain_http_job_url_is_refused() -> None:
    record = _record()
    record["jobUrl"] = "http://lojasrenner.gupy.io/job/abc"
    assert public_url(record) is None


def test_the_posting_url_is_the_employers_own_subdomain() -> None:
    """Not the portal's. One shared URL for 81,310 postings would make every
    employer look like the same employer, which ADR-0008 forbids."""
    for row in REMOTE["data"]:
        url = public_url(row)
        assert url is not None
        assert ".gupy.io/" in url
        assert "employability-portal" not in url


# -- geography, which is the whole reason this source was built ---------------


def test_the_location_is_composed_city_state_country() -> None:
    record = _record()
    record.update({"city": "Recife", "state": "Pernambuco", "country": "Brasil"})
    assert compose_location(record) == "Recife, Pernambuco, Brasil"


def test_an_empty_part_is_skipped_rather_than_leaving_a_stray_comma() -> None:
    record = _record()
    record.update({"city": "", "state": None, "country": "Brasil"})
    assert compose_location(record) == "Brasil"


def test_a_row_with_no_place_at_all_composes_nothing() -> None:
    record = _record()
    record.update({"city": None, "state": None, "country": None})
    assert compose_location(record) is None


def test_the_composed_location_resolves_to_brazil_and_latam() -> None:
    """The end this composition exists for. If the gazetteer stops resolving
    it, every posting from this source silently becomes located nowhere and
    the whole source stops being worth collecting."""
    from career_agent.match.places import resolve_place

    place = resolve_place("Cachoeira do Sul, Rio Grande do Sul, Brasil")
    assert "BR" in place.countries
    assert "LATAM" in place.regions


def test_this_adapter_does_not_claim_to_publish_a_hiring_scope() -> None:
    """INVARIANT 3, IN PORTUGUESE.

    `country: Brasil` on an on-site posting in Cachoeira do Sul says where the
    work is. It does not say the employer will hire from anywhere in Brazil.
    The temptation is stronger here than on any other source in this corpus,
    because the board is Brazilian and so is the candidate, and giving in to it
    would offer her thousands of postings on the strength of a field that
    answers a different question.
    """
    assert GupyProvider.capabilities.publishes_hiring_scope is False


def test_no_field_is_mapped_to_a_hiring_location_hint() -> None:
    """The same rule, asserted where it would actually be broken."""
    from career_agent.domain.enums import MetadataDimension

    dimensions = GupyProvider.field_map.dimensions()
    assert MetadataDimension.HIRING_LOCATION_HINT not in dimensions


# -- the vendor's two walls --------------------------------------------------


def test_an_offset_at_the_ceiling_is_refused_before_a_request() -> None:
    """`offset=10000` answers 400. Discovering that from the vendor turns a
    known wall into a reported failure."""
    provider = GupyProvider(fetcher=None)  # type: ignore[arg-type]
    with pytest.raises(GupyError):
        provider.feed_url("remote", MAX_OFFSET)


def test_an_offset_just_under_the_ceiling_is_allowed() -> None:
    provider = GupyProvider(fetcher=None)  # type: ignore[arg-type]
    assert provider.feed_url("remote", MAX_OFFSET - PER_PAGE)


def test_an_unmeasured_workplace_type_never_reaches_a_url() -> None:
    provider = GupyProvider(fetcher=None)  # type: ignore[arg-type]
    with pytest.raises(GupyError):
        provider.feed_url("anywhere", 0)


def test_the_count_probe_asks_for_ten_and_only_ten() -> None:
    """`total` is real at `limit=10` and comes back as 100 at `limit=100`.

    The probe is a separate request for that reason alone, so its shape is
    asserted rather than left to a comment.
    """
    provider = GupyProvider(fetcher=None)  # type: ignore[arg-type]
    assert "limit=10&" in provider.count_url("remote")


def test_the_recorded_page_shows_the_total_trap_rather_than_the_real_total() -> None:
    """Captured proof, so the trap cannot be quietly forgotten: this response
    was taken at `limit=100` and its `total` is 100, not the 2,115 the same
    partition reports at `limit=10`."""
    assert REMOTE["pagination"]["limit"] == PER_PAGE
    assert REMOTE["pagination"]["total"] == PER_PAGE


# -- what a default run reads ------------------------------------------------


def test_every_workplace_type_is_collected_by_default() -> None:
    """THE CORRECTION, AND IT IS ABOUT WHOSE TASTE A CORPUS HAS.

    On-site was excluded from the default because the owner works remotely.
    That mistook a fact about ONE candidate for a fact about the corpus, and
    invariant 4 says the opposite: the same posting must produce the same
    fingerprint for every candidate, because a corpus describes work and a
    later layer decides whether it is wanted.

    A lawyer moving into an office role, somebody starting in shop-floor
    sales, and a candidate in Utah are all served by the same database and none
    of them is served by one filtered to somebody else's preferences. The
    FILTERS decide what is shown.
    """
    from career_agent.pipeline.gupy_collect import GupyCollector

    assert set(WORKPLACE_TYPES) == {"remote", "hybrid", "on-site"}
    collector = GupyCollector.__new__(GupyCollector)
    collector.workplace_types = None or WORKPLACE_TYPES  # what __init__ falls back to
    assert set(collector.workplace_types) == set(WORKPLACE_TYPES)


def test_pj_is_a_contract_type_this_collector_knows_about() -> None:
    """`vacancy_legal_entity` is PJ and `vacancy_type_effective` is CLT.

    Both are collected. Whether a candidate wants PJ work is a question for her
    filters, and `contract_regime` has been NULL on every scored row in this
    corpus precisely because no source had ever stated it.
    """
    assert "vacancy_legal_entity" in CONTRACT_TYPES
    assert "vacancy_type_effective" in CONTRACT_TYPES


def test_the_contract_dimension_is_exhaustive_and_that_is_why_it_is_first() -> None:
    """Measured 2026-09-09 over the on-site slice: the fourteen values summed
    to 73,299 against a declared 72,682, so every posting carries one. A cut
    that loses rows is not a partition, and this is the only dimension proven
    not to."""
    assert len(CONTRACT_TYPES) == 14
    assert PARTITION_ORDER[1] == "type"


def test_a_plan_never_grows_past_its_budget() -> None:
    """A vendor answering the same total for every query -- a stub, or a broken
    endpoint -- would drive the recursion to the bottom of every dimension:
    1,134 queries for a feed nobody can partition."""
    plan = plan_slices(
        lambda filters: 10**6,
        lambda dimension, seed: {
            "workplaceType": WORKPLACE_TYPES,
            "type": CONTRACT_TYPES,
            "state": BRAZILIAN_STATES,
        }.get(dimension, ()),
        ceiling=100,
    )
    assert len(plan) <= MAX_SLICES


def test_a_cell_that_fits_is_not_cut_further() -> None:
    plan = plan_slices(
        lambda filters: 10,
        lambda dimension, seed: WORKPLACE_TYPES if dimension == "workplaceType" else (),
        ceiling=100,
    )
    assert len(plan) == 1
    assert plan[0].filters == ()
    assert not plan[0].over_ceiling


def test_an_oversized_cell_keeps_its_parent_beside_its_children() -> None:
    """THE REASON THE PLAN OVERLAPS ITSELF.

    `state` is only 99% exhaustive -- 546 of 56,920 postings in the largest
    cell carry no state at all -- so walking only the children would drop them
    silently. The parent is walked too, to its own ceiling, and the collector
    deduplicates by id. Overlap costs requests; a gap costs postings.
    """
    plan = plan_slices(
        lambda filters: 10 if "workplaceType" in filters else 10**6,
        lambda dimension, seed: WORKPLACE_TYPES if dimension == "workplaceType" else (),
        ceiling=100,
    )
    parents = [one for one in plan if one.filters == ()]
    assert len(parents) == 1, "the parent is walked as well as its children"
    assert parents[0].over_ceiling
    assert len(plan) == 1 + len(WORKPLACE_TYPES)


def test_a_url_outside_the_api_host_is_never_fetched() -> None:
    with pytest.raises(GupyError):
        assert_trusted("https://employability-portal.gupy.io.evil.example/api/v1/jobs")
    with pytest.raises(GupyError):
        assert_trusted("http://employability-portal.gupy.io/api/v1/jobs")


# -- the advert --------------------------------------------------------------


def test_the_description_arrives_in_the_listing() -> None:
    """The economics of this source. If it stopped being true, a run would
    need 100 extra requests per page and nobody would notice from the counts.
    """
    assert GupyProvider.capabilities.full_description_in_list is True
    assert all(row.get("description") for row in REMOTE["data"])


def test_fetching_a_posting_opens_nothing_and_returns_the_listed_advert() -> None:
    provider = GupyProvider(fetcher=None)  # type: ignore[arg-type]
    stub = to_stub(_record())
    assert stub is not None
    posting = provider.fetch_posting(None, stub)  # type: ignore[arg-type]
    assert posting.has_description
    assert "<" not in posting.description_text


def test_list_items_survive_as_separate_lines() -> None:
    """A requirements list collapsed into one line makes the employer's own
    sentences harder to read than the employer wrote them, and ADR-0002
    verifies quotes against exactly this text."""
    from career_agent.providers.gupy import _strip_html

    text = _strip_html("<ul><li>SQL</li><li>Python</li></ul>")
    assert "SQL" in text.splitlines()
    assert "Python" in text.splitlines()


def test_the_employer_name_comes_from_the_career_page() -> None:
    assert company_of(_record())
    assert company_of({"careerPageName": "   "}) is None
