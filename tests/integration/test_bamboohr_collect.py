"""BambooHR, a tenant ATS family (2026-11-03), through the normal board runner.

Every fixture here is invented in the shape `{tenant}.bamboohr.com/careers/
list` and `/careers/<id>/detail` answered on 2026-11-03; tenants, employers
and postings are fictional. Covered: list plus detail into whole adverts with
the employer's own date, an empty board, a malformed item, a duplicated id, a
changed contract, an unknown tenant, a board that disappeared, 500, 429, 403
and a timeout, one posting the board lists and will not serve, a posting
closed between two runs, idempotency, the canonical URL, no invented location
or work model, the cross-source identity of an aggregator's apply link, and
one failing tenant never stopping the others.
"""

from __future__ import annotations

import httpx
import pytest

from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.pipeline.collect import Collector
from career_agent.providers.bamboohr import (
    BambooHRProvider,
    compose_location,
    recognise_board_url,
    recognise_posting_url,
    validate_listing,
)
from career_agent.providers.base import BoardRef
from career_agent.providers.registry import identify_board_url, identify_posting_url
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo

TENANT = "acme-ficticia"


def _row(n: int, **over) -> dict:
    row = {
        "id": str(n),
        "jobOpeningName": f"Customer Onboarding Specialist {n} (fictional)",
        "departmentId": "100",
        "departmentLabel": "Customer Success",
        "employmentStatusLabel": "Full-Time",
        "employmentType": None,
        "location": {"city": "Leeds", "state": "West Yorkshire"},
        "atsLocation": {"country": None, "state": None, "province": None, "city": None},
        "isRemote": None,
        "locationType": "0",
    }
    row.update(over)
    return row


def _detail(n: int, **over) -> dict:
    opening = {
        "jobOpeningShareUrl": f"https://{TENANT}.bamboohr.com/careers/{n}",
        "jobOpeningName": f"Customer Onboarding Specialist {n} (fictional)",
        "jobOpeningStatus": "Open",
        "departmentLabel": "Customer Success",
        "employmentStatusLabel": "Full-Time",
        "location": {
            "city": "Leeds",
            "state": "West Yorkshire",
            "postalCode": None,
            "addressCountry": "United Kingdom",
        },
        "atsLocation": {"country": None, "state": None, "province": None, "city": None},
        "description": f"<p>Acme Ficticia is fictional.</p><ul><li>Duty {n}.</li></ul>",
        "compensation": None,
        "datePosted": "2026-10-15",
        "locationType": "0",
    }
    opening.update(over)
    return {"result": {"jobOpening": opening, "formFields": {"firstName": {"label": "x"}}}}


def _board(tenant: str = TENANT) -> BoardRef:
    return BoardRef(company_slug=tenant, provider="bamboohr", board_identifier=tenant)


def _fetcher(handler) -> HttpFetcher:
    return HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_seconds=0,
        backoff_seconds=0,
        sleep=lambda _s: None,
    )


