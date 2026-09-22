"""Re-deriving posting bodies from payloads already on disk.

The scenario is the real one: a source is collected while its adapter believes
it publishes no description, and the adapter is later found to have been wrong.
The corpus holds the payloads verbatim, so the fix is arithmetic on disk rather
than a second retrieval.

Everything here runs on synthetic payloads through a temporary database. The
tests that matter are the refusals -- a rebuild that emptied a body, or that
opened a socket, would each be worse than the defect it exists to fix.
"""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile
from collections.abc import Iterator

import pytest

from career_agent.pipeline.rebuild_bodies import (
    UnsupportedProvider,
    rebuild_bodies,
)
from career_agent.storage.db import connect, migrate

PROVIDER = "getonbrd"


def _payload(external_id: str, *, sections: dict[str, str]) -> dict:
    """One Get on Board resource, in the vendor's shape. Invented, not captured."""
    attributes: dict[str, object] = {
        "title": f"Role {external_id}",
        "remote": True,
        "countries": ["Chile"],
        "published_at": 1788739200,
        "company": {"data": {"type": "company", "id": "1", "attributes": {"name": "Acme"}}},
        # Company marketing. Present on every real posting and never part of
        # the composed body.
        "projects": "<p>Acme is rethinking the region, from anywhere in the world.</p>",
    }
    attributes.update(sections)
    return {
        "type": "job",
        "id": external_id,
        "attributes": attributes,
        "links": {"public_url": f"https://www.getonbrd.com/jobs/role-{external_id}"},
    }


def _seed(conn: sqlite3.Connection, resources: list[dict]) -> None:
    """Rows exactly as the collector left them before the body was mapped.

    Seeded through the production repositories rather than by hand-written
    SQL, so the fixture cannot drift from the schema the collector writes.
    """
    from career_agent.domain.enums import CollectionStatus
    from career_agent.storage.records import (
        CompanyRecord,
        JobRecord,
        ProviderPayloadRecord,
        SourceBoardRecord,
    )
    from career_agent.storage.repositories import (
        CompanyRepo,
        JobRepo,
        ProviderPayloadRepo,
        SourceBoardRepo,
    )

    company_id = CompanyRepo(conn).upsert(
        CompanyRecord(slug="acme", name="Acme", discovery_source=PROVIDER)
    )
    board_id = SourceBoardRepo(conn).upsert(
        SourceBoardRecord(
            company_id=company_id,
            provider=PROVIDER,
            board_identifier="acme",
            board_url=None,
            discovery_method="getonbrd_category",
        )
    )
    jobs, payloads = JobRepo(conn), ProviderPayloadRepo(conn)
    for resource in resources:
        job_id = jobs.upsert_seen(
            JobRecord(
                company_id=company_id,
                source_board_id=board_id,
                provider=PROVIDER,
                external_id=resource["id"],
                url=resource["links"]["public_url"],
                title=str(resource["attributes"]["title"]),
                department=None,
                location_raw="Chile",
                posted_at=None,
                # No body: exactly what the collector wrote before the
                # description was mapped.
                content_hash=None,
            ),
            status=CollectionStatus.FETCHED,
        )
        payloads.put(
            ProviderPayloadRecord(job_id=job_id, provider=PROVIDER, payload=dict(resource))
        )
    conn.commit()


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="rebuild-bodies")) / "corpus.db"
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


def _bodies(conn: sqlite3.Connection) -> dict[str, str | None]:
    rows = conn.execute(
        "SELECT j.external_id AS e, r.description_text AS t FROM job j"
        " LEFT JOIN job_raw r ON r.content_hash = j.content_hash"
    ).fetchall()
    return {str(r["e"]): r["t"] for r in rows}


# -- the thing it exists to do ---------------------------------------------


def test_a_row_stored_without_a_body_gains_the_one_its_payload_held(conn) -> None:
    _seed(
        conn,
        [
            _payload(
                "1",
                sections={
                    "functions_headline": "Funciones",
                    "functions": "<p>Own the reporting pipeline.</p>",
                    "description": "<ul><li>4+ years in operations.</li></ul>",
                },
            )
        ],
    )
    assert _bodies(conn) == {"1": None}

    stats = rebuild_bodies(conn, provider=PROVIDER)

    assert stats.jobs_given_a_body == 1
    assert stats.jobs_inspected == 1
    body = _bodies(conn)["1"]
    assert body is not None
    assert "Own the reporting pipeline" in body
    assert "4+ years in operations" in body


def test_the_rebuilt_row_is_marked_normalised_and_carries_its_hash(conn) -> None:
    """A body with no hash is a body no `job_match` row can be keyed to."""
    _seed(conn, [_payload("1", sections={"functions": "<p>Do the work.</p>"})])
    rebuild_bodies(conn, provider=PROVIDER)

    row = conn.execute("SELECT content_hash, collection_status FROM job").fetchone()
    assert row["content_hash"] is not None
    assert row["collection_status"] == "NORMALISED"


def test_the_companys_marketing_is_not_folded_into_the_rebuilt_body(conn) -> None:
    """The V1.2 defect, at the one door a backfill could reopen."""
    _seed(conn, [_payload("1", sections={"functions": "<p>Do the work.</p>"})])
    rebuild_bodies(conn, provider=PROVIDER)

    assert "anywhere in the world" not in str(_bodies(conn)["1"])


