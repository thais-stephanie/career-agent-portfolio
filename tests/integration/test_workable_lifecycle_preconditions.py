"""What the Workable update does today that a closing policy must change first.

Characterisation only (research O1, docs/research/workable-lifecycle/). No
network and no real database. The xfail is strict: once the update path
reopens a returning posting, this file fails until the marker is removed.
"""

from __future__ import annotations

import pytest
from test_workable_update import NEWER, Server, feed_xml, run

from career_agent.storage.db import connect, migrate


@pytest.fixture
def conn(tmp_path):
    db = connect(tmp_path / "scratch.db")
    migrate(db)
    yield db
    db.close()


@pytest.fixture
def folder(tmp_path):
    return tmp_path / "shared" / "workable"


def test_an_unchanged_posting_is_not_stamped_as_seen(conn, folder):
    run(conn, Server(feed_xml(3)), folder)
    conn.execute("UPDATE job SET last_seen_at = '2000-01-01T00:00:00Z'")
    conn.commit()
    stats = run(conn, Server(feed_xml(3), modified=NEWER), folder)
    assert stats.jobs_unchanged == 3
    stamps = {r[0] for r in conn.execute("SELECT last_seen_at FROM job")}
    assert stamps == {"2000-01-01T00:00:00Z"}  # presence is not recorded


@pytest.mark.xfail(strict=True, reason="O1 precondition: the update skips a returning closed row")
def test_a_closed_posting_that_comes_back_unchanged_is_reopened(conn, folder):
    run(conn, Server(feed_xml(3)), folder)
    conn.execute(
        "UPDATE job SET closed_at = '2026-11-01T00:00:00Z', collection_status = 'CLOSED'"
        " WHERE external_id = 'workable-R1'"
    )
    conn.commit()
    run(conn, Server(feed_xml(3), modified=NEWER), folder)
    row = conn.execute("SELECT closed_at FROM job WHERE external_id = 'workable-R1'").fetchone()
    assert row[0] is None
