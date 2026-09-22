"""Rebuilding bodies reaches nothing. Asserted by making every exit raise.

This is the property that distinguishes a backfill from a collection. The whole
argument for `rebuild_bodies` is that the bytes are already on disk, so fixing
409 rows costs nothing and asks somebody else's server for nothing. A comment
claiming that would be worth exactly as much as the comment `rescore.py` used
to carry before its own offline test existed.

The module constructs an `HttpFetcher` -- the registry's signature asks for one
-- and the danger is precisely that a future edit starts USING it. A rebuild
that quietly fetched a detail page per row would still pass every other test in
the suite while turning a free operation into 409 requests.
"""

from __future__ import annotations

import socket
import sqlite3
from collections.abc import Iterator

import pytest
from tests.integration.test_rebuild_bodies import _payload, _seed

from career_agent.pipeline.rebuild_bodies import rebuild_bodies

PROVIDER = "getonbrd"


class NetworkReached(AssertionError):
    """Raised the instant anything tries to open a socket."""


@pytest.fixture
def severed_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every way out of this process, closed."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise NetworkReached(
            "rebuilding bodies attempted a network call. The input is "
            "`job_provider_payload`, which is already on disk; a request here "
            "would make a backfill into a collection wearing its name."
        )

    # The floor, and the only thing that actually proves the claim: nothing
    # can build a socket, so nothing above it can reach a host.
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)

    import httpx

    # The module-level conveniences, so a stray `httpx.get` names itself
    # rather than surfacing as an opaque socket error from inside a library.
    monkeypatch.setattr(httpx, "request", refuse)
    monkeypatch.setattr(httpx, "get", refuse)
    monkeypatch.setattr(httpx, "post", refuse)

    # `httpx.Client` is deliberately NOT patched. `rebuild_bodies` constructs
    # one on purpose, over a transport that refuses -- see `_no_transport` --
    # and a client with a mock transport opens no socket, which is what the
    # floor above proves. Patching the constructor would fail this test on the
    # very mechanism that makes the guarantee structural.


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    import pathlib
    import tempfile

    from career_agent.storage.db import connect, migrate

    db_path = pathlib.Path(tempfile.mkdtemp(prefix="rebuild-offline")) / "corpus.db"
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


def test_a_full_rebuild_completes_with_every_exit_severed(conn, severed_network) -> None:
    _seed(
        conn,
        [
            _payload(
                str(i),
                sections={
                    "functions": f"<p>Own the reporting pipeline, team {i}.</p>",
                    "description": "<ul><li>4+ years in operations.</li></ul>",
                },
            )
            for i in range(5)
        ],
    )

    stats = rebuild_bodies(conn, provider=PROVIDER)

    assert stats.jobs_given_a_body == 5
    assert stats.errors == 0


def test_the_refusing_transport_is_not_decoration(conn, severed_network) -> None:
    """The structural half, proved rather than promised.

    `rebuild_bodies` hands each adapter a fetcher that cannot fetch. If a
    future edit makes an adapter request a detail page per row, this is what
    turns that into a loud failure instead of 409 quiet requests.
    """
    from career_agent.pipeline.rebuild_bodies import (
        RebuildAttemptedARequest,
        _no_transport,
    )

    with pytest.raises(RebuildAttemptedARequest):
        _no_transport().get_json("https://www.getonbrd.com/api/v0/jobs/1")