def _serving(listing: list, details: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/careers/list":
            return httpx.Response(
                200, json={"meta": {"totalCount": len(listing)}, "result": listing}
            )
        posting = path.split("/")[2]
        found = (details or {}).get(posting, _detail(int(posting)))
        return found if isinstance(found, httpx.Response) else httpx.Response(200, json=found)

    return handler


def test_a_tenant_list_and_its_details_become_whole_adverts_with_the_employers_date() -> None:
    asked: list[str] = []
    inner = _serving([_row(41), _row(42, isRemote=True)])

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return inner(request)

    provider = BambooHRProvider(_fetcher(handler))
    stubs = list(provider.list_postings(_board()))
    assert [s.external_id for s in stubs] == [
        "bamboohr-acme-ficticia-41",
        "bamboohr-acme-ficticia-42",
    ]
    assert stubs[0].posted_at is None, "the list carries no date and none is invented"
    raw = provider.fetch_posting(_board(), stubs[0])
    assert asked == [
        "https://acme-ficticia.bamboohr.com/careers/list",
        "https://acme-ficticia.bamboohr.com/careers/41/detail",
    ]
    assert raw.stub.url == "https://acme-ficticia.bamboohr.com/careers/41"
    assert raw.stub.posted_at == "2026-10-15T00:00:00+00:00"
    assert raw.stub.location_raw == "Leeds, West Yorkshire, United Kingdom"
    assert raw.stub.department == "Customer Success"
    assert "Duty 41" in raw.description_text
    assert "formFields" not in raw.payload, "the application form is not the job"
    assert raw.payload["workplace"] is None, "isRemote null and locationType unread: unknown"
    assert raw.payload["locationType"] == "0", "archived verbatim"
    assert stubs[1].payload["workplace"] == "remote"


def test_a_missing_date_or_place_stays_missing() -> None:
    provider = BambooHRProvider(
        _fetcher(
            _serving(
                [_row(1, location={"city": None, "state": None})],
                {"1": _detail(1, datePosted=None, location={"city": None, "addressCountry": None})},
            )
        )
    )
    (stub,) = provider.list_postings(_board())
    assert stub.location_raw is None, "never Remote, never Worldwide"
    raw = provider.fetch_posting(_board(), stub)
    assert raw.stub.posted_at is None and raw.stub.location_raw is None


def test_an_empty_board_is_an_honest_empty_list() -> None:
    provider = BambooHRProvider(_fetcher(_serving([])))
    assert list(provider.list_postings(_board())) == []


def test_a_malformed_item_is_skipped_not_fatal() -> None:
    rows = [
        _row(1),
        "not an object",
        _row(2, jobOpeningName=""),
        _row(3, id="x9"),
        _row(4, id=None),
    ]
    provider = BambooHRProvider(_fetcher(_serving(rows)))
    assert [s.external_id for s in provider.list_postings(_board())] == ["bamboohr-acme-ficticia-1"]
    assert provider.postings_skipped == 4


def test_a_duplicated_id_is_an_error_not_a_silent_merge() -> None:
    provider = BambooHRProvider(_fetcher(_serving([_row(1), _row(1)])))
    with pytest.raises(FetchError, match="duplicate"):
        list(provider.list_postings(_board()))


@pytest.mark.parametrize("body", [{"result": None}, {"jobs": []}, [], "text"])
def test_a_changed_contract_is_refused_not_read_as_empty(body) -> None:
    provider = BambooHRProvider(_fetcher(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(FetchError):
        list(provider.list_postings(_board()))
    assert validate_listing({"meta": {}, "result": []}) is None


def test_an_unknown_tenant_redirecting_to_the_vendor_is_an_error_never_an_empty_board() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.bamboohr.com":
            return httpx.Response(200, text="<html>marketing page</html>")
        return httpx.Response(302, headers={"location": "https://www.bamboohr.com/"})

    with pytest.raises(FetchError, match="not valid JSON"):
        list(BambooHRProvider(_fetcher(handler)).list_postings(_board("no-such-ficticia")))


def test_a_detail_without_a_job_opening_is_refused() -> None:
    provider = BambooHRProvider(_fetcher(_serving([_row(1)], {"1": {"result": {}}})))
    (stub,) = provider.list_postings(_board())
    with pytest.raises(FetchError, match="jobOpening"):
        provider.fetch_posting(_board(), stub)


@pytest.mark.parametrize("identifier", ["", "Acme Corp", "www", "api", "a" * 70, "acme.evil"])
def test_a_tenant_that_is_not_one_dns_label_is_refused_before_a_request(identifier) -> None:
    asked: list[str] = []
    provider = BambooHRProvider(_fetcher(lambda r: asked.append(str(r.url)) or httpx.Response(200)))
    with pytest.raises(FetchError):
        list(provider.list_postings(_board(identifier)))
    assert asked == []


def test_a_location_does_not_repeat_itself() -> None:
    assert compose_location({"city": "London", "state": "London", "addressCountry": "UK"}) == (
        "London, UK"
    )
    assert compose_location({}) is None and compose_location(None) is None


# -- cross-source identity ------------------------------------------------------


def test_an_aggregators_apply_link_resolves_to_the_stored_external_id() -> None:
    """The deterministic identity ADR-0008 allows: an aggregator's republication
    whose apply link is this posting is the same job, whatever title or id the
    aggregator gave it. Nothing fuzzy merges a repost that does not link it."""
    stored = "bamboohr-acme-ficticia-83"
    for link in (
        "https://acme-ficticia.bamboohr.com/careers/83",
        "https://acme-ficticia.bamboohr.com/careers/83/",
        "https://acme-ficticia.bamboohr.com/careers/83?source=aWQ9MzA%3D",
        "https://ACME-FICTICIA.bamboohr.com/careers/83",
    ):
        assert recognise_posting_url(link) == stored
        assert identify_posting_url(link) == ("bamboohr", stored)
        assert identify_board_url(link) == ("bamboohr", "acme-ficticia", stored)
    for foreign in (
        "https://www.bamboohr.com/careers/83",
        "https://acme-ficticia.bamboohr.com/hiring/jobs/edit/43",
        "http://acme-ficticia.bamboohr.com/careers/83",
        "https://acme-ficticia.bamboohr.com.evil.example/careers/83",
        "https://jobs.ashbyhq.com/acme/83",
    ):
        assert recognise_posting_url(foreign) is None, foreign
    assert recognise_board_url("https://acme-ficticia.bamboohr.com/careers") == (
        "acme-ficticia",
        None,
    )


# -- through the normal board runner ------------------------------------------


def _register(conn, tenant: str) -> None:
    with transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug=tenant, name=tenant))
        SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company, provider="bamboohr", board_identifier=tenant)
        )


