"""The re-normalisation backfill: correct, idempotent, and not a fake observation.

The property that matters most is the one that is easiest to get wrong. A
normalisation change and an employer editing a posting both end in "the stored
text is different", and if the backfill takes the second path it silently
falsifies every freshness signal in the corpus.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from career_agent.domain.normalize import content_hash, html_to_text
from career_agent.pipeline.renormalize import renormalize, shared_rows
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
from career_agent.storage.repositories import (
    CompanyRepo,
    JobRawRepo,
    JobRepo,
    SourceBoardRepo,
)

# The defect, in the markup that produced it. Stored under the OLD rendering.
WRAPPED = "<ul><li><p>First requirement</p></li><li><p>Second requirement</p></li></ul>"
STALE_TEXT = "-\n\nFirst requirement\n\n-\n\nSecond requirement"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "career.db")
    migrate(connection)
    yield connection
    connection.close()


def seed(conn: sqlite3.Connection, *, provider: str = "ashby", jobs: int = 1) -> list[str]:
    """A job whose stored text is stale: the html is right, the text is not."""
    stale_hash = content_hash(STALE_TEXT)
    ids = []
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(
            CompanyRecord(slug=f"{provider}-co", name="Co", hq_country="US")
        )
        board_id = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company_id, provider=provider, board_identifier=f"{provider}board"
            )
        )
        conn.execute(
            "INSERT INTO job_raw (content_hash, description_text, description_html,"
            " byte_length, created_at) VALUES (?, ?, ?, ?, '2026-08-01T00:00:00Z')"
            " ON CONFLICT (content_hash) DO NOTHING",
            (stale_hash, STALE_TEXT, WRAPPED, len(STALE_TEXT.encode())),
        )
        for index in range(jobs):
            ids.append(
                JobRepo(conn).upsert_seen(
                    JobRecord(
                        company_id=company_id,
                        source_board_id=board_id,
                        provider=provider,
                        external_id=f"ext-{index}",
                        url=f"https://example.com/{index}",
                        title=f"Role {index}",
                        content_hash=stale_hash,
                    )
                )
            )
    return ids


def job_row(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone()


def text_of(conn: sqlite3.Connection, job_id: str) -> str:
    row = conn.execute(
        "SELECT r.description_text t FROM job j"
        " JOIN job_raw r ON r.content_hash = j.content_hash WHERE j.id = ?",
        (job_id,),
    ).fetchone()
    return str(row["t"])


# --- it actually fixes the text --------------------------------------------


def test_the_stale_text_is_rebuilt_from_the_archived_html(conn) -> None:
    [job_id] = seed(conn)
    assert text_of(conn, job_id) == STALE_TEXT

    stats = renormalize(conn)

    assert stats.raw_rows_rewritten == 1
    assert stats.jobs_repointed == 1
    assert text_of(conn, job_id) == html_to_text(WRAPPED)
    assert text_of(conn, job_id) == "- First requirement\n\n- Second requirement"


def test_the_new_hash_matches_the_new_text(conn) -> None:
    [job_id] = seed(conn)
    renormalize(conn)
    row = job_row(conn, job_id)

    assert row["content_hash"] == content_hash(text_of(conn, job_id))


def test_the_old_raw_row_is_preserved(conn) -> None:
    """History is append-only. The earlier text was correct for the rules in
    force when it was written, and deleting it would destroy the provenance the
    table exists for."""
    seed(conn)
    stale_hash = content_hash(STALE_TEXT)
    renormalize(conn)

    old = conn.execute("SELECT * FROM job_raw WHERE content_hash = ?", (stale_hash,)).fetchone()
    assert old is not None
    assert old["description_text"] == STALE_TEXT
    assert conn.execute("SELECT COUNT(*) n FROM job_raw").fetchone()["n"] == 2


def test_text_that_is_already_correct_is_left_alone(conn) -> None:
    with transaction(conn):
        JobRawRepo(conn).put(html_to_text(WRAPPED), WRAPPED)
    stats = renormalize(conn)

    assert stats.raw_rows_rewritten == 0
    assert stats.raw_rows_inspected == 0, "unreferenced rows are history, not work"


# --- it is not a collection pass -------------------------------------------


def test_job_identity_and_first_seen_are_untouched(conn) -> None:
    [job_id] = seed(conn)
    before = job_row(conn, job_id)

    renormalize(conn)
    after = job_row(conn, job_id)

    assert after["id"] == before["id"] == job_id
    assert after["first_seen_at"] == before["first_seen_at"]
    assert after["external_id"] == before["external_id"]
    assert after["provider"] == before["provider"]


def test_the_backfill_does_not_look_like_a_new_observation(conn) -> None:
    """The property this whole module is arranged around. A job that appears
    newly seen is a job whose freshness signal has been quietly falsified."""
    [job_id] = seed(conn)
    before = job_row(conn, job_id)

    renormalize(conn)
    after = job_row(conn, job_id)

    assert after["last_seen_at"] == before["last_seen_at"]
    assert after["closed_at"] == before["closed_at"] is None
    assert after["collection_status"] == before["collection_status"]
    assert after["content_hash"] != before["content_hash"], "only this may change"
    assert after["updated_at"] >= before["updated_at"]


def test_board_state_is_untouched(conn) -> None:
    seed(conn)
    before = conn.execute("SELECT * FROM source_board").fetchone()

    renormalize(conn)
    after = conn.execute("SELECT * FROM source_board").fetchone()

    assert after["last_collected_at"] == before["last_collected_at"]
    assert after["last_error"] == before["last_error"]
    assert after["active"] == before["active"]


def test_no_provider_payload_is_invented(conn) -> None:
    seed(conn)
    renormalize(conn)
    assert conn.execute("SELECT COUNT(*) n FROM job_provider_payload").fetchone()["n"] == 0


def test_a_closed_job_stays_closed(conn) -> None:
    [job_id] = seed(conn)
    with transaction(conn):
        conn.execute(
            "UPDATE job SET closed_at = '2026-08-02T00:00:00Z', collection_status = 'CLOSED'"
            " WHERE id = ?",
            (job_id,),
        )

    renormalize(conn)
    row = job_row(conn, job_id)

    assert row["closed_at"] == "2026-08-02T00:00:00Z"
    assert row["collection_status"] == "CLOSED"
    assert text_of(conn, job_id) == html_to_text(WRAPPED), "still re-normalised, just not reopened"


# --- idempotence and shared rows -------------------------------------------


def test_the_second_run_does_nothing(conn) -> None:
    seed(conn, jobs=3)
    first = renormalize(conn)
    rows_after_first = conn.execute("SELECT COUNT(*) n FROM job_raw").fetchone()["n"]

    second = renormalize(conn)

    assert first.jobs_repointed == 3
    assert second.raw_rows_rewritten == 0
    assert second.raw_rows_created == 0
    assert second.jobs_repointed == 0
    assert second.is_noop
    assert conn.execute("SELECT COUNT(*) n FROM job_raw").fetchone()["n"] == rows_after_first


def test_every_job_sharing_a_row_moves_together(conn) -> None:
    """`job_raw` is content-addressed, so several postings can point at one row.
    Re-normalising the row must move all of them, or the ones left behind keep
    text that no longer matches their own html."""
    ids = seed(conn, jobs=4)
    assert len(shared_rows(conn)) == 1

    stats = renormalize(conn)

    assert stats.raw_rows_rewritten == 1
    assert stats.jobs_repointed == 4
    hashes = {job_row(conn, job_id)["content_hash"] for job_id in ids}
    assert len(hashes) == 1
    assert all(text_of(conn, job_id) == html_to_text(WRAPPED) for job_id in ids)
    assert shared_rows(conn)[0]["job_count"] == 4


def test_two_rows_converging_on_one_text_is_deduplication_not_loss(conn) -> None:
    """Once rendering improves, two differently-stale texts can become the same
    text. That is one raw row and two jobs, which is exactly what content
    addressing is for."""
    seed(conn, provider="ashby", jobs=1)
    variant = WRAPPED.replace("<p>", '<p style="min-height:1.5em">')
    variant_text = "-\n\nFirst requirement\n\n-\n\nSecond requirement\n\nx"
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(
            CompanyRecord(slug="lever-co", name="Co2", hq_country="US")
        )
        board_id = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company_id, provider="lever", board_identifier="lvboard")
        )
        conn.execute(
            "INSERT INTO job_raw (content_hash, description_text, description_html,"
            " byte_length, created_at) VALUES (?, ?, ?, ?, '2026-08-01T00:00:00Z')",
            (content_hash(variant_text), variant_text, variant, len(variant_text.encode())),
        )
        JobRepo(conn).upsert_seen(
            JobRecord(
                company_id=company_id,
                source_board_id=board_id,
                provider="lever",
                external_id="lv-1",
                url="https://example.com/lv1",
                title="Same words",
                content_hash=content_hash(variant_text),
            )
        )

    stats = renormalize(conn)

    assert stats.raw_rows_rewritten == 2
    assert stats.raw_rows_created + stats.raw_rows_converged == 2
    assert stats.raw_rows_converged == 1, "the second row landed on the first's new text"
    assert stats.jobs_by_provider == {"ashby": 1, "lever": 1}


def test_a_row_without_archived_html_is_left_alone_not_guessed_at(conn) -> None:
    """No html means nothing deterministic to re-derive from. Leaving the text
    as it is beats inventing one."""
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(CompanyRecord(slug="co", name="Co", hq_country="US"))
        board_id = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company_id, provider="ashby", board_identifier="b")
        )
        conn.execute(
            "INSERT INTO job_raw (content_hash, description_text, description_html,"
            " byte_length, created_at) VALUES (?, ?, NULL, ?, '2026-08-01T00:00:00Z')",
            (content_hash("plain text"), "plain text", 10),
        )
        JobRepo(conn).upsert_seen(
            JobRecord(
                company_id=company_id,
                source_board_id=board_id,
                provider="ashby",
                external_id="x",
                url="https://example.com/x",
                title="No html",
                content_hash=content_hash("plain text"),
            )
        )

    stats = renormalize(conn)

    assert stats.raw_rows_without_html == 1
    assert stats.raw_rows_rewritten == 0
    assert stats.jobs_repointed == 0


def test_the_run_is_recorded_as_a_normalisation_change(conn) -> None:
    """The report must not describe this as employers editing their postings.
    `pipeline_run` is where that distinction becomes durable."""
    seed(conn)
    renormalize(conn)

    row = conn.execute(
        "SELECT * FROM pipeline_run ORDER BY started_at DESC, id DESC LIMIT 1"
    ).fetchone()

    assert row["stage"] == "renormalize"
    assert row["status"] == "OK"
    assert '"kind": "NORMALIZATION_CHANGE"' in row["stats_json"]
    assert '"jobs_repointed": 1' in row["stats_json"]
