"""An identifier from one namespace may never be spent in another.

WHAT HAPPENED
-------------
The owner pressed `Retrieve jobs` and got:

    Failed: unknown WWR feed '6sense'.
    Known feeds: all, all-other, business, customer-support,
    design, devops, product, programming, sales-marketing

`6sense` is an employer. The We Work Remotely adapter was entirely right to
refuse it -- WWR is addressed by CATEGORY, and `board_providers()` has said in
prose since WWR was added that a company slug is "a question in the wrong
vocabulary".

Two things were wrong, and they are separate.

**One table, two meanings.** `source_board` names a board to FETCH for an ATS
family and records which employer a posting CAME FROM for an aggregator. The
generic collector walks that one table and hands `board_identifier` to the
adapter either way. Measured on the real corpus 2026-09-08: 442 of 676
`source_board` rows belong to aggregators, and 72 of them carry
`provider='wwr'` with a company slug -- `6sense`, `airbnb`, `databricks`,
`discord` -- in the column WWR reads as a feed name.

**And one bad row ended everything.** `_collect_board` caught `FetchError` and
nothing else, so a `ValueError` from the adapter propagated out of
`collect_all`. One row of 442 stopped the run, and the other 441 sources were
never asked. The screen then showed corpus totals beside a run that had not
happened, which is how `Scored 0` appeared beside a header counting 19,469
stored scores.

THE CONTRACT
------------
The whole plan is validated before the first socket opens. A structurally
impossible source is rejected, named, and skipped; every other source is
collected. Nothing closes on either path.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import FetchError, FetchErrorCategory, HttpFetcher
from career_agent.pipeline import collect as collect_module
from career_agent.pipeline.collect import Collector
from career_agent.providers.base import BoardRef, PostingStub, ProviderFieldMap, RawPosting
from career_agent.providers.registry import get_provider
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo

#: The nine feeds We Work Remotely actually publishes, as the owner's error
#: message listed them. Written out rather than imported, so a change to the
#: adapter's vocabulary has to be a deliberate edit here too.
WWR_FEEDS = (
    "all",
    "all-other",
    "business",
    "customer-support",
    "design",
    "devops",
    "product",
    "programming",
    "sales-marketing",
)


# =========================================================================
# 1. THE ADAPTER KNOWS ITS OWN VOCABULARY, WITHOUT ASKING ANYBODY
# =========================================================================


@pytest.fixture(scope="module")
def offline_fetcher() -> HttpFetcher:
    """A fetcher that cannot fetch. Validation must not need one."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"validation opened a socket: {request.url}")

    return HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(refuse)))


@pytest.mark.parametrize("feed", WWR_FEEDS)
def test_every_documented_feed_is_accepted(offline_fetcher, feed: str) -> None:
    provider = get_provider("wwr", offline_fetcher)
    board = BoardRef(company_slug="x", provider="wwr", board_identifier=feed)

    assert provider.validate_board(board) is None


@pytest.mark.parametrize("employer", ["6sense", "airbnb", "databricks", "discord"])
def test_an_employer_slug_is_refused_as_a_feed(offline_fetcher, employer: str) -> None:
    """The exact failure the owner met, decided before any request."""
    provider = get_provider("wwr", offline_fetcher)
    board = BoardRef(company_slug=employer, provider="wwr", board_identifier=employer)

    reason = provider.validate_board(board)

    assert reason is not None
    assert employer in reason
    assert "addressed by CATEGORY" in reason
    # And it says what WOULD have worked, because the person reading it has to
    # be able to fix the configuration.
    for feed in WWR_FEEDS:
        assert feed in reason


def test_the_same_slug_is_fine_for_a_provider_addressed_by_employer(offline_fetcher) -> None:
    """`6sense` is not a bad string. It is a string in the wrong namespace.

    An ATS adapter takes an employer and cannot know whether one exists until
    it asks, so it must NOT reject here -- rejecting an unknown employer
    without asking would be inventing a fact about the world.
    """
    for family in ("greenhouse", "lever", "ashby"):
        provider = get_provider(family, offline_fetcher)
        board = BoardRef(company_slug="6sense", provider=family, board_identifier="6sense")

        assert provider.validate_board(board) is None


# =========================================================================
# 2. THE PLAN IS CHECKED BEFORE THE RUN, AND ONE BAD SOURCE IS ISOLATED
# =========================================================================


@dataclass
class ScriptedProvider:
    """An adapter whose answer per board the test decides."""

    name: str
    answers: dict[str, Any]

    field_map = ProviderFieldMap(mappings=())

    def validate_board(self, board: BoardRef) -> str | None:
        answer = self.answers.get(board.board_identifier)
        return answer if isinstance(answer, str) and answer.startswith("INVALID:") else None

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        answer = self.answers.get(board.board_identifier)
        if isinstance(answer, Exception):
            raise answer
        for external_id in answer or ():
            yield PostingStub(
                external_id=external_id,
                title=f"Role {external_id}",
                url=f"https://example.test/{external_id}",
                location_raw="Remote",
                payload={"id": external_id},
            )

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        return RawPosting(
            stub=stub,
            description_html="<p>Own the integration surface.</p>",
            description_text="Own the integration surface.",
            payload=stub.payload,
        )


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "career.db")
    migrate(connection)
    yield connection
    connection.close()


