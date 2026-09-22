"""Two providers, one pipeline, one database, one run.

`test_collect.py` proves the collection lifecycle against a single vendor. That
cannot show whether the pipeline is provider-neutral, because any interface fits
one implementation. This file runs Greenhouse and Lever boards through the same
unmodified `Collector` in the same pass and asserts that nothing downstream
behaves differently for either.

Everything here is offline: the transport routes by hostname and returns
whichever vendor's shape the test decided on.
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

GH_HOST = "boards-api.greenhouse.io"
LV_HOST = "api.lever.co"


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
        "id": f"{suffix}" * 36,
        "text": title,
        "hostedUrl": f"https://jobs.lever.co/lvco/{suffix * 36}",
        "categories": {
            "team": "Revenue Operations",
            "location": "Remote - United States",
            "commitment": "Full-time",
        },
        # No salaryRange on purpose: Lever declares the dimension, this posting
        # does not state it, and the two must stay distinguishable.
        "createdAt": 1780000000000,
        "workplaceType": "remote",
        "country": "US",
        "description": opening,
        "lists": [{"text": "Requirements", "content": "<li>Own our HubSpot instance.</li>"}],
        "additional": "<p>We are an equal opportunity employer.</p>",
    }


class TwoBoards:
    """One transport serving both vendors, routed by hostname."""

    def __init__(self) -> None:
        self.greenhouse: object = {"jobs": []}
        self.lever: object = []
        self.greenhouse_fails = False
        self.lever_fails = False
        self.calls: dict[str, int] = {GH_HOST: 0, LV_HOST: 0}

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.calls[host] = self.calls.get(host, 0) + 1
        if host == GH_HOST:
            if self.greenhouse_fails:
                raise httpx.ReadTimeout("greenhouse timed out", request=request)
            return httpx.Response(200, json=self.greenhouse)
        if self.lever_fails:
            raise httpx.ReadTimeout("lever timed out", request=request)
        return httpx.Response(200, json=self.lever)


@pytest.fixture
def boards() -> TwoBoards:
    return TwoBoards()


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
        ):
            company_id = companies.upsert(CompanyRecord(slug=slug, name=name, hq_country="US"))
            source_boards.upsert(
                SourceBoardRecord(
                    company_id=company_id, provider=provider, board_identifier=identifier
                )
            )
    yield connection
    connection.close()


def run(conn: sqlite3.Connection, boards: TwoBoards):
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


# --- both vendors through one unmodified pipeline --------------------------


def test_one_pass_collects_both_providers(conn, boards) -> None:
    boards.greenhouse = {"jobs": [gh_job(1, "Business Systems Analyst"), gh_job(2, "Designer")]}
    boards.lever = [lv_job("a", "Revenue Systems Analyst"), lv_job("b", "Counsel")]

    stats = run(conn, boards)

    assert stats.boards_attempted == 2
    assert stats.boards_succeeded == 2
    assert stats.postings_observed == 4
    assert stats.jobs_new == 4
    assert len(rows(conn, "greenhouse")) == 2
    assert len(rows(conn, "lever")) == 2


def test_one_request_per_board_at_both_providers(conn, boards) -> None:
    """Both expose the full description in the listing, so `fetch_posting` is a
    local conversion. Four postings, two requests."""
    boards.greenhouse = {"jobs": [gh_job(1, "A"), gh_job(2, "B")]}
    boards.lever = [lv_job("a", "A"), lv_job("b", "B")]

    run(conn, boards)

    assert boards.calls == {GH_HOST: 1, LV_HOST: 1}


def test_stored_rows_are_indistinguishable_in_shape(conn, boards) -> None:
    """The point of the abstraction: a Lever row and a Greenhouse row differ in
    their values, never in which columns are populated."""
    boards.greenhouse = {"jobs": [gh_job(1, "Business Systems Analyst")]}
    boards.lever = [lv_job("a", "Revenue Systems Analyst")]

    run(conn, boards)
    gh = rows(conn, "greenhouse")[0]
    lv = rows(conn, "lever")[0]

    for column in ("url", "title", "department", "location_raw", "posted_at", "content_hash"):
        assert gh[column], f"greenhouse {column} empty"
        assert lv[column], f"lever {column} empty"
    assert gh["department"] == lv["department"] == "Revenue Operations"
    assert gh["location_raw"] == lv["location_raw"] == "Remote - United States"
    assert gh["collection_status"] == lv["collection_status"] == "NORMALISED"


def test_posted_at_from_both_providers_is_stored_as_utc(conn, boards) -> None:
    """The A7.2 contract, checked where it actually matters: in the column.

    Greenhouse states `2026-06-01T09:00:00-04:00` and Lever an epoch, and both
    land as UTC. Two encodings in one column would make every later freshness
    calculation a guess dressed as arithmetic.
    """
    from datetime import datetime

    boards.greenhouse = {"jobs": [gh_job(1, "A")]}
    boards.lever = [lv_job("a", "A")]
    run(conn, boards)

    values = [row["posted_at"] for row in rows(conn)]
    assert len(values) == 2
    for value in values:
        assert len(value) == 25
        assert value.endswith("+00:00")
        assert datetime.fromisoformat(value).utcoffset().total_seconds() == 0

    greenhouse_value = rows(conn, "greenhouse")[0]["posted_at"]
    assert greenhouse_value == "2026-06-01T13:00:00+00:00", "converted, not passed through"
    assert (
        ProviderPayloadRepo(conn).latest_for_job(rows(conn, "greenhouse")[0]["id"])[
            "first_published"
        ]
        == "2026-06-01T09:00:00-04:00"
    ), "the provider's own encoding survives in the payload"


def test_identical_text_from_two_providers_shares_one_raw_row(conn, boards) -> None:
    """Content addressing is provider-blind. Two vendors carrying the same
    description cost one stored row and, later, one extraction -- without any
    cross-provider deduplication of `job` rows, which M1B does not do."""
    shared = "<p>Exactly the same words.</p>"
    boards.greenhouse = {"jobs": [gh_job(1, "A", shared)]}
    boards.lever = [{**lv_job("a", "A"), "description": shared, "lists": [], "additional": ""}]

    run(conn, boards)

    assert len(rows(conn)) == 2, "two jobs: no cross-provider merging"
    assert conn.execute("SELECT COUNT(*) AS n FROM job_raw").fetchone()["n"] == 1


def test_lever_sections_survive_into_the_stored_text(conn, boards) -> None:
    boards.greenhouse = {"jobs": []}
    boards.lever = [lv_job("a", "Revenue Systems Analyst")]
    run(conn, boards)

    raw = conn.execute("SELECT * FROM job_raw").fetchone()
    lines = raw["description_text"].split("\n")

    assert "Requirements" in lines
    assert "- Own our HubSpot instance." in lines
    assert "We are an equal opportunity employer." in lines


# --- isolation: one vendor's failure never touches the other ---------------


def test_a_lever_failure_leaves_greenhouse_untouched(conn, boards) -> None:
    boards.greenhouse = {"jobs": [gh_job(1, "A"), gh_job(2, "B")]}
    boards.lever = [lv_job("a", "A"), lv_job("b", "B")]
    run(conn, boards)

    def board_state() -> dict[str, sqlite3.Row]:
        return {
            row["provider"]: row for row in conn.execute("SELECT * FROM source_board").fetchall()
        }

    before = board_state()

    boards.lever_fails = True
    stats = run(conn, boards)

    assert stats.boards_succeeded == 1
    assert stats.boards_failed == 1
    assert stats.failures[0].provider == "lever"
    assert stats.jobs_closed == 0
    assert all(row["closed_at"] is None for row in rows(conn))

    after = board_state()
    assert after["greenhouse"]["last_error"] is None
    assert "timed out" in after["lever"]["last_error"]
    # Compared against its own earlier value rather than against the healthy
    # board's: these timestamps have second resolution, and both passes land in
    # the same second, so any cross-board comparison silently proves nothing.
    assert after["lever"]["last_collected_at"] == before["lever"]["last_collected_at"]


def test_an_empty_lever_board_closes_only_its_own_jobs(conn, boards) -> None:
    """Lever's empty board is HTTP 200 with `[]`, which is grounds for closing.
    The Greenhouse board's postings must be entirely unaffected."""
    boards.greenhouse = {"jobs": [gh_job(1, "A")]}
    boards.lever = [lv_job("a", "A"), lv_job("b", "B")]
    run(conn, boards)

    boards.lever = []
    stats = run(conn, boards)

    assert stats.boards_failed == 0
    assert stats.jobs_closed == 2
    assert all(row["closed_at"] is not None for row in rows(conn, "lever"))
    assert all(row["closed_at"] is None for row in rows(conn, "greenhouse"))


