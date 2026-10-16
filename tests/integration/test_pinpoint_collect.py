"""Pinpoint, a tenant ATS family (2026-10-16), through the normal board runner.

Every fixture here is invented in the shape `{tenant}.pinpointhq.com/
postings.json` answered on 2026-10-16. Covered: success with the whole advert,
an empty board, a malformed item, a duplicated id, a link off the tenant, a
board that disappeared, a board answering 500, a timeout, an abandoned trial
tenant serving the canned demonstration postings, a posting closed between
two runs, the canonical URL, the structured work mode and the visible salary,
and one failing tenant never stopping the others.
"""

from __future__ import annotations

import httpx
import pytest

from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.pipeline.collect import Collector
from career_agent.providers.base import BoardRef
from career_agent.providers.pinpoint import (
    DEMO_MARKER,
    PinpointProvider,
    compose_location,
    read_compensation,
    recognise_board_url,
    validate_listing,
)
from career_agent.providers.registry import identify_board_url
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo


def _posting(n: int, tenant: str = "acme-ficticia", **over) -> dict:
    row = {
        "id": n,
        "title": f"Integration Specialist {n} (fictional)",
        "url": f"https://{tenant}.pinpointhq.com/en/postings/0000000{n}-aaaa-bbbb-cccc-000000000000",
        "path": f"/en/postings/0000000{n}-aaaa-bbbb-cccc-000000000000",
        "description": "<p>We are a fictional company.</p>",
        "key_responsibilities": "<ul><li>Build API integrations.</li></ul>",
        "skills_knowledge_expertise": "<ul><li>HubSpot and Zapier.</li></ul>",
        "benefits": "<p>Remote work.</p>",
        "workplace_type": "remote",
        "employment_type": "permanent_full_time",
        "compensation_visible": False,
        "location": {"name": "Brazil", "city": "Sao Paulo", "province": "SP"},
        "job": {"department": {"name": "Operations"}},
    }
    row.update(over)
    return row


def _board(tenant: str = "acme-ficticia") -> BoardRef:
    return BoardRef(company_slug=tenant, provider="pinpoint", board_identifier=tenant)


def _provider(handler) -> PinpointProvider:
    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)), request_delay_seconds=0
    )
    return PinpointProvider(fetcher)


def test_a_tenant_listing_becomes_whole_adverts_with_a_canonical_url() -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(
            200, json={"data": [_posting(1), _posting(2, workplace_type="hybrid")]}
        )

    stubs = list(_provider(handler).list_postings(_board()))
    assert asked == ["https://acme-ficticia.pinpointhq.com/postings.json"]
    assert [s.external_id for s in stubs] == [
        "pinpoint-acme-ficticia-1",
        "pinpoint-acme-ficticia-2",
    ]
    first = stubs[0]
    assert first.url.startswith("https://acme-ficticia.pinpointhq.com/en/postings/")
    assert (
        "Build API integrations" in first.description_html
        and "Remote work" in first.description_html
    )
    assert first.location_raw == "Sao Paulo, SP, Brazil"
    assert first.payload["workplace"] == "remote" and stubs[1].payload["workplace"] == "hybrid"
    assert first.payload["employment_type"] == "Full-time"
    assert first.department == "Operations"
    assert first.posted_at is None, "Pinpoint publishes no date and none is invented"


def test_an_empty_board_is_an_honest_empty_list() -> None:
    stubs = list(
        _provider(lambda r: httpx.Response(200, json={"data": []})).list_postings(_board())
    )
    assert stubs == []


def test_a_malformed_item_or_a_link_off_the_tenant_is_skipped_not_fatal() -> None:
    rows = [
        _posting(1),
        "not an object",
        _posting(2, title=""),
        _posting(3, url="https://evil.example/postings/3"),
        _posting(4, url="http://acme-ficticia.pinpointhq.com/en/postings/4"),
        _posting(5, url="https://other-tenant.pinpointhq.com/en/postings/5"),
    ]
    provider = _provider(lambda r: httpx.Response(200, json={"data": rows}))
    stubs = list(provider.list_postings(_board()))
    assert [s.external_id for s in stubs] == ["pinpoint-acme-ficticia-1"]
    assert provider.postings_skipped == 5


def test_a_duplicated_id_is_an_error_not_a_silent_merge() -> None:
    provider = _provider(lambda r: httpx.Response(200, json={"data": [_posting(1), _posting(1)]}))
    with pytest.raises(FetchError, match="duplicate"):
        list(provider.list_postings(_board()))


@pytest.mark.parametrize(
    "body",
    [
        {"postings": []},
        {"data": None},
        [],
        {"data": [_posting(1, description=f"<iframe src='https://www.{DEMO_MARKER}x'>")]},
    ],
)
def test_a_changed_contract_or_a_demo_tenant_is_refused(body) -> None:
    provider = _provider(lambda r: httpx.Response(200, json=body))
    with pytest.raises(FetchError):
        list(provider.list_postings(_board()))


