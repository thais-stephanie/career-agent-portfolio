"""The mass-disappearance hold.

Closing is already safe against every failure the pipeline can see. This guards
the one it cannot: a pass that succeeds while returning wrong data. A board of
300 that answers 200 OK with 12 postings would close 288, and every closure
would look legitimate.

The policy is deliberately conservative. A false alarm delays some closures by
one pass; a false negative erases hundreds of jobs.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from career_agent.pipeline.collect import Collector, DisappearancePolicy
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo


def job(job_id: int) -> dict:
    return {
        "id": job_id,
        "title": f"Role {job_id}",
        "absolute_url": f"https://boards.greenhouse.io/ghco/jobs/{job_id}",
        "location": {"name": "Remote"},
        "content": f"<p>Description for role {job_id}.</p>",
    }


class Board:
    """A board whose next response the test decides."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []
        self.fails = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fails:
            raise httpx.ReadTimeout("board timed out", request=request)
        return httpx.Response(200, json={"jobs": self.jobs})


@pytest.fixture
def board() -> Board:
    return Board()


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "career.db")
    migrate(connection)
    with transaction(connection):
        company_id = CompanyRepo(connection).upsert(
            CompanyRecord(slug="gh-co", name="Co", canonical_domain="ghco.com")
        )
        SourceBoardRepo(connection).upsert(
            SourceBoardRecord(company_id=company_id, provider="greenhouse", board_identifier="ghco")
        )
    yield connection
    connection.close()


def run(conn: sqlite3.Connection, board: Board, policy: DisappearancePolicy | None = None):
    client = httpx.Client(transport=httpx.MockTransport(board.handler))
    from career_agent.net.fetcher import HttpFetcher

    fetcher = HttpFetcher(
        client=client, request_delay_seconds=0.0, backoff_seconds=0.0, sleep=lambda _s: None
    )
    return Collector(conn, fetcher, policy=policy).collect_all(use_cache=False)


def open_jobs(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) n FROM job WHERE closed_at IS NULL").fetchone()["n"])