def test_a_lever_error_object_served_as_json_closes_nothing(conn, boards) -> None:
    """Lever answers an unknown board with a 404 whose body is valid JSON. If
    that ever reached the pipeline as "no postings", it would close every job
    the company has. It must arrive as a failure instead."""
    boards.greenhouse = {"jobs": [gh_job(1, "A")]}
    boards.lever = [lv_job("a", "A"), lv_job("b", "B")]
    run(conn, boards)

    boards.lever = {"ok": False, "error": "Document not found"}
    stats = run(conn, boards)

    assert stats.boards_failed == 1
    assert stats.failures[0].category == "MALFORMED"
    assert stats.jobs_closed == 0
    assert all(row["closed_at"] is None for row in rows(conn))


def test_both_providers_archive_their_payloads_verbatim(conn, boards) -> None:
    gh = gh_job(1, "A")
    lv = lv_job("a", "A")
    boards.greenhouse = {"jobs": [gh]}
    boards.lever = [lv]
    run(conn, boards)

    payloads = ProviderPayloadRepo(conn)
    assert payloads.latest_for_job(rows(conn, "greenhouse")[0]["id"]) == gh
    stored_lever = payloads.latest_for_job(rows(conn, "lever")[0]["id"])

    assert stored_lever == lv
    assert stored_lever["workplaceType"] == "remote", "unreachable neutrally today, but not lost"
    assert stored_lever["country"] == "US"


