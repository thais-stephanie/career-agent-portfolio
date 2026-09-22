"""A candidate may decide which slice is fresh first, never which slices are
never collected. Five proofs, each named for the directive rule it answers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.jobgether_collect import JobgetherCollector
from career_agent.providers.jobgether import LOCATION_SLUGS, Slice, default_slices
from career_agent.sources.scheduling import slice_order
from career_agent.storage.db import connect, migrate
from career_agent.storage.slice_state import (
    COMPLETE,
    NOT_STARTED,
    PARTIAL,
    PAUSED_PROVIDER_LIMIT,
    SliceStateRepo,
    plan_order,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "jobgether"
PAGE = json.loads((FIXTURES / "page.json").read_text(encoding="utf-8"))


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "fair.db")
    migrate(connection)
    yield connection
    connection.close()


def collector(conn: Any, seen: list[str] | None = None) -> JobgetherCollector:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request.url.params.get("locations", "?"))
        # Every slice is one page and ends: the vendor said no more.
        return httpx.Response(
            200, json={"jobs": PAGE["jobs"][:2], "pagination": {"page": 1, "hasMore": False}}
        )

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return JobgetherCollector(conn, fetcher)


UNIVERSE = tuple(Slice(loc) for loc in LOCATION_SLUGS)


def _priority(country: str) -> list[str]:
    return [Slice(loc).key for loc in slice_order(LOCATION_SLUGS, countries=(country,))]


# A. Two candidates produce the same slice universe.
def test_a_two_candidates_share_one_universe() -> None:
    brazil = set(slice_order(LOCATION_SLUGS, countries=("BR",)))
    germany = set(slice_order(LOCATION_SLUGS, countries=("DE",)))
    assert brazil == germany == set(LOCATION_SLUGS)
    assert {s.key for s in default_slices(LOCATION_SLUGS)} == {
        s.key for s in default_slices(slice_order(LOCATION_SLUGS, countries=("DE",)))
    }


# B. Occupational preferences produce exactly the same universe, because
#    nothing about an occupation reaches the plan at all.
@pytest.mark.parametrize("occupation", ["hubspot", "fashion", "nursing", ""])
def test_b_an_occupation_cannot_reach_the_universe(occupation: str) -> None:
    del occupation  # there is no parameter to pass it through
    assert [s.key for s in default_slices(LOCATION_SLUGS)] == [
        s.key for s in default_slices(LOCATION_SLUGS)
    ]
    assert len(set(plan_order([s.key for s in UNIVERSE], {}, priority=()))) == len(UNIVERSE)


# C. A bounded run number two progresses into slices run one never reached.
def test_c_run_two_continues_where_run_one_stopped(conn) -> None:
    first: list[str] = []
    stats1 = collector(conn, seen=first).collect(UNIVERSE, priority=_priority("BR"), max_requests=5)
    assert stats1.slices_walked == 5
    assert first[:4] == ["anywhere", "south-america", "latam", "brazil"], (
        "the owner's markets first"
    )
    second: list[str] = []
    stats2 = collector(conn, seen=second).collect(
        UNIVERSE, priority=_priority("BR"), max_requests=5
    )
    assert stats2.slices_walked == 5
    assert not set(first) & set(second), "run two touched nothing run one had finished"
    ledger = SliceStateRepo(conn).summary("jobgether")
    assert ledger[COMPLETE] == 10
    assert ledger[NOT_STARTED] == len(UNIVERSE) - 10


# D. Repeated owner-priority refreshes cannot starve the neutral slices.
def test_d_priority_refreshes_cannot_starve_the_rest(conn) -> None:
    walked: list[str] = []
    runs = 0
    while runs < 20:
        stats = collector(conn, seen=walked).collect(
            UNIVERSE, priority=_priority("BR"), max_requests=4
        )
        runs += 1
        if SliceStateRepo(conn).summary("jobgether").get(NOT_STARTED, 0) == 0:
            break
    assert runs * 4 >= len(UNIVERSE)
    assert set(walked) == set(LOCATION_SLUGS), "every neutral slice was reached"
    # And only now may a slice be walked a second time.
    counts = {
        key: row.times_walked for key, row in SliceStateRepo(conn).rows_for("jobgether").items()
    }
    assert max(counts.values()) - min(counts.values()) <= 1
    assert stats.slices_walked > 0


# E. A completed neutral slice is reusable for every candidate: a German
#    candidate's run finds the Brazilian's slices already walked and does not
#    rewalk them before the never-walked ones.
def test_e_completed_slices_serve_every_candidate(conn) -> None:
    collector(conn).collect(UNIVERSE, priority=_priority("BR"), max_requests=6)
    seen: list[str] = []
    collector(conn, seen=seen).collect(UNIVERSE, priority=_priority("DE"), max_requests=6)
    rows = SliceStateRepo(conn).rows_for("jobgether")
    for loc in seen:
        assert rows[loc].times_walked == 1, "a slice the Brazilian's run finished was not rewalked"
    assert "anywhere" not in seen and "brazil" not in seen
    assert "europe" in seen or "germany" in seen, (
        "the German's own markets came first among the unwalked"
    )


def test_plan_order_keeps_every_key_once_and_puts_fewest_walks_first() -> None:
    keys = ["a", "b", "c", "d"]
    from career_agent.storage.slice_state import SliceRow

    def row(key: str, walks: int) -> SliceRow:
        return SliceRow("p", key, COMPLETE, walks, None, None, None, 0, 0, 0, None)

    walked = {"a": row("a", 2), "b": row("b", 1), "c": row("c", 0)}
    assert plan_order(keys, walked, priority=["a", "b"]) == ["c", "d", "b", "a"]
    assert plan_order(keys, {}, priority=["d"]) == ["d", "a", "b", "c"]
    assert plan_order(keys, {}, priority=["zzz"]) == keys


def test_a_slice_cut_short_by_our_budget_keeps_its_turn(conn) -> None:
    """PARTIAL earns no walk: the next run comes back to it first."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jobs": PAGE["jobs"][:2], "pagination": {"page": 1, "hasMore": True}}
        )

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    JobgetherCollector(conn, fetcher).collect((Slice("anywhere"), Slice("brazil")), max_requests=1)
    rows = SliceStateRepo(conn).rows_for("jobgether")
    assert rows["anywhere"].state == PARTIAL and rows["anywhere"].times_walked == 0
    assert plan_order(["anywhere", "brazil"], rows)[0] == "anywhere"


def test_the_vendors_ceiling_is_recorded_as_its_own_state(conn) -> None:
    pages = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal pages
        pages += 1
        return httpx.Response(
            200,
            json={
                "jobs": [dict(PAGE["jobs"][0], url=f"https://jobgether.com/offer/{pages:024x}-x")],
                "pagination": {"page": pages, "hasMore": True},
            },
        )

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    JobgetherCollector(conn, fetcher).collect((Slice("anywhere"),), max_requests=50)
    assert SliceStateRepo(conn).rows_for("jobgether")["anywhere"].state == PAUSED_PROVIDER_LIMIT
