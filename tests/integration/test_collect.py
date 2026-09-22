"""End-to-end collection against a scripted board.

The scenarios that matter are the lifecycle ones. Everything here is offline
and deterministic: a fake board whose response we control between runs.
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


def job(job_id: int, title: str, content: str = "<p>Own our HubSpot instance.</p>") -> dict:
    return {
        "id": job_id,
        "title": title,
        "absolute_url": f"https://boards.greenhouse.io/exampleco/jobs/{job_id}",
        "location": {"name": "Remote - United States"},
        "updated_at": "2026-08-20T10:00:00Z",
        "content": content,
    }


class ScriptedBoard:
    """A board whose next response the test decides."""

    def __init__(self) -> None:
        self.response: httpx.Response | None = None
        self.exception: Exception | None = None
        self.calls = 0

    def set_jobs(self, jobs: list[dict]) -> None:
        self.exception = None
        self.response = httpx.Response(200, json={"jobs": jobs})

    def set_status(self, status: int) -> None:
        self.exception = None
        self.response = httpx.Response(status)

    def set_timeout(self) -> None:
        self.exception = httpx.ReadTimeout("board timed out")

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.exception is not None:
            raise httpx.ReadTimeout("board timed out", request=request)
        assert self.response is not None
        return httpx.Response(
            self.response.status_code,
            content=self.response.content,
            headers=self.response.headers,
        )


@pytest.fixture
def board() -> ScriptedBoard:
    return ScriptedBoard()


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "career.db")
    migrate(connection)
    with transaction(connection):
        company_id = CompanyRepo(connection).upsert(
            CompanyRecord(slug="example-co", name="Example Co", hq_country="US")
        )
        SourceBoardRepo(connection).upsert(
            SourceBoardRecord(
                company_id=company_id, provider="greenhouse", board_identifier="exampleco"
            )
        )
    yield connection
    connection.close()


def run(conn: sqlite3.Connection, board: ScriptedBoard):
    client = httpx.Client(transport=httpx.MockTransport(board.handler))
    fetcher = HttpFetcher(
        client=client, request_delay_seconds=0.0, backoff_seconds=0.0, sleep=lambda _s: None
    )
    return Collector(conn, fetcher).collect_all(use_cache=False)


def rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM job ORDER BY external_id").fetchall()


# --- basic collection ------------------------------------------------------


def test_first_run_persists_every_posting(conn, board) -> None:
    board.set_jobs([job(1, "Business Systems Analyst"), job(2, "Graphic Designer")])
    stats = run(conn, board)

    assert stats.boards_succeeded == 1
    assert stats.postings_observed == 2
    assert stats.jobs_new == 2
    assert stats.descriptions_non_empty == 2
    assert len(rows(conn)) == 2


def test_the_corpus_is_the_whole_board_not_a_relevant_subset(conn, board) -> None:
    """Collection Universe is not Analysis Universe. A monitored company's
    Graphic Designer and Legal Counsel postings are stored too; narrowing
    happens later, at the relevance prefilter, and never deletes from here."""
    board.set_jobs(
        [
            job(1, "Business Systems Analyst"),
            job(2, "Graphic Designer"),
            job(3, "Legal Counsel"),
            job(4, "Account Executive"),
        ]
    )
    run(conn, board)
    titles = {row["title"] for row in rows(conn)}
    assert titles == {
        "Business Systems Analyst",
        "Graphic Designer",
        "Legal Counsel",
        "Account Executive",
    }


def test_empty_description_is_counted_not_dropped(conn, board) -> None:
    board.set_jobs([job(1, "No Description", content="")])
    stats = run(conn, board)

    assert stats.descriptions_empty == 1
    assert stats.descriptions_non_empty == 0
    assert len(rows(conn)) == 1, "the job is still stored"
    assert rows(conn)[0]["content_hash"] is None


# --- idempotence -----------------------------------------------------------


def test_second_run_creates_no_duplicates(conn, board) -> None:
    board.set_jobs([job(1, "Business Systems Analyst"), job(2, "Graphic Designer")])
    run(conn, board)
    before = len(rows(conn))

    stats = run(conn, board)
    after = len(rows(conn))

    assert before == after == 2
    assert stats.jobs_new == 0
    assert stats.jobs_seen_again == 2


def test_first_seen_is_preserved_and_last_seen_advances(conn, board) -> None:
    board.set_jobs([job(1, "Business Systems Analyst")])
    run(conn, board)
    first = rows(conn)[0]

    run(conn, board)
    second = rows(conn)[0]

    assert second["first_seen_at"] == first["first_seen_at"]
    assert second["last_seen_at"] >= first["last_seen_at"]
    assert second["id"] == first["id"], "identity must be stable"


# --- content change --------------------------------------------------------


def test_changed_description_keeps_one_job_and_adds_a_raw_observation(conn, board) -> None:
    """History without identity fragmentation: an edited posting is the same
    posting, but the previous text survives as its own immutable row."""
    board.set_jobs([job(1, "Business Systems Analyst", "<p>Own our HubSpot instance.</p>")])
    run(conn, board)
    original_hash = rows(conn)[0]["content_hash"]

    board.set_jobs([job(1, "Business Systems Analyst", "<p>Own our HubSpot and Stripe stack.</p>")])
    stats = run(conn, board)

    assert stats.jobs_changed == 1
    assert len(rows(conn)) == 1, "an edit does not create a second job"

    new_hash = rows(conn)[0]["content_hash"]
    assert new_hash != original_hash

    stored = conn.execute("SELECT COUNT(*) AS n FROM job_raw").fetchone()["n"]
    assert stored == 2, "the earlier text is retained as a separate observation"

    previous = conn.execute(
        "SELECT description_text FROM job_raw WHERE content_hash = ?", (original_hash,)
    ).fetchone()
    assert "HubSpot instance" in previous["description_text"]


def test_title_change_does_not_create_a_new_job(conn, board) -> None:
    board.set_jobs([job(1, "Business Systems Analyst")])
    run(conn, board)
    original_id = rows(conn)[0]["id"]

    board.set_jobs([job(1, "Business Technology Analyst")])
    run(conn, board)

    assert len(rows(conn)) == 1
    assert rows(conn)[0]["id"] == original_id
    assert rows(conn)[0]["title"] == "Business Technology Analyst"


def test_identical_descriptions_share_one_raw_row(conn, board) -> None:
    """Real boards repost the same role. Content addressing means one stored
    description and, later, one extraction."""
    same = "<p>Identical text.</p>"
    board.set_jobs([job(1, "Role A", same), job(2, "Role B", same)])
    run(conn, board)

    assert len(rows(conn)) == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM job_raw").fetchone()["n"] == 1


# --- closing lifecycle: the load-bearing part ------------------------------


def test_disappearance_after_a_successful_pass_closes_the_job(conn, board) -> None:
    board.set_jobs([job(1, "A"), job(2, "B"), job(3, "C")])
    run(conn, board)
    assert all(row["closed_at"] is None for row in rows(conn))

    board.set_jobs([job(1, "A"), job(2, "B")])
    stats = run(conn, board)

    assert stats.jobs_closed == 1
    closed = [row for row in rows(conn) if row["closed_at"] is not None]
    assert len(closed) == 1
    assert closed[0]["external_id"] == "3"
    assert closed[0]["collection_status"] == "CLOSED"


def test_a_posting_that_comes_back_stops_being_closed_in_both_columns(conn, board) -> None:
    """THE FIRST BROKEN INTEGRITY FINDING THIS CORPUS HAS EVER CARRIED.

    `upsert_seen` cleared `closed_at` on a returning posting and left
    `collection_status` reading `CLOSED`, forever. Latent until a board lost and
    regained postings at scale: one production run on 2026-09-09 closed 931,
    and `career-agent integrity` then reported 37 rows where the two disagree.

    Both columns describe the same fact, and a row that says a live posting is
    closed is one that anything reading the status will hide.
    """
    board.set_jobs([job(1, "A"), job(2, "B")])
    run(conn, board)

    board.set_jobs([job(1, "A")])
    run(conn, board)
    gone = next(row for row in rows(conn) if row["external_id"] == "2")
    assert gone["closed_at"] is not None
    assert gone["collection_status"] == "CLOSED"

    # And back. Companies repost, and a board drops a posting for an afternoon.
    board.set_jobs([job(1, "A"), job(2, "B")])
    run(conn, board)
    back = next(row for row in rows(conn) if row["external_id"] == "2")
    assert back["closed_at"] is None
    assert back["collection_status"] != "CLOSED"

    disagreeing = conn.execute(
        "SELECT COUNT(*) AS n FROM job"
        " WHERE (closed_at IS NULL AND collection_status = 'CLOSED')"
        "    OR (closed_at IS NOT NULL AND collection_status != 'CLOSED')"
    ).fetchone()["n"]
    assert disagreeing == 0, "which is what `career-agent integrity` asks"


def test_a_routine_pass_does_not_reset_the_status_of_a_posting_that_never_left(conn, board) -> None:
    """THE OPPOSITE DEFECT, and the reason the fix is conditional.

    Overwriting `collection_status` on every pass would erase progress: a
    posting that reached `NORMALISED` would drop back to whatever a lighter
    pass happened to hand in. The status is restored only for a row that was
    actually closed.
    """
    board.set_jobs([job(1, "A")])
    run(conn, board)
    first = rows(conn)[0]["collection_status"]

    conn.execute("UPDATE job SET collection_status = 'EVALUATED'")
    conn.commit()

    run(conn, board)
    assert rows(conn)[0]["collection_status"] == "EVALUATED", (
        f"a routine pass reset it to {first!r}"
    )


def test_a_board_timeout_closes_nothing(conn, board) -> None:
    """The scenario this whole milestone is arranged around. A board that times
    out tells us nothing about which of its jobs still exist; inferring 'they
    all closed' from a network problem would destroy a company's history."""
    board.set_jobs([job(1, "A"), job(2, "B"), job(3, "C")])
    run(conn, board)
    before = {row["external_id"]: row["last_seen_at"] for row in rows(conn)}

    board.set_timeout()
    stats = run(conn, board)

    assert stats.boards_failed == 1
    assert stats.boards_succeeded == 0
    assert stats.jobs_closed == 0
    assert all(row["closed_at"] is None for row in rows(conn)), "nothing may close"
    assert {row["external_id"]: row["last_seen_at"] for row in rows(conn)} == before


def test_a_failed_board_does_not_advance_last_collected_at(conn, board) -> None:
    """If a failure advanced this timestamp, the NEXT pass would treat every
    unseen job as missing and close them all."""
    board.set_jobs([job(1, "A")])
    run(conn, board)
    after_success = conn.execute("SELECT * FROM source_board").fetchone()["last_collected_at"]
    assert after_success is not None

    board.set_timeout()
    run(conn, board)
    row = conn.execute("SELECT * FROM source_board").fetchone()

    assert row["last_collected_at"] == after_success
    assert "RETRY_EXHAUSTED" in row["last_error"]
    assert "timed out" in row["last_error"], "the underlying cause stays diagnosable"


def test_recovery_after_a_failure_resumes_normally(conn, board) -> None:
    board.set_jobs([job(1, "A"), job(2, "B")])
    run(conn, board)

    board.set_timeout()
    run(conn, board)

    board.set_jobs([job(1, "A"), job(2, "B")])
    stats = run(conn, board)

    assert stats.boards_succeeded == 1
    assert stats.jobs_closed == 0
    assert all(row["closed_at"] is None for row in rows(conn))
    assert conn.execute("SELECT * FROM source_board").fetchone()["last_error"] is None


def test_http_error_closes_nothing(conn, board) -> None:
    board.set_jobs([job(1, "A")])
    run(conn, board)

    board.set_status(500)
    stats = run(conn, board)

    assert stats.boards_failed == 1
    assert stats.jobs_closed == 0
    assert rows(conn)[0]["closed_at"] is None


def test_board_not_found_closes_nothing(conn, board) -> None:
    """A 404 might mean the board moved, not that every job vanished."""
    board.set_jobs([job(1, "A")])
    run(conn, board)

    board.set_status(404)
    stats = run(conn, board)

    assert stats.boards_failed == 1
    assert stats.failures[0].category == "NOT_FOUND"
    assert stats.jobs_closed == 0
    assert rows(conn)[0]["closed_at"] is None


def test_malformed_response_closes_nothing(conn, board) -> None:
    board.set_jobs([job(1, "A")])
    run(conn, board)

    board.response = httpx.Response(200, text="<html>not json</html>")
    board.exception = None
    stats = run(conn, board)

    assert stats.boards_failed == 1
    assert stats.jobs_closed == 0
    assert rows(conn)[0]["closed_at"] is None


def test_a_genuinely_empty_board_does_close_its_jobs(conn, board) -> None:
    """The other half of the rule. A company that closed every role is real,
    and it is distinguishable from a failure because the request succeeded."""
    board.set_jobs([job(1, "A"), job(2, "B")])
    run(conn, board)

    board.set_jobs([])
    stats = run(conn, board)

    assert stats.boards_succeeded == 1
    assert stats.boards_failed == 0
    assert stats.jobs_closed == 2
    assert all(row["closed_at"] is not None for row in rows(conn))


def test_a_reopened_posting_is_reopened_not_duplicated(conn, board) -> None:
    board.set_jobs([job(1, "A"), job(2, "B")])
    run(conn, board)
    board.set_jobs([job(1, "A")])
    run(conn, board)
    assert any(row["closed_at"] is not None for row in rows(conn))

    board.set_jobs([job(1, "A"), job(2, "B")])
    run(conn, board)

    assert len(rows(conn)) == 2
    assert all(row["closed_at"] is None for row in rows(conn))


# --- provenance ------------------------------------------------------------


def test_the_original_payload_stays_retrievable(conn, board) -> None:
    original = job(1, "Business Systems Analyst")
    board.set_jobs([original])
    run(conn, board)

    job_id = rows(conn)[0]["id"]
    archived = ProviderPayloadRepo(conn).latest_for_job(job_id)

    assert archived == original
    assert archived["location"]["name"] == "Remote - United States"
    assert archived["updated_at"] == "2026-08-20T10:00:00Z"


def test_a_changed_payload_is_archived_alongside_the_old_one(conn, board) -> None:
    board.set_jobs([job(1, "A", "<p>first</p>")])
    run(conn, board)
    board.set_jobs([job(1, "A", "<p>second</p>")])
    run(conn, board)

    job_id = rows(conn)[0]["id"]
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM job_provider_payload WHERE job_id = ?", (job_id,)
    ).fetchone()["n"]
    assert count == 2, "prior observations are never overwritten"


def test_an_unchanged_payload_is_not_archived_twice(conn, board) -> None:
    board.set_jobs([job(1, "A")])
    run(conn, board)
    run(conn, board)

    assert conn.execute("SELECT COUNT(*) AS n FROM job_provider_payload").fetchone()["n"] == 1


def test_raw_html_is_recoverable_after_normalisation(conn, board) -> None:
    board.set_jobs([job(1, "A", "<h3>Requirements</h3><ul><li>HubSpot</li></ul>")])
    run(conn, board)

    raw = conn.execute("SELECT * FROM job_raw").fetchone()
    assert "<h3>" in raw["description_html"], "original markup preserved"
    assert "Requirements" in raw["description_text"]
    assert "- HubSpot" in raw["description_text"], "structure preserved in the text"


# --- run observability -----------------------------------------------------


def test_the_run_is_recorded_with_its_statistics(conn, board) -> None:
    board.set_jobs([job(1, "A"), job(2, "B")])
    run(conn, board)

    row = conn.execute("SELECT * FROM pipeline_run ORDER BY started_at DESC").fetchone()
    assert row["stage"] == "collect"
    assert row["status"] == "OK"
    assert '"jobs_new": 2' in row["stats_json"]
    assert row["finished_at"].endswith("Z")


def test_a_failed_run_is_recorded_as_failed_with_diagnostics(conn, board) -> None:
    board.set_timeout()
    stats = run(conn, board)

    row = conn.execute("SELECT * FROM pipeline_run ORDER BY started_at DESC").fetchone()
    assert row["status"] == "FAILED"
    assert "1 board(s) failed" in row["error"]

    failure = stats.failures[0]
    assert failure.category == "RETRY_EXHAUSTED"
    assert failure.company_slug == "example-co"
    assert failure.board_identifier == "exampleco"
    assert failure.occurred_at.endswith("Z")


# =========================================================================
# The plan, and the 4,860 rows that are not a plan
# =========================================================================


def test_a_feed_row_is_not_something_this_runner_collects_from(tmp_path) -> None:
    """THE DEFECT THAT MADE THE PRODUCTION PATH UNUSABLE.

    Every feed collector registers one `source_board` per EMPLOYER, and it is
    right to: ADR-0008 says a board belongs to one company, and that row is what
    attributes a Gupy posting to whoever wrote it rather than to a stream. It is
    an ATTRIBUTION record, not a collection plan.

    This runner could not tell the difference. Measured on 2026-09-09 after the
    deep Gupy collection: 5,140 `source_board` rows, of which 3,794 are Gupy
    employers, 591 Himalayas, 142 Speedrun. 280 are boards this runner can
    actually ask about. A run left going for forty minutes reached EIGHTEEN, and
    the forty-six ATS boards added that day were never reached at all.

    `addresses_boards_by_company` is the question, and it already existed --
    `discover` has asked it since V1.4 to decide what may be probed. The
    collector had never asked it.
    """
    import httpx

    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.collect import Collector
    from career_agent.storage.db import connect, migrate, transaction
    from career_agent.storage.records import CompanyRecord, SourceBoardRecord
    from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo

    conn = connect(tmp_path / "plan.db")
    migrate(conn)
    with transaction(conn):
        companies, boards = CompanyRepo(conn), SourceBoardRepo(conn)
        for slug, provider in (
            ("acme", "greenhouse"),
            ("padaria-do-ze", "gupy"),
            ("some-remote-shop", "himalayas"),
        ):
            company_id = companies.upsert(CompanyRecord(slug=slug, name=slug))
            boards.upsert(
                SourceBoardRecord(
                    company_id=company_id,
                    provider=provider,
                    board_identifier=slug,
                    board_url=None,
                    discovery_method="test",
                )
            )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": []})

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        stats = Collector(conn, fetcher).collect_all()
    finally:
        fetcher.close()
        conn.close()

    assert stats.boards_attempted == 1, "only the Greenhouse row is a board to collect from"
    assert stats.boards_not_this_runners == 2
    # NOT failures and NOT rejections. A feed's attribution row is healthy.
    assert stats.boards_rejected == 0
    assert stats.failures == []
    assert stats.as_dict()["boards_not_this_runners"] == 2


def test_a_feed_row_is_never_marked_as_a_board_in_error(tmp_path) -> None:
    """The half that would have been worse than the slowness.

    `source-health` reports "N of M boards last recorded an error". Marking
    every feed attribution row NOT_ADDRESSABLE would have printed "3,794 of
    3,794 boards last recorded an error" about a source that is working
    perfectly, which is how a health report becomes something people learn to
    ignore.
    """
    import httpx

    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.collect import Collector
    from career_agent.storage.db import connect, migrate, transaction
    from career_agent.storage.records import CompanyRecord, SourceBoardRecord
    from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo

    conn = connect(tmp_path / "health.db")
    migrate(conn)
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(CompanyRecord(slug="padaria", name="Padaria"))
        SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company_id,
                provider="gupy",
                board_identifier="padaria",
                board_url=None,
                discovery_method="gupy_feed",
            )
        )

    fetcher = HttpFetcher(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"jobs": []}))
        )
    )
    try:
        Collector(conn, fetcher).collect_all()
    finally:
        fetcher.close()

    row = conn.execute("SELECT last_error FROM source_board").fetchone()
    conn.close()
    assert row["last_error"] is None


def test_a_run_where_most_boards_worked_is_not_a_failed_run(tmp_path) -> None:
    """A FRACTION, NEVER A STATE.

    The status read `OK if boards_failed == 0`, so a production run where 276
    boards of 280 collected perfectly reported FAILED -- because four companies
    had taken their boards down. V1.2 made exactly this correction to
    `source-health`: "Three boards of 116 recording NOT_FOUND is a company that
    took its board down, not a broken connector."

    A run status that goes red for that is a red light people learn to ignore.
    FAILED now means the run collected NOTHING while trying, and the per-board
    failures are named in the stats either way.
    """
    import httpx

    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.collect import Collector
    from career_agent.storage.db import connect, migrate, transaction
    from career_agent.storage.records import CompanyRecord, SourceBoardRecord
    from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo

    conn = connect(tmp_path / "fraction.db")
    migrate(conn)
    with transaction(conn):
        companies, boards = CompanyRepo(conn), SourceBoardRepo(conn)
        for slug in ("works", "gone"):
            company_id = companies.upsert(CompanyRecord(slug=slug, name=slug))
            boards.upsert(
                SourceBoardRecord(
                    company_id=company_id,
                    provider="greenhouse",
                    board_identifier=slug,
                    board_url=None,
                    discovery_method="test",
                )
            )

    def handler(request: httpx.Request) -> httpx.Response:
        if "gone" in str(request.url):
            return httpx.Response(404, text="no such board")
        return httpx.Response(200, json={"jobs": []})

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        stats = Collector(conn, fetcher).collect_all()
    finally:
        fetcher.close()

    assert stats.boards_succeeded == 1
    assert stats.boards_failed == 1
    assert stats.failures, "the dead board is still named"

    row = conn.execute(
        "SELECT status FROM pipeline_run WHERE stage = 'collect' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row["status"] == "OK"


def test_a_run_where_every_board_failed_is_a_failed_run(tmp_path) -> None:
    """The other direction, which is what the status is for."""
    import httpx

    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.collect import Collector
    from career_agent.storage.db import connect, migrate, transaction
    from career_agent.storage.records import CompanyRecord, SourceBoardRecord
    from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo

    conn = connect(tmp_path / "allgone.db")
    migrate(conn)
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(CompanyRecord(slug="gone", name="gone"))
        SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company_id,
                provider="greenhouse",
                board_identifier="gone",
                board_url=None,
                discovery_method="test",
            )
        )

    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(404, text="x")))
    )
    try:
        Collector(conn, fetcher).collect_all()
    finally:
        fetcher.close()

    row = conn.execute(
        "SELECT status FROM pipeline_run WHERE stage = 'collect' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row["status"] == "FAILED"