def test_the_demo_marker_is_named_when_refused() -> None:
    body = {"data": [_posting(1, benefits=f"<p>{DEMO_MARKER}abc</p>")]}
    assert "demonstration" in (validate_listing(body, "trial-ficticia") or "")
    assert validate_listing({"data": [_posting(1)]}, "acme-ficticia") is None


@pytest.mark.parametrize("identifier", ["", "Acme Corp", "www", "api", "a" * 70, "acme.evil"])
def test_a_tenant_that_is_not_one_dns_label_is_refused_before_a_request(identifier) -> None:
    asked: list[str] = []
    provider = _provider(
        lambda r: asked.append(str(r.url)) or httpx.Response(200, json={"data": []})
    )
    with pytest.raises(FetchError):
        list(provider.list_postings(_board(identifier)))
    assert asked == []


def test_a_visible_salary_is_read_and_a_hidden_one_is_not() -> None:
    shown = _posting(1, compensation_visible=True, compensation_minimum=3000,
                     compensation_maximum=4500, compensation_currency="usd",
                     compensation_frequency="month")  # fmt: skip
    hint = read_compensation(shown)
    assert hint is not None
    assert (hint.min_value, hint.max_value, hint.currency, hint.period) == (
        3000,
        4500,
        "USD",
        "monthly",
    )
    assert read_compensation(dict(shown, compensation_visible=False)) is None


def test_a_location_does_not_repeat_itself() -> None:
    assert (
        compose_location({"location": {"name": "Remote", "city": "", "province": ""}}) == "Remote"
    )
    assert (
        compose_location({"location": {"name": "Peru", "city": "Lima", "province": "Lima"}})
        == "Lima, Peru"
    )
    assert compose_location({}) is None


def test_a_published_posting_url_names_its_tenant() -> None:
    url = "https://acme-ficticia.pinpointhq.com/en/postings/261cc20f-e1ae-4c7a-88ba-8ed633eea78f"
    assert recognise_board_url(url) == ("acme-ficticia", None)
    assert identify_board_url(url) == ("pinpoint", "acme-ficticia", None)
    assert recognise_board_url("https://www.pinpointhq.com/en/postings/261cc20f-e1ae") is None
    assert recognise_board_url("https://acme.pinpointhq.com/about") is None


# -- through the normal board runner ------------------------------------------


def _register(conn, tenant: str) -> None:
    with transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug=tenant, name=tenant))
        SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company, provider="pinpoint", board_identifier=tenant)
        )


def test_one_failing_tenant_never_stops_the_others_and_a_closed_posting_closes(tmp_path) -> None:
    conn = connect(tmp_path / "pinpoint.db")
    migrate(conn)
    for tenant in (
        "acme-ficticia",
        "gone-ficticia",
        "broken-ficticia",
        "slow-ficticia",
        "trial-ficticia",
    ):
        _register(conn, tenant)
    listing = {"acme-ficticia": [_posting(1), _posting(2)]}

    def handler(request: httpx.Request) -> httpx.Response:
        tenant = request.url.host.split(".")[0]
        if tenant == "gone-ficticia":
            return httpx.Response(404, text="not found")
        if tenant == "broken-ficticia":
            return httpx.Response(500, text="error")
        if tenant == "slow-ficticia":
            raise httpx.ReadTimeout("timed out", request=request)
        if tenant == "trial-ficticia":
            return httpx.Response(
                200, json={"data": [_posting(9, tenant=tenant, description=DEMO_MARKER)]}
            )
        return httpx.Response(200, json={"data": listing["acme-ficticia"]})

    def run():
        with HttpFetcher(
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            request_delay_seconds=0,
        ) as fetcher:
            return Collector(conn, fetcher).collect_all()

    first = run()
    assert first.boards_attempted == 5
    assert first.boards_succeeded == 1 and first.boards_failed == 4
    assert first.jobs_new == 2
    urls = [
        r[0] for r in conn.execute("SELECT url FROM job WHERE provider = 'pinpoint' ORDER BY url")
    ]
    assert all(u.startswith("https://acme-ficticia.pinpointhq.com/en/postings/") for u in urls)

    # The second pass sees only posting 1: posting 2 closed; nothing duplicated.
    listing["acme-ficticia"] = [_posting(1)]
    second = run()
    assert second.jobs_new == 0 and second.jobs_seen_again == 1
    open_ids = {
        r[0]
        for r in conn.execute(
            "SELECT external_id FROM job WHERE provider = 'pinpoint' AND closed_at IS NULL"
        )
    }
    assert open_ids == {"pinpoint-acme-ficticia-1"}
    assert conn.execute("SELECT count(*) FROM job WHERE provider = 'pinpoint'").fetchone()[0] == 2
    conn.close()