# -- the refusals ----------------------------------------------------------


def test_a_payload_that_composes_to_nothing_leaves_the_row_alone(conn) -> None:
    """Absence stays absence. A rebuild may not invent an empty description."""
    _seed(conn, [_payload("1", sections={})])

    stats = rebuild_bodies(conn, provider=PROVIDER)

    assert stats.jobs_payload_yields_nothing == 1
    assert stats.jobs_given_a_body == 0
    assert _bodies(conn) == {"1": None}
    row = conn.execute("SELECT collection_status FROM job").fetchone()
    assert row["collection_status"] == "FETCHED"


def test_it_never_replaces_a_stored_body_with_an_empty_one(conn) -> None:
    """An adapter regression must not delete an employer's words.

    The row already holds text; its payload now composes to nothing. The
    correct outcome is that the text survives.
    """
    _seed(conn, [_payload("1", sections={})])
    conn.execute(
        "INSERT INTO job_raw (content_hash, description_text, description_html, byte_length,"
        " created_at) VALUES ('keep', 'A body somebody already stored', '<p>x</p>', 30,"
        " '2026-09-07T00:00:00Z')"
    )
    conn.execute("UPDATE job SET content_hash = 'keep'")
    conn.commit()

    rebuild_bodies(conn, provider=PROVIDER)

    assert _bodies(conn)["1"] == "A body somebody already stored"


def test_a_job_with_no_archived_payload_is_counted_and_untouched(conn) -> None:
    _seed(conn, [_payload("1", sections={"functions": "<p>Do the work.</p>"})])
    conn.execute("DELETE FROM job_provider_payload")
    conn.commit()

    stats = rebuild_bodies(conn, provider=PROVIDER)

    assert stats.jobs_without_payload == 1
    assert _bodies(conn) == {"1": None}


def test_an_unregistered_provider_is_refused_rather_than_skipped(conn) -> None:
    with pytest.raises(UnsupportedProvider):
        rebuild_bodies(conn, provider="no-such-source")


# -- the properties a backfill lives or dies by -----------------------------


def test_a_dry_run_reports_the_same_work_and_writes_nothing(conn) -> None:
    _seed(conn, [_payload("1", sections={"functions": "<p>Do the work.</p>"})])

    preview = rebuild_bodies(conn, provider=PROVIDER, dry_run=True)

    assert preview.jobs_given_a_body == 1
    assert _bodies(conn) == {"1": None}
    assert conn.execute("SELECT COUNT(*) c FROM pipeline_run").fetchone()["c"] == 0

    applied = rebuild_bodies(conn, provider=PROVIDER)
    assert applied.jobs_given_a_body == preview.jobs_given_a_body


def test_the_second_run_moves_nothing(conn) -> None:
    """Idempotence, asserted rather than asserted-to-be-true."""
    _seed(
        conn,
        [
            _payload("1", sections={"functions": "<p>One.</p>"}),
            _payload("2", sections={"description": "<p>Two.</p>"}),
        ],
    )
    rebuild_bodies(conn, provider=PROVIDER)
    before = _bodies(conn)

    again = rebuild_bodies(conn, provider=PROVIDER)

    assert again.jobs_unchanged == 2
    assert again.jobs_given_a_body == 0
    assert again.jobs_body_changed == 0
    assert _bodies(conn) == before


def test_it_never_moves_a_sighting_date_or_closes_a_job(conn) -> None:
    """A rebuild is not an observation from the board.

    `renormalize.py` states the rule and this is the same rule one layer out:
    the employer did not touch the posting, so nothing that records what the
    board said may move.
    """
    _seed(conn, [_payload("1", sections={"functions": "<p>Do the work.</p>"})])
    before = dict(
        conn.execute(
            "SELECT first_seen_at, last_seen_at, closed_at, url, title FROM job"
        ).fetchone()
    )

    rebuild_bodies(conn, provider=PROVIDER)

    after = dict(
        conn.execute(
            "SELECT first_seen_at, last_seen_at, closed_at, url, title FROM job"
        ).fetchone()
    )
    assert after == before


def test_the_rebuilt_body_equals_what_a_fresh_collection_would_store(conn) -> None:
    """The property the whole file rests on.

    A rebuilt row and a re-collected row must be indistinguishable, or a
    rescore reconstructs one thing while the corpus holds another. Both sides
    go through the adapter's own `fetch_posting`, and this asserts the result
    rather than trusting that they do.
    """
    from career_agent.providers.getonbrd import to_stub

    resource = _payload(
        "1",
        sections={
            "functions_headline": "Funciones",
            "functions": "<p>Own the reporting pipeline.</p>",
            "description_headline": "Requisitos",
            "description": "<ul><li>4+ years.</li></ul>",
        },
    )
    _seed(conn, [resource])
    rebuild_bodies(conn, provider=PROVIDER)

    from career_agent.net.fetcher import HttpFetcher
    from career_agent.providers.registry import get_provider

    adapter = get_provider(PROVIDER, HttpFetcher())
    stub = to_stub(resource)
    assert stub is not None
    fresh = adapter.fetch_posting(None, stub)

    assert _bodies(conn)["1"] == fresh.description_text
