"""Comeet: one vacancy per base uid, the employer's own location list, and an
aggregator's friendlier label that does not overrule it.

Mock transport; the fixtures are TripleTen's board trimmed to four vacancies
and token-scrubbed (`tests/fixtures/providers/comeet/README.md`). The
candidate is the committed worked example, whose country is Brazil.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.pipeline.collect import Collector
from career_agent.pipeline.rescore import rescore
from career_agent.providers.base import BoardRef
from career_agent.providers.comeet import (
    ComeetProvider,
    base_uid,
    compose_description,
    location_label,
    read_compensation,
    recognise_board_url,
    recognise_posting_url,
    scrub_token,
)
from career_agent.providers.registry import identify_posting_url
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, DiscoverySourceRepo, SourceBoardRepo

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "comeet"
POSITIONS = json.loads((FIXTURES / "positions.json").read_text(encoding="utf-8"))
PAGE = (FIXTURES / "hosted-page.html").read_text(encoding="utf-8")
BOARD = BoardRef(company_slug="tripleten", provider="comeet", board_identifier="tripleten/98.008")
CRM = "comeet-98.008-62.F64"
TEACHING = "comeet-98.008-5E.A6A"


def _transport(requests: list[str] | None = None, page: str = PAGE) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(str(request.url))
        if request.url.host == "www.comeet.com":
            return httpx.Response(200, text=page)
        assert request.url.host == "www.comeet.co", request.url
        if request.url.params.get("token") != "0000TESTTOKEN0000":
            return httpx.Response(400, json={"status": 400, "message": "Token is missing"})
        return httpx.Response(200, json=POSITIONS)

    return httpx.MockTransport(handler)


def _fetcher(requests: list[str] | None = None, page: str = PAGE) -> HttpFetcher:
    return HttpFetcher(
        client=httpx.Client(transport=_transport(requests, page)), request_delay_seconds=0
    )


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "comeet.db")
    migrate(connection)
    yield connection
    connection.close()


def _register(conn: Any) -> None:
    with transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug="tripleten", name="TripleTen"))
        SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company,
                provider="comeet",
                board_identifier="tripleten/98.008",
                discovery_method="careers_page_link",
            )
        )


# -- the adapter, offline --------------------------------------------------------


def test_the_identity_is_both_parts_or_nothing() -> None:
    provider = ComeetProvider(_fetcher())
    assert provider.validate_board(BOARD) is None
    for bad in ("tripleten", "98.008", "tripleten/98008", "TripleTen/98.008", ""):
        assert provider.validate_board(
            BoardRef(company_slug="x", provider="comeet", board_identifier=bad)
        ), bad
    assert provider.board_url(BOARD) == "https://www.comeet.com/jobs/tripleten/98.008"


def test_one_vacancy_per_base_uid_with_every_location(conn: Any) -> None:
    """335 rows were 47 vacancies on the live board. Four rows of `62.F64`
    are one posting whose location is all four, base row first."""
    requests: list[str] = []
    stubs = {s.external_id: s for s in ComeetProvider(_fetcher(requests)).list_postings(BOARD)}

    assert set(stubs) == {CRM, TEACHING, "comeet-98.008-C3.F61", "comeet-98.008-63.46B"}
    crm = stubs[CRM]
    assert crm.title == "CRM Systems & Automation Manager"
    assert crm.location_raw == "Madrid, ES / Belgrade, RS / Lisbon, PT / Warszawa, PL"
    assert crm.payload["workplace"] == "remote"
    assert crm.payload["employment_type"] == "Full-time"
    assert crm.payload["location_countries"] == ["ES", "RS", "PT", "PL"]
    assert [v["uid"] for v in crm.payload["location_variants"]] == [
        "62.F64",
        "62.F64-AE.302",
        "62.F64-DA.406",
        "62.F64-3C.40F",
    ]
    # The vendor's own page URL, as served (its slug writes `&` as `--`).
    assert crm.url == (
        "https://www.comeet.com/jobs/tripleten/98.008/crm-systems--automation-manager/62.F64"
    )
    # The advert came with the list: sections in order, headed.
    assert crm.description_html and crm.description_html.startswith("<h3>Description</h3>")
    assert "<h3>Requirements</h3>" in crm.description_html
    # No posted date is invented from `time_updated`.
    assert crm.posted_at is None
    # Two requests for a whole board: the hosted page, then the list.
    assert len(requests) == 2
    assert requests[0] == "https://www.comeet.com/jobs/tripleten/98.008"
    assert requests[1].startswith("https://www.comeet.co/careers-api/2.0/company/98.008/positions")


def test_the_token_reaches_no_payload_and_no_error(conn: Any) -> None:
    stubs = list(ComeetProvider(_fetcher()).list_postings(BOARD))
    for stub in stubs:
        assert "token" not in json.dumps(stub.payload).lower()
        assert stub.payload["position_url"].endswith("/positions/" + stub.payload["uid"])

    # A page that publishes no token stops the read with an error naming
    # the page, not a guess and not a request to the API.
    requests: list[str] = []
    provider = ComeetProvider(_fetcher(requests, page="<html>Request for consent</html>"))
    with pytest.raises(FetchError) as caught:
        list(provider.list_postings(BOARD))
    assert "did not publish the board's token" in caught.value.message
    assert requests == ["https://www.comeet.com/jobs/tripleten/98.008"]

    assert scrub_token("https://x.example/p?token=SECRET&details=true") == (
        "https://x.example/p?details=true"
    )


def test_brazil_on_the_list_and_brazil_absent_are_two_different_postings(conn: Any) -> None:
    stubs = {s.external_id: s for s in ComeetProvider(_fetcher()).list_postings(BOARD)}
    assert "BR" in stubs[TEACHING].payload["location_countries"]
    assert stubs[TEACHING].location_raw.startswith("Remote, BR / ")
    assert "BR" not in stubs[CRM].payload["location_countries"]
    onsite = stubs["comeet-98.008-63.46B"]
    assert onsite.payload["workplace"] == "onsite"
    assert onsite.payload["employment_type"] is None  # `Hourly` is not a relationship


def test_helpers() -> None:
    assert base_uid("62.F64-AE.302") == "62.F64"
    assert base_uid("62.F64") == "62.F64"
    assert base_uid("nonsense") is None
    assert location_label({"city": "Lisbon", "country": "PT"}) == "Lisbon, PT"
    assert location_label({"city": "", "country": "", "name": "Remote, based in LATAM"}) == (
        "Remote, based in LATAM"
    )
    assert location_label(None) is None
    assert compose_description(None) == ""
    assert read_compensation({"salary_range": None}) is None
    band = read_compensation(
        {"salary_range": {"min": 4000, "max": 6000, "currency_code": "usd", "period": "Month"}}
    )
    assert band is not None
    assert (band.min_value, band.max_value, band.currency, band.period) == (
        4000.0,
        6000.0,
        "USD",
        "monthly",
    )
    assert read_compensation({"salary_range": {"min": 0, "currency_code": "USD"}}) is None


def test_a_variant_url_resolves_to_the_vacancy() -> None:
    """Remotive linked the Serbia row. It is the same vacancy."""
    serbia = (
        "https://www.comeet.com/jobs/tripleten/98.008/crm-systems-automation-manager/62.F64-AE.302"
    )
    assert recognise_posting_url(serbia) == CRM
    assert identify_posting_url(serbia) == ("comeet", CRM)
    assert recognise_board_url(serbia) == ("tripleten/98.008", CRM)
    assert recognise_posting_url("https://www.comeet.com/jobs/tripleten/98.008") is None


# -- through the normal runner ------------------------------------------------------


def test_a_registered_board_is_collected_by_the_normal_runner(conn: Any) -> None:
    _register(conn)
    with _fetcher() as fetcher:
        stats = Collector(conn, fetcher).collect_all()
    assert stats.boards_attempted == stats.boards_succeeded == 1
    assert stats.jobs_new == 4
    rows = conn.execute(
        "SELECT external_id, location_raw, url, collection_status FROM job ORDER BY external_id"
    ).fetchall()
    assert [r["external_id"] for r in rows] == [
        TEACHING,
        CRM,
        "comeet-98.008-63.46B",
        "comeet-98.008-C3.F61",
    ]
    crm = next(r for r in rows if r["external_id"] == CRM)
    assert crm["location_raw"] == "Madrid, ES / Belgrade, RS / Lisbon, PT / Warszawa, PL"
    assert crm["url"].startswith("https://www.comeet.com/jobs/tripleten/98.008/")


def test_the_employers_own_list_outranks_an_aggregators_worldwide(conn: Any) -> None:
    """THE TRIPLETEN CASE. Comeet publishes four countries with `is_remote`;
    Remotive republishes the same vacancy with `Worldwide`. One posting, one
    aggregator sighting, and the candidate in Brazil is refused by the
    employer's own list -- the sighting's scope never becomes the answer.
    The vacancy that lists Brazil on the same board is eligible."""
    _register(conn)
    with _fetcher() as fetcher:
        Collector(conn, fetcher).collect_all()
    crm_id = conn.execute("SELECT id FROM job WHERE external_id = ?", (CRM,)).fetchone()["id"]
    teaching_id = conn.execute("SELECT id FROM job WHERE external_id = ?", (TEACHING,)).fetchone()[
        "id"
    ]
    with transaction(conn):
        DiscoverySourceRepo(conn).record(
            crm_id,
            "remotive",
            "5380328",
            "ORIGIN_URL",
            url="https://remotive.com/remote/jobs/all-others/crm-systems-automation-manager-5380328",
            origin_url=(
                "https://www.comeet.com/jobs/tripleten/98.008/crm-systems-automation-manager/"
                "62.F64-AE.302"
            ),
            payload={"candidate_required_location": "Worldwide"},
        )

    config, _ = load_search_config(committed_config_dir())
    assert config.eligibility.candidate_country == "BR"
    rescore(conn, config)

    def status(job_id: str) -> str:
        return conn.execute(
            "SELECT eligibility_status FROM job_match WHERE job_id = ?", (job_id,)
        ).fetchone()["eligibility_status"]

    assert status(crm_id) == "VERIFIED_NOT_ELIGIBLE"
    assert status(teaching_id) == "VERIFIED_ELIGIBLE"
    # Still ONE job with ONE sighting; the aggregator never became a row.
    assert conn.execute("SELECT COUNT(*) FROM job WHERE external_id = ?", (CRM,)).fetchone()[0] == 1
    sightings = conn.execute(
        "SELECT source, matched_by FROM job_discovery_source WHERE job_id = ?", (crm_id,)
    ).fetchall()
    assert [(s["source"], s["matched_by"]) for s in sightings] == [("remotive", "ORIGIN_URL")]