def test_one_failing_tenant_never_stops_the_others_and_a_closed_posting_closes(tmp_path) -> None:
    conn = connect(tmp_path / "bamboohr.db")
    migrate(conn)
    tenants = (TENANT, "gone-ficticia", "broken-ficticia", "slow-ficticia",
               "denied-ficticia", "busy-ficticia")  # fmt: skip
    for tenant in tenants:
        _register(conn, tenant)
    listing = {"rows": [_row(1), _row(2), _row(3)]}
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        tenant = request.url.host.split(".")[0]
        if tenant == "gone-ficticia":
            return httpx.Response(404, json={})
        if tenant == "broken-ficticia":
            return httpx.Response(500, text="error")
        if tenant == "slow-ficticia":
            raise httpx.ReadTimeout("timed out", request=request)
        if tenant == "denied-ficticia":
            return httpx.Response(403, text="denied")
        if tenant == "busy-ficticia":
            return httpx.Response(429, text="slow down")
        # Posting 3 is listed and will not serve: counted, never closed.
        return _serving(listing["rows"], {"3": httpx.Response(404, json={})})(request)

    def run():
        with _fetcher(handler) as fetcher:
            return Collector(conn, fetcher).collect_all()

    first = run()
    assert first.boards_attempted == 6
    assert first.boards_succeeded == 1 and first.boards_failed == 5
    assert first.jobs_new == 2 and first.postings_fetch_failed == 1
    gone = conn.execute(
        "SELECT last_error FROM source_board WHERE board_identifier = 'gone-ficticia'"
    ).fetchone()[0]
    assert gone.startswith("NOT_FOUND since"), "a 404 starts the dead-board streak"
    assert sum("busy-ficticia" in u for u in asked) > 1, "429 is retried with backoff"
    assert sum("denied-ficticia" in u for u in asked) == 1, "403 is never retried"
    rows = conn.execute(
        "SELECT external_id, url, posted_at, location_raw FROM job"
        " WHERE provider = 'bamboohr' ORDER BY external_id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("bamboohr-acme-ficticia-1", "https://acme-ficticia.bamboohr.com/careers/1",
         "2026-10-15T00:00:00+00:00", "Leeds, West Yorkshire, United Kingdom"),
        ("bamboohr-acme-ficticia-2", "https://acme-ficticia.bamboohr.com/careers/2",
         "2026-10-15T00:00:00+00:00", "Leeds, West Yorkshire, United Kingdom"),
    ]  # fmt: skip

    # Idempotent: the same answer adds nothing and changes nothing.
    again = run()
    assert again.jobs_new == 0 and again.jobs_seen_again == 2
    assert conn.execute("SELECT count(*) FROM job WHERE provider = 'bamboohr'").fetchone()[0] == 2

    # Posting 2 leaves the list: it closes; posting 3 was never read, so it
    # was never a job and nothing about it is closed.
    listing["rows"] = [_row(1), _row(3)]
    third = run()
    assert third.jobs_new == 0 and third.jobs_closed == 1
    open_ids = {
        r[0]
        for r in conn.execute(
            "SELECT external_id FROM job WHERE provider = 'bamboohr' AND closed_at IS NULL"
        )
    }
    assert open_ids == {"bamboohr-acme-ficticia-1"}
    conn.close()