def register(conn: sqlite3.Connection, boards: list[tuple[str, str, str]]) -> None:
    with transaction(conn):
        companies, repo = CompanyRepo(conn), SourceBoardRepo(conn)
        for slug, provider, identifier in boards:
            company_id = companies.upsert(
                CompanyRecord(slug=slug, name=slug.title(), hq_country="US")
            )
            repo.upsert(
                SourceBoardRecord(
                    company_id=company_id, provider=provider, board_identifier=identifier
                )
            )


def run(conn, monkeypatch, answers: dict[str, Any]):
    provider = ScriptedProvider(name="greenhouse", answers=answers)
    monkeypatch.setattr(collect_module, "get_provider", lambda *_a, **_k: provider)
    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        request_delay_seconds=0.0,
        backoff_seconds=0.0,
        sleep=lambda _s: None,
    )
    return Collector(conn, fetcher).collect_all(use_cache=False)


def test_an_invalid_source_is_rejected_and_the_valid_ones_still_collect(conn, monkeypatch) -> None:
    """**The whole point.** 441 sources must not be lost to one bad row."""
    register(
        conn,
        [
            ("alpha", "greenhouse", "alpha"),
            ("sixsense", "greenhouse", "wrong-namespace"),
            ("beta", "greenhouse", "beta"),
        ],
    )

    stats = run(
        conn,
        monkeypatch,
        {
            "alpha": ["1", "2"],
            "wrong-namespace": "INVALID: that identifier belongs to another vocabulary",
            "beta": ["3"],
        },
    )

    assert stats.boards_rejected == 1
    assert stats.boards_attempted == 2
    assert stats.boards_succeeded == 2
    assert stats.postings_observed == 3
    rejected = [f for f in stats.failures if f.category == "NOT_ADDRESSABLE"]
    assert len(rejected) == 1
    assert "another vocabulary" in rejected[0].message


def test_an_adapter_that_raises_does_not_end_the_run(conn, monkeypatch) -> None:
    """`FetchError` was caught and everything else was not.

    A `ValueError` out of `list_postings` used to propagate through
    `collect_all` and stop the collection where it stood.
    """
    register(
        conn,
        [
            ("alpha", "greenhouse", "alpha"),
            ("broken", "greenhouse", "broken"),
            ("beta", "greenhouse", "beta"),
        ],
    )

    stats = run(
        conn,
        monkeypatch,
        {
            "alpha": ["1"],
            "broken": ValueError("unknown WWR feed '6sense'"),
            "beta": ["2"],
        },
    )

    assert stats.boards_failed == 1
    assert stats.boards_succeeded == 2
    assert stats.postings_observed == 2
    adapter_errors = [f for f in stats.failures if f.category == "ADAPTER_ERROR"]
    assert len(adapter_errors) == 1
    assert "unknown WWR feed" in adapter_errors[0].message


def test_a_network_failure_and_a_broken_adapter_are_different_sentences(conn, monkeypatch) -> None:
    """One is about the world; the other is about us. Source health says which."""
    register(conn, [("a", "greenhouse", "a"), ("b", "greenhouse", "b")])

    stats = run(
        conn,
        monkeypatch,
        {
            "a": FetchError(FetchErrorCategory.TIMEOUT, "https://example.test", "timed out"),
            "b": ValueError("a shape nobody predicted"),
        },
    )

    categories = sorted(f.category for f in stats.failures)
    assert categories == ["ADAPTER_ERROR", "TIMEOUT"]


def test_neither_rejection_nor_an_adapter_error_closes_anything(conn, monkeypatch) -> None:
    """An exception is not a statement about what still exists."""
    register(conn, [("alpha", "greenhouse", "alpha")])
    run(conn, monkeypatch, {"alpha": ["1", "2"]})
    assert conn.execute("SELECT COUNT(*) FROM job WHERE closed_at IS NULL").fetchone()[0] == 2

    run(conn, monkeypatch, {"alpha": ValueError("broken today")})

    assert conn.execute("SELECT COUNT(*) FROM job WHERE closed_at IS NULL").fetchone()[0] == 2


def test_a_rejected_source_is_never_asked(conn, monkeypatch) -> None:
    """Validation happens before the run, so no request is spent on it."""
    register(conn, [("bad", "greenhouse", "bad")])
    asked: list[str] = []

    provider = ScriptedProvider(name="greenhouse", answers={"bad": "INVALID: wrong namespace"})
    original = provider.list_postings

    def watched(board):
        asked.append(board.board_identifier)
        return original(board)

    provider.list_postings = watched  # type: ignore[method-assign]
    monkeypatch.setattr(collect_module, "get_provider", lambda *_a, **_k: provider)
    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        request_delay_seconds=0.0,
        backoff_seconds=0.0,
        sleep=lambda _s: None,
    )

    stats = Collector(conn, fetcher).collect_all(use_cache=False)

    assert asked == []
    assert stats.boards_rejected == 1
    assert stats.boards_attempted == 0
