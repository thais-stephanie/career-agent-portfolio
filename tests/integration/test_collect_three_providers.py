"""Three providers, one pipeline, one database, one run.

`test_collect_multi_provider.py` stays as the M1B two-provider regression. This
file asks the M1C question: does the *generic* lifecycle still work when a third,
structurally different vendor joins it -- and does Ashby get its own code path
anywhere? It should not.

Offline: the transport routes by hostname and returns whichever vendor's shape
the test decided on.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.collect import Collector
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, ProviderPayloadRepo, SourceBoardRepo

HOSTS = {
    "boards-api.greenhouse.io": "greenhouse",
    "api.lever.co": "lever",
    "api.ashbyhq.com": "ashby",
}


def gh_job(job_id: int, title: str, content: str = "<p>Own our HubSpot instance.</p>") -> dict:
    return {
        "id": job_id,
        "title": title,
        "absolute_url": f"https://boards.greenhouse.io/ghco/jobs/{job_id}",
        "location": {"name": "Remote - United States"},
        "first_published": "2026-06-01T09:00:00-04:00",
        "departments": [{"id": 1, "name": "Revenue Operations"}],
        "content": content,
    }


def lv_job(suffix: str, title: str, opening: str = "<p>About us.</p>") -> dict:
    return {
        "id": suffix * 36,
        "text": title,
        "hostedUrl": f"https://jobs.lever.co/lvco/{suffix * 36}",
        "categories": {
            "team": "Revenue Operations",
            "location": "Remote - United States",
            "commitment": "Full-time",
        },
        "createdAt": 1780000000000,
        "workplaceType": "remote",
        "country": "US",
        "description": opening,
        "lists": [{"text": "Requirements", "content": "<li>Own our HubSpot instance.</li>"}],
        "additional": "<p>We are an equal opportunity employer.</p>",
    }


def ab_job(suffix: str, title: str, body: str = "<p>Own our HubSpot instance.</p>") -> dict:
    return {
        "id": suffix * 36,
        "title": title,
        "jobUrl": f"https://jobs.ashbyhq.com/abco/{suffix * 36}",
        "applyUrl": f"https://jobs.ashbyhq.com/abco/{suffix * 36}/application",
        "department": "Revenue Operations",
        "team": "Revenue Operations",
        "location": "Remote - United States",
        "secondaryLocations": [
            {"location": "NYC Office", "address": {"postalAddress": {"addressCountry": "USA"}}}
        ],
        "address": {"postalAddress": {"addressCountry": "USA", "addressLocality": "New York"}},
        "workplaceType": "Remote",
        "isRemote": True,
        "employmentType": "FullTime",
        "isListed": True,
        "publishedAt": "2026-06-01T13:00:00.442+00:00",
        "compensation": {
            "compensationTierSummary": "$150K - $200K",
            "scrapeableCompensationSalarySummary": "$150K - $200K",
            "compensationTiers": [],
            "summaryComponents": [],
        },
        "shouldDisplayCompensationOnJobPostings": True,
        "descriptionHtml": body,
        "descriptionPlain": "OWN OUR HUBSPOT INSTANCE.",
    }


class ThreeBoards:
    """One transport serving three vendors, routed by hostname."""

    def __init__(self) -> None:
        self.bodies: dict[str, object] = {
            "greenhouse": {"jobs": []},
            "lever": [],
            "ashby": {"apiVersion": "1", "jobs": []},
        }
        self.failing: set[str] = set()
        self.calls: dict[str, int] = dict.fromkeys(HOSTS.values(), 0)

    def handler(self, request: httpx.Request) -> httpx.Response:
        provider = HOSTS[request.url.host]
        self.calls[provider] += 1
        if provider in self.failing:
            raise httpx.ReadTimeout(f"{provider} timed out", request=request)
        return httpx.Response(200, json=self.bodies[provider])


@pytest.fixture
def boards() -> ThreeBoards:
    return ThreeBoards()


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "career.db")
    migrate(connection)
    with transaction(connection):
        companies = CompanyRepo(connection)
        source_boards = SourceBoardRepo(connection)
        for slug, name, provider, identifier in (
            ("gh-co", "Greenhouse Co", "greenhouse", "ghco"),
            ("lv-co", "Lever Co", "lever", "lvco"),
            ("ab-co", "Ashby Co", "ashby", "abco"),
        ):
            company_id = companies.upsert(CompanyRecord(slug=slug, name=name, hq_country="US"))
            source_boards.upsert(
                SourceBoardRecord(
                    company_id=company_id, provider=provider, board_identifier=identifier
                )
            )
    yield connection
    connection.close()


def run(conn: sqlite3.Connection, boards: ThreeBoards):
    client = httpx.Client(transport=httpx.MockTransport(boards.handler))
    fetcher = HttpFetcher(
        client=client, request_delay_seconds=0.0, backoff_seconds=0.0, sleep=lambda _s: None
    )
    return Collector(conn, fetcher).collect_all(use_cache=False)


def rows(conn: sqlite3.Connection, provider: str | None = None) -> list[sqlite3.Row]:
    if provider is None:
        return conn.execute("SELECT * FROM job ORDER BY provider, external_id").fetchall()
    return conn.execute(
        "SELECT * FROM job WHERE provider = ? ORDER BY external_id", (provider,)
    ).fetchall()


def populate(boards: ThreeBoards, n: int = 2) -> None:
    letters = "abcdefgh"[:n]
    boards.bodies["greenhouse"] = {"jobs": [gh_job(i + 1, f"GH {i}") for i in range(n)]}
    boards.bodies["lever"] = [lv_job(letters[i], f"LV {i}") for i in range(n)]
    boards.bodies["ashby"] = {
        "apiVersion": "1",
        "jobs": [ab_job(letters[i], f"AB {i}") for i in range(n)],
    }


# --- one pass, three vendors ------------------------------------------------


def test_one_pass_collects_all_three(conn, boards) -> None:
    populate(boards, 2)
    stats = run(conn, boards)

    assert stats.boards_attempted == 3
    assert stats.boards_succeeded == 3
    assert stats.postings_observed == 6
    assert stats.jobs_new == 6
    assert boards.calls == {"greenhouse": 1, "lever": 1, "ashby": 1}
    for provider in ("greenhouse", "lever", "ashby"):
        assert len(rows(conn, provider)) == 2


def test_stored_rows_are_indistinguishable_in_shape(conn, boards) -> None:
    """The whole claim: three vendors' rows differ in their values, never in
    which columns are populated."""
    populate(boards, 1)
    run(conn, boards)

    for column in ("url", "title", "department", "location_raw", "posted_at", "content_hash"):
        values = {r["provider"]: r[column] for r in rows(conn)}
        assert len(values) == 3
        assert all(values.values()), f"{column} empty for {[k for k, v in values.items() if not v]}"

    assert {r["department"] for r in rows(conn)} == {"Revenue Operations"}
    assert {r["location_raw"] for r in rows(conn)} == {"Remote - United States"}
    assert {r["collection_status"] for r in rows(conn)} == {"NORMALISED"}


def test_posted_at_from_all_three_is_utc(conn, boards) -> None:
    from datetime import datetime

    populate(boards, 1)
    run(conn, boards)

    values = {r["provider"]: r["posted_at"] for r in rows(conn)}
    assert len(values) == 3
    for provider, value in values.items():
        assert len(value) == 25, f"{provider} is not the contract length"
        assert value.endswith("+00:00")
        assert datetime.fromisoformat(value).utcoffset().total_seconds() == 0
    # The same instant, stated three different ways by three vendors:
    #   greenhouse "2026-06-01T09:00:00-04:00"     -- a local offset
    #   ashby      "2026-06-01T13:00:00.442+00:00" -- UTC with milliseconds
    # both converge on one comparable value.
    assert values["greenhouse"] == values["ashby"] == "2026-06-01T13:00:00+00:00"


# --- the generic lifecycle, with a third vendor in it -----------------------


def test_second_pass_is_idempotent_across_all_three(conn, boards) -> None:
    populate(boards, 2)
    run(conn, boards)
    before = {r["external_id"]: (r["id"], r["first_seen_at"]) for r in rows(conn)}

    stats = run(conn, boards)
    after = {r["external_id"]: (r["id"], r["first_seen_at"]) for r in rows(conn)}

    assert stats.jobs_new == 0
    assert stats.jobs_seen_again == 6
    assert stats.jobs_closed == 0
    assert len(rows(conn)) == 6
    assert before == after, "identity and first_seen_at are stable"
    assert all(r["last_seen_at"] >= r["first_seen_at"] for r in rows(conn))


def test_a_changed_ashby_description_keeps_one_job_and_adds_a_raw_row(conn, boards) -> None:
    populate(boards, 1)
    run(conn, boards)
    original_id = rows(conn, "ashby")[0]["id"]
    original_hash = rows(conn, "ashby")[0]["content_hash"]

    boards.bodies["ashby"] = {
        "apiVersion": "1",
        "jobs": [ab_job("a", "AB 0", "<p>Own our HubSpot and Stripe stack.</p>")],
    }
    stats = run(conn, boards)

    assert stats.jobs_changed == 1
    assert len(rows(conn, "ashby")) == 1, "an edit does not create a second job"
    assert rows(conn, "ashby")[0]["id"] == original_id
    assert rows(conn, "ashby")[0]["content_hash"] != original_hash

    previous = conn.execute(
        "SELECT description_text FROM job_raw WHERE content_hash = ?", (original_hash,)
    ).fetchone()
    assert "HubSpot instance" in previous["description_text"], "the earlier text is retained"


def test_an_ashby_disappearance_closes_only_that_posting(conn, boards) -> None:
    populate(boards, 2)
    run(conn, boards)

    boards.bodies["ashby"] = {"apiVersion": "1", "jobs": [ab_job("a", "AB 0")]}
    stats = run(conn, boards)

    assert stats.jobs_closed == 1
    closed = [r for r in rows(conn) if r["closed_at"] is not None]
    assert len(closed) == 1
    assert closed[0]["provider"] == "ashby"
    assert closed[0]["collection_status"] == "CLOSED"


# --- failure safety, third time -------------------------------------------


def test_an_ashby_failure_closes_nothing_and_spares_the_others(conn, boards) -> None:
    populate(boards, 2)
    run(conn, boards)

    def board_state() -> dict[str, sqlite3.Row]:
        return {r["provider"]: r for r in conn.execute("SELECT * FROM source_board").fetchall()}

    before = board_state()

    boards.failing = {"ashby"}
    stats = run(conn, boards)
    after = board_state()

    assert stats.boards_succeeded == 2
    assert stats.boards_failed == 1
    assert stats.failures[0].provider == "ashby"
    assert stats.jobs_closed == 0
    assert all(r["closed_at"] is None for r in rows(conn)), "nothing may close"

    # Compared against its own earlier value, not against a healthy board's:
    # these timestamps have second resolution and both passes land in the same
    # second, so a cross-board comparison would silently prove nothing.
    assert after["ashby"]["last_collected_at"] == before["ashby"]["last_collected_at"]
    assert "timed out" in after["ashby"]["last_error"]
    assert after["greenhouse"]["last_error"] is None
    assert after["lever"]["last_error"] is None


def test_an_empty_ashby_board_closes_only_its_own(conn, boards) -> None:
    """Ashby's empty board is 200 with an empty jobs array -- grounds for
    closing. The other two providers must be entirely unaffected."""
    populate(boards, 2)
    run(conn, boards)

    boards.bodies["ashby"] = {"apiVersion": "1", "jobs": []}
    stats = run(conn, boards)

    assert stats.boards_failed == 0
    assert stats.jobs_closed == 2
    assert all(r["closed_at"] is not None for r in rows(conn, "ashby"))
    assert all(r["closed_at"] is None for r in rows(conn, "greenhouse"))
    assert all(r["closed_at"] is None for r in rows(conn, "lever"))


def test_a_malformed_ashby_envelope_closes_nothing(conn, boards) -> None:
    populate(boards, 2)
    run(conn, boards)

    boards.bodies["ashby"] = {"apiVersion": "1"}  # jobs key gone
    stats = run(conn, boards)

    assert stats.boards_failed == 1
    assert stats.failures[0].category == "MALFORMED"
    assert stats.jobs_closed == 0
    assert all(r["closed_at"] is None for r in rows(conn))


# --- metadata transport, three providers -----------------------------------


def test_metadata_transport_carries_all_three(conn, boards) -> None:
    populate(boards, 1)
    stats = run(conn, boards)
    metadata = stats.metadata

    # 1 greenhouse + 5 lever + 7 ashby
    assert metadata["paths_declared"] == 13
    assert metadata["by_dimension"] == {
        "compensation_hint": 2,  # ashby states it two ways
        "employment_type_hint": 2,
        "hiring_location_hint": 6,  # 1 gh + 2 lever + 3 ashby
        "work_model_hint": 2,
    }
    # The Lever posting in this fixture states no salary, so the path it
    # declares goes unanswered -- and is reported rather than swallowed. That is
    # the difference between "the employer did not disclose" and "we stopped
    # looking".
    assert metadata["paths_unresolved"] == ["lever:salaryRange"]


def test_every_observation_verifies_against_its_archived_payload(conn, boards) -> None:
    """The M2 loop across three vendors: registry to provider, provider to map,
    neutral code to resolve and neutral code to prove. Nothing here names a
    vendor path."""
    from career_agent.providers.base import resolve_metadata, verify_provider_field
    from career_agent.providers.registry import get_provider

    populate(boards, 2)
    run(conn, boards)

    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(boards.handler)),
        request_delay_seconds=0.0,
    )
    payloads = ProviderPayloadRepo(conn)
    checked: dict[str, int] = {}

    for row in rows(conn):
        provider = get_provider(row["provider"], fetcher)
        archived = payloads.latest_for_job(row["id"])
        assert archived is not None
        for observation in resolve_metadata(provider.name, provider.field_map, archived):
            assert (
                verify_provider_field(
                    archived, observation.source_field, observation.source_value
                ).value
                == "FIELD_MATCH"
            )
            checked[row["provider"]] = checked.get(row["provider"], 0) + 1

    assert checked == {"greenhouse": 2, "lever": 8, "ashby": 14}