def test_a_registered_tenant_joins_the_company_an_aggregator_already_created(tmp_path) -> None:
    """A repost on an aggregator under another job id and the employer's own
    posting must share one `company_id`, so the (company, title) grouping
    (migration 0014) shows them as one card. Nothing merges the rows."""
    from career_agent.config.registry import load_registry_file, sync_registry

    conn = connect(tmp_path / "join.db")
    migrate(conn)
    with transaction(conn):
        held = CompanyRepo(conn).upsert(CompanyRecord(slug="acme-ficticia", name="Acme Ficticia"))
        feed = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=held, provider="fourdayweek", board_identifier="all")
        )
        conn.execute(
            "INSERT INTO job (id, company_id, source_board_id, provider, external_id, url, title,"
            " first_seen_at, last_seen_at, collection_status, created_at, updated_at)"
            " VALUES ('agg-1', ?, ?, 'fourdayweek', 'fdw-1', 'https://feed.example/job/1',"
            " 'Customer Onboarding Specialist 1 (fictional)', '2026-11-01T00:00:00Z',"
            " '2026-11-01T00:00:00Z', 'NORMALISED', '2026-11-01T00:00:00Z',"
            " '2026-11-01T00:00:00Z')",
            (held, feed),
        )
    registry = tmp_path / "companies.yaml"
    registry.write_text(
        "schema_version: 1\npurpose: curated_registry\ncompanies:\n"
        '  - slug: acme-ficticia\n    name: "Acme Ficticia"\n'
        '    canonical_domain: "acme-ficticia.example"\n'
        '    discovery_source: "aggregator_apply_link_validated_on_vendor_board"\n'
        '    boards:\n      - provider: bamboohr\n        board_identifier: "acme-ficticia"\n'
        '        discovery_method: "aggregator_apply_link_validated_2026_11_03"\n'
        '        verified_at: "2026-11-03T23:30:00Z"\n',
        encoding="utf-8",
    )
    with transaction(conn):
        assert sync_registry(conn, load_registry_file(registry)) == 1
    with _fetcher(_serving([_row(1)])) as fetcher:
        assert Collector(conn, fetcher).collect_all().jobs_new == 1
    pairs = conn.execute("SELECT provider, company_id, title FROM job ORDER BY provider").fetchall()
    assert [r[0] for r in pairs] == ["bamboohr", "fourdayweek"], "two rows, never merged"
    assert pairs[0][1] == pairs[1][1] == held and pairs[0][2] == pairs[1][2]
    conn.close()