def board_row(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM source_board").fetchone()


def populate(board: Board, count: int) -> None:
    board.jobs = [job(i) for i in range(count)]


# --- the policy in isolation -----------------------------------------------


@pytest.mark.parametrize(
    ("previous", "missing", "expected"),
    [
        (100, 10, False),  # 10% -- ordinary churn
        (100, 49, False),  # just under the threshold
        (100, 50, True),  # exactly at it
        (100, 90, True),
        (100, 100, True),
        (19, 19, False),  # below the minimum board size, whatever the ratio
        (20, 10, True),  # exactly at the minimum, exactly at the ratio
        (0, 0, False),  # a board that never had anything
    ],
    ids=["10pc", "49pc", "50pc", "90pc", "100pc", "small-board", "at-minimum", "empty-history"],
)
def test_policy_thresholds(previous: int, missing: int, expected: bool) -> None:
    assert DisappearancePolicy().is_suspicious(previous, missing) is expected


def test_the_provisional_defaults_are_what_the_brief_says() -> None:
    """PROVISIONAL -- revisit after real scheduled history. Pinned so the
    numbers cannot drift away from the document that justifies them."""
    policy = DisappearancePolicy()
    assert policy.minimum_previous_open == 20
    assert policy.suspicious_drop_ratio == 0.50


# --- the required matrix, end to end ---------------------------------------


def test_100_to_90_is_a_normal_decline(conn, board) -> None:
    populate(board, 100)
    run(conn, board)

    populate(board, 90)
    stats = run(conn, board)

    assert stats.jobs_closed == 10
    assert stats.boards_held == 0
    assert open_jobs(conn) == 90
    assert board_row(conn)["suspicious_since"] is None


def test_100_to_55_is_still_a_normal_decline(conn, board) -> None:
    """45% missing: under the threshold, so it closes. The guard is for
    responses that look wrong, not for bad quarters."""
    populate(board, 100)
    run(conn, board)

    populate(board, 55)
    stats = run(conn, board)

    assert stats.jobs_closed == 45
    assert stats.boards_held == 0
    assert open_jobs(conn) == 55


def test_100_to_10_is_held_and_closes_nothing(conn, board) -> None:
    populate(board, 100)
    run(conn, board)

    populate(board, 10)
    stats = run(conn, board)

    assert stats.boards_succeeded == 1, "the board answered; this is not a failure"
    assert stats.boards_held == 1
    assert stats.jobs_closed == 0
    assert open_jobs(conn) == 100, "nothing closed on the first anomalous pass"

    hold = stats.holds[0]
    assert (hold.previously_open, hold.observed, hold.would_have_closed) == (100, 10, 90)

    row = board_row(conn)
    assert row["suspicious_since"] is not None
    assert row["suspicious_observed"] == 10
    assert row["last_collected_at"] is not None, "it did answer, so it was collected"


def test_100_to_0_does_not_destroy_history_on_the_first_pass(conn, board) -> None:
    """A valid empty response is normally grounds for closing -- that rule was
    proved three times in M1A-M1C. It stops being trustworthy when the board had
    100 postings yesterday: "every role closed overnight" is a rarer explanation
    than "this response is wrong"."""
    populate(board, 100)
    run(conn, board)

    populate(board, 0)
    stats = run(conn, board)

    assert stats.boards_held == 1
    assert stats.jobs_closed == 0
    assert open_jobs(conn) == 100
    assert board_row(conn)["suspicious_observed"] == 0


def test_a_small_board_emptying_closes_normally(conn, board) -> None:
    """10 -> 0 is below the minimum board size, so the rule does not apply.
    Documented rather than silent: on a small board an empty response is
    unremarkable, and holding every one of them would train the reader to
    ignore holds."""
    populate(board, 10)
    run(conn, board)

    populate(board, 0)
    stats = run(conn, board)

    assert stats.boards_held == 0
    assert stats.jobs_closed == 10
    assert open_jobs(conn) == 0


def test_a_suspicious_board_that_recovers_clears_the_hold(conn, board) -> None:
    """100 -> 10 -> 100."""
    populate(board, 100)
    run(conn, board)
    populate(board, 10)
    run(conn, board)
    assert board_row(conn)["suspicious_since"] is not None

    populate(board, 100)
    stats = run(conn, board)

    assert stats.boards_recovered == 1
    assert stats.boards_held == 0
    assert stats.jobs_closed == 0
    assert open_jobs(conn) == 100
    assert board_row(conn)["suspicious_since"] is None
    assert board_row(conn)["suspicious_observed"] is None


def test_a_partial_recovery_that_is_still_suspicious_stays_held(conn, board) -> None:
    """100 -> 10 -> 40. Better, but still 60% missing, and larger than the first
    observation -- so it is neither confirmation nor recovery. Holding again is
    the conservative reading."""
    populate(board, 100)
    run(conn, board)
    populate(board, 10)
    run(conn, board)
    held_since = board_row(conn)["suspicious_since"]

    populate(board, 40)
    stats = run(conn, board)

    assert stats.boards_held == 1
    assert stats.jobs_closed == 0
    assert open_jobs(conn) == 100
    assert board_row(conn)["suspicious_since"] == held_since, "still held since the first time"
    assert board_row(conn)["suspicious_observed"] == 40


def test_a_suspicious_board_that_stays_down_is_confirmed(conn, board) -> None:
    """100 -> 10 -> 10. Twice in a row and no better the second time, so the
    delayed closures happen now."""
    populate(board, 100)
    run(conn, board)
    populate(board, 10)
    run(conn, board)
    assert open_jobs(conn) == 100

    stats = run(conn, board)

    assert stats.boards_confirmed_drop == 1
    assert stats.boards_held == 0
    assert stats.jobs_closed == 90
    assert open_jobs(conn) == 10
    assert board_row(conn)["suspicious_since"] is None


# --- a provider failure can never confirm ----------------------------------


def test_a_failure_cannot_confirm_a_suspicious_disappearance(conn, board) -> None:
    """The property this guard would be worthless without.

    100 -> 10 (held) -> timeout -> timeout. If a failure could confirm, two
    network problems in a row would close 90 postings that are probably still
    open. It cannot, because failures return before the guard is reached.
    """
    populate(board, 100)
    run(conn, board)
    populate(board, 10)
    run(conn, board)
    held_since = board_row(conn)["suspicious_since"]
    collected_at = board_row(conn)["last_collected_at"]

    board.fails = True
    for _ in range(2):
        stats = run(conn, board)
        assert stats.boards_failed == 1
        assert stats.boards_confirmed_drop == 0
        assert stats.jobs_closed == 0

    assert open_jobs(conn) == 100
    row = board_row(conn)
    assert row["suspicious_since"] == held_since, "the hold is untouched by a failure"
    assert row["suspicious_observed"] == 10
    assert row["last_collected_at"] == collected_at, "and the board was not marked collected"


def test_a_failure_never_starts_a_hold_either(conn, board) -> None:
    populate(board, 100)
    run(conn, board)

    board.fails = True
    stats = run(conn, board)

    assert stats.boards_failed == 1
    assert stats.boards_held == 0
    assert board_row(conn)["suspicious_since"] is None
    assert open_jobs(conn) == 100


def test_recovery_after_a_failure_still_works(conn, board) -> None:
    populate(board, 100)
    run(conn, board)
    populate(board, 10)
    run(conn, board)

    board.fails = True
    run(conn, board)
    board.fails = False
    populate(board, 100)
    stats = run(conn, board)

    assert stats.boards_recovered == 1
    assert open_jobs(conn) == 100
    assert board_row(conn)["suspicious_since"] is None


# --- the guard does not change anything else -------------------------------


def test_postings_actually_observed_are_still_updated_while_held(conn, board) -> None:
    """Only the closing is withheld. The board answered, so what it did say is
    recorded -- including an edited description."""
    populate(board, 100)
    run(conn, board)

    board.jobs = [job(0)]
    board.jobs[0]["content"] = "<p>Rewritten description.</p>"
    stats = run(conn, board)

    assert stats.boards_held == 1
    assert stats.jobs_seen_again == 1
    assert stats.jobs_changed == 1

    row = conn.execute(
        "SELECT r.description_text t FROM job j JOIN job_raw r ON r.content_hash = j.content_hash"
        " WHERE j.external_id = '0'"
    ).fetchone()
    assert row["t"] == "Rewritten description."


def test_a_growing_board_is_never_suspicious(conn, board) -> None:
    populate(board, 20)
    run(conn, board)

    populate(board, 200)
    stats = run(conn, board)

    assert stats.boards_held == 0
    assert stats.jobs_new == 180
    assert open_jobs(conn) == 200


def test_a_stricter_policy_can_be_injected(conn, board) -> None:
    """The policy is a typed object rather than a constant precisely so it can
    be varied without editing the collector -- and so `sources.yaml` does not
    have to pretend to configure something the runtime never reads."""
    populate(board, 30)
    run(conn, board)

    populate(board, 25)
    strict = DisappearancePolicy(minimum_previous_open=10, suspicious_drop_ratio=0.10)
    stats = run(conn, board, policy=strict)

    assert stats.boards_held == 1
    assert stats.jobs_closed == 0
    assert open_jobs(conn) == 30


def test_holds_are_recorded_on_the_run_for_later_inspection(conn, board) -> None:
    populate(board, 100)
    run(conn, board)
    populate(board, 5)
    run(conn, board)

    row = conn.execute(
        "SELECT * FROM pipeline_run WHERE stage = 'collect' ORDER BY started_at DESC, id DESC"
    ).fetchone()
    assert '"boards_held": 1' in row["stats_json"]
    assert '"would_have_closed": 95' in row["stats_json"]

    held = SourceBoardRepo(conn).suspicious()
    assert len(held) == 1
    assert held[0]["company_slug"] == "gh-co"
    assert held[0]["suspicious_observed"] == 5