# --- provider metadata reaches neutral code without leaking paths ----------


def test_metadata_transport_carries_both_providers_in_one_run(conn, boards) -> None:
    """The seam is exercised on every collection rather than sitting dormant.

    Lever declares five paths across four dimensions and Greenhouse one; the run
    reports what actually resolved. A dormant seam rots -- this is what turns a
    vendor renaming a field into a number instead of a surprise at M2.
    """
    boards.greenhouse = {"jobs": [gh_job(1, "A"), gh_job(2, "B")]}
    boards.lever = [lv_job("a", "A"), lv_job("b", "B")]

    stats = run(conn, boards)
    metadata = stats.metadata

    assert metadata["paths_declared"] == 6  # 1 greenhouse + 5 lever
    assert metadata["paths_resolved"] == 5
    assert metadata["paths_unresolved"] == ["lever:salaryRange"]
    assert metadata["by_dimension"] == {
        "employment_type_hint": 2,
        "hiring_location_hint": 6,  # 2 greenhouse + 2x2 lever (country + location)
        "work_model_hint": 2,
    }
    assert "compensation_hint" not in metadata["by_dimension"], (
        "declared by lever, absent from these postings -- the employer did not state it"
    )


def test_an_unanswered_declared_path_is_reported_not_swallowed(conn, boards) -> None:
    """A Lever board where nothing states workplace type or commitment. The run
    must say so: silence about a declared path is exactly the signal that a
    vendor changed something."""
    stripped = lv_job("a", "A")
    for key in ("workplaceType", "country"):
        stripped.pop(key)
    stripped["categories"] = {"team": "Ops", "location": "Remote"}
    boards.greenhouse = {"jobs": []}
    boards.lever = [stripped]

    stats = run(conn, boards)

    assert sorted(stats.metadata["paths_unresolved"]) == [
        # The Greenhouse board was empty, so its declared path went unanswered
        # too -- a declaration is made when the board is walked, not when a
        # posting happens to carry the field.
        "greenhouse:location.name",
        "lever:categories.commitment",
        "lever:country",
        "lever:salaryRange",
        "lever:workplaceType",
    ]
    assert stats.metadata["by_dimension"] == {"hiring_location_hint": 1}


def test_a_stored_payload_still_answers_its_own_observations(conn, boards) -> None:
    """End to end, the M2 shape: resolve metadata from the ARCHIVED payload, and
    verify each observation back against it.

    Nothing in this test names a vendor path. It asks the registry for the
    provider, the provider for its field map, and neutral code for the rest --
    which is the entire claim the mechanism makes.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.providers.base import resolve_metadata, verify_provider_field
    from career_agent.providers.registry import get_provider

    boards.greenhouse = {"jobs": [gh_job(1, "A")]}
    boards.lever = [lv_job("a", "A")]
    run(conn, boards)

    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(boards.handler)),
        request_delay_seconds=0.0,
    )
    payloads = ProviderPayloadRepo(conn)
    checked = 0

    for row in rows(conn):
        provider = get_provider(row["provider"], fetcher)
        archived = payloads.latest_for_job(row["id"])
        assert archived is not None
        for observation in resolve_metadata(provider.name, provider.field_map, archived):
            assert observation.provider == row["provider"]
            assert (
                verify_provider_field(
                    archived, observation.source_field, observation.source_value
                ).value
                == "FIELD_MATCH"
            )
            checked += 1

    assert checked == 5, "1 greenhouse location + 4 lever fields, each proved against its payload"
