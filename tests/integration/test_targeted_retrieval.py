"""Coverage repair: the targeted lane, employer board discovery, registry sync,
truthful source status. Every request goes to `httpx.MockTransport`; nothing
here opens a socket, and every posting and employer is invented.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import SearchConfig
from career_agent.discovery.anchors import (
    Alias,
    Anchor,
    RoleAnchors,
    load_anchors,
    save_anchors,
)
from career_agent.discovery.plan import plan_queries, query_terms
from career_agent.discovery.scopes import market_scopes
from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.employer_boards import discover_employer_boards
from career_agent.pipeline.himalayas_collect import HimalayasCollector
from career_agent.sources.progress import RefreshState, partial_reason, read_progress
from career_agent.storage.db import connect, migrate
from career_agent.yaml_io import safe_load

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers"
PAGE1 = json.loads((FIXTURES / "himalayas" / "feed-page1.json").read_text(encoding="utf-8"))
BASE = safe_load((committed_config_dir() / "search.local.yaml").read_text(encoding="utf-8"))


def config(country: str, scopes: list[str], countries: list[str] | None = None) -> SearchConfig:
    data = copy.deepcopy(BASE)
    data["eligibility"]["candidate_country"] = country
    data["eligibility"]["eligible_scopes"] = scopes
    data["eligibility"]["eligible_countries"] = countries or []
    data["preferences"]["remote"]["accepted_work_models"] = ["REMOTE"]
    return SearchConfig.model_validate(data)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    db = Path(tempfile.mkdtemp(prefix="targeted")) / "corpus.db"
    connection = connect(db)
    migrate(connection)
    yield connection
    connection.close()


# =========================================================================
# market scopes: related search scopes, never synonyms, never eligibility
# =========================================================================


def test_a_brazil_search_open_to_the_region_and_the_world_asks_every_related_scope() -> None:
    keys = [s.key for s in market_scopes(config("BR", ["WORLDWIDE", "AMERICAS", "LATAM"]))]
    assert keys == [
        "country:BR",
        "region:LATAM",
        "region:SOUTH_AMERICA",
        "region:AMERICAS",
        "worldwide",
        "remote",
    ]


def test_a_country_only_search_asks_only_that_country() -> None:
    assert [s.key for s in market_scopes(config("US", []))] == ["country:US"]


def test_scopes_are_derived_for_any_market() -> None:
    keys = [s.key for s in market_scopes(config("DE", ["EMEA"], ["AT"]))]
    assert keys == ["country:DE", "country:AT", "region:EMEA"]
    assert "region:SOUTH_AMERICA" not in keys


# =========================================================================
# role anchors and the plan
# =========================================================================


def test_anchors_lead_the_plan_then_aliases_then_work_phrases(tmp_path: Path) -> None:
    cfg = config("BR", ["LATAM"])
    anchors = RoleAnchors(
        anchors=(
            Anchor(text="ICU Nurse"),
            Anchor(text="Charge Nurse", source="confirmed_suggestion"),
        ),
        aliases=(
            Alias(text="Critical Care Nurse", anchor="ICU Nurse", generator="rule"),
            Alias(text="ICU nurse", anchor="ICU Nurse", generator="rule"),  # duplicate of anchor
            Alias(text="Ward Nurse", anchor="Removed Anchor", generator="rule"),  # orphan
        ),
    )
    terms = query_terms(cfg, anchors)
    assert [(t.text, t.origin) for t in terms[:3]] == [
        ("ICU Nurse", "anchor"),
        ("Charge Nurse", "anchor"),
        ("Critical Care Nurse", "alias"),
    ]
    assert all(t.text != "Ward Nurse" for t in terms)
    assert sum(1 for t in terms if t.origin == "work") <= 4
    plan = plan_queries(terms, market_scopes(cfg), max_queries=5)
    assert len(plan) == 5 and len({q.key for q in plan}) == 5


def test_anchors_round_trip_keep_provenance_and_are_bounded(tmp_path: Path) -> None:
    saved = save_anchors(
        tmp_path,
        RoleAnchors(
            anchors=(Anchor(text="  Account   Executive "),),
            aliases=(Alias(text="Sales Executive", anchor="Account Executive", generator="rule"),),
        ),
    )
    loaded = load_anchors(tmp_path)
    assert loaded == saved and loaded.anchors[0].text == "Account Executive"
    assert loaded.aliases[0].source == "generated" and loaded.aliases[0].generator == "rule"
    with pytest.raises(Exception):  # noqa: B017 - pydantic's own error type
        RoleAnchors(anchors=tuple(Anchor(text=f"Role {i}") for i in range(9)))
    with pytest.raises(Exception):  # noqa: B017
        Anchor(text="Stylist", source="cv")
    (tmp_path / "role_anchors.local.yaml").write_text("anchors: [unclosed", encoding="utf-8")
    assert load_anchors(tmp_path) == RoleAnchors()


# =========================================================================
# Himalayas: the targeted lane
# =========================================================================


def himalayas(conn: sqlite3.Connection, search: Any) -> tuple[HimalayasCollector, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/jobs/api/search":
            return search(request)
        return httpx.Response(200, json={"jobs": PAGE1["jobs"][:1], "nextCursor": None})

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return HimalayasCollector(conn, fetcher, max_pages=1), seen


def _plan(n: int = 2) -> tuple[Any, ...]:
    cfg = config("BR", ["LATAM"])
    terms = query_terms(cfg, RoleAnchors(anchors=(Anchor(text="Widget Engineer"),)))
    return plan_queries(terms, [s for s in market_scopes(cfg) if s.country], max_queries=n)


def test_targeted_searches_add_unique_postings_and_record_their_lane(conn) -> None:
    fresh = dict(PAGE1["jobs"][2], guid="https://himalayas.app/companies/x/jobs/widget-engineer")

    def search(request: httpx.Request) -> httpx.Response:
        assert request.url.params["country"] == "BR"
        return httpx.Response(200, json={"jobs": [PAGE1["jobs"][0], fresh], "totalCount": 2})

    collector, _ = himalayas(conn, search)
    stats = collector.collect(searches=_plan(2), search_pages=1)
    assert stats.queries_planned == stats.queries_succeeded == 2
    assert stats.search_results == 4 and stats.search_unique == 1
    assert stats.unique_by_scope == {"country:BR": 1}
    assert stats.unique_by_origin == {"anchor": 1}
    lanes = conn.execute("SELECT lane, source, term_origin FROM job_retrieval_lane").fetchall()
    assert {tuple(r) for r in lanes} == {("targeted", "himalayas", "anchor")}
    assert len(lanes) == 1, "the second query's copy is the same posting"


def test_a_rate_limit_stops_the_searches_without_retrying(conn) -> None:
    calls: list[str] = []

    def search(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(429, json={"error": "slow down"})

    collector, _ = himalayas(conn, search)
    stats = collector.collect(searches=_plan(4))
    assert stats.queries_rate_limited == 1 and stats.search_stopped_reason == "rate_limited"
    assert stats.queries_succeeded == 0
    # The fetcher's own bounded retry may repeat one request; the plan never
    # moves on to the next query.
    assert len({c.split("q=")[1].split("&")[0] for c in calls}) == 1


# =========================================================================
# employer board discovery
# =========================================================================


def _employer(conn: sqlite3.Connection, slug: str, name: str, title: str, targeted: bool) -> None:
    from career_agent.clock import new_id, now_utc

    company_id = new_id()
    now = now_utc()
    conn.execute(
        "INSERT INTO company (id, slug, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (company_id, slug, name, now, now),
    )
    board_id = new_id()
    conn.execute(
        "INSERT INTO source_board (id, company_id, provider, board_identifier)"
        " VALUES (?, ?, 'himalayas', ?)",
        (board_id, company_id, slug),
    )
    job_id = new_id()
    conn.execute(
        "INSERT INTO job (id, company_id, source_board_id, provider, external_id, url, title,"
        " collection_status, first_seen_at, last_seen_at, created_at, updated_at)"
        " VALUES (?, ?, ?, 'himalayas', ?, ?, ?, 'NORMALISED', ?, ?, ?, ?)",
        (
            job_id,
            company_id,
            board_id,
            slug,
            f"https://himalayas.app/x/{slug}",
            title,
            now,
            now,
            now,
            now,
        ),
    )
    if targeted:
        conn.execute(
            "INSERT INTO job_retrieval_lane VALUES"
            " (?, 'targeted', 'himalayas', 'q', 'anchor', ?, ?)",
            (job_id, now, now),
        )
    conn.commit()


def ashby_board(titles: list[str]) -> dict[str, Any]:
    return {
        "jobs": [
            {
                "id": f"00000000-0000-4000-8000-00000000000{i}",
                "title": t,
                "jobUrl": f"https://jobs.ashbyhq.com/b/00000000-0000-4000-8000-00000000000{i}",
                "isListed": True,
                "publishedAt": "2026-09-20T10:00:00.000+00:00",
            }
            for i, t in enumerate(titles)
        ]
    }


def test_a_board_is_registered_only_when_it_lists_the_employers_posting(conn) -> None:
    _employer(conn, "acme-widgets", "Acme Widgets Inc.", "Widget Engineer", targeted=True)
    _employer(conn, "the", "The", "Head Chef", targeted=False)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "api.ashbyhq.com" and path.endswith("/acme-widgets"):
            return httpx.Response(200, json=ashby_board(["Widget Engineer", "Designer"]))
        if request.url.host == "api.ashbyhq.com" and path.endswith("/the"):
            # A board that answers for the guess and lists nothing of theirs.
            return httpx.Response(200, json=ashby_board(["Accountant"]))
        return httpx.Response(404, text="not found")

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    stats = discover_employer_boards(conn, fetcher, limit=10)
    assert stats.boards == ["ashby:acme-widgets"]
    assert stats.registered == 1 and stats.no_board == 1
    outcomes = dict(
        conn.execute("SELECT employer_key, outcome FROM board_discovery_lead").fetchall()
    )
    assert outcomes == {"acme-widgets": "REGISTERED", "the": "VALIDATION_FAILED"}
    # Answered employers are never probed again.
    again = discover_employer_boards(conn, fetcher, limit=10)
    assert again.employers_considered == 0 and again.requests == 0


def test_targeted_employers_are_probed_first(conn) -> None:
    from career_agent.pipeline.employer_boards import employers_to_probe

    _employer(conn, "zzz-plain", "Zzz Plain", "Role A", targeted=False)
    _employer(conn, "aaa-searched", "Aaa Searched", "Role B", targeted=True)
    assert [e.key for e in employers_to_probe(conn, limit=5)] == ["aaa-searched", "zzz-plain"]


# =========================================================================
# registry sync
# =========================================================================


def test_the_registry_sync_adds_boards_and_never_reactivates_one(conn, tmp_path: Path) -> None:
    from career_agent.config.registry import sync_registry_file

    (tmp_path / "companies.yaml").write_text(
        "schema_version: 1\npurpose: curated_registry\ncompanies:\n"
        "  - slug: acme\n    name: Acme\n    boards:\n"
        "      - provider: ashby\n        board_identifier: acme\n"
        "      - provider: lever\n        board_identifier: acme\n",
        encoding="utf-8",
    )
    assert sync_registry_file(conn, tmp_path) == 2
    conn.execute("UPDATE source_board SET active = 0 WHERE provider = 'lever'")
    conn.commit()
    assert sync_registry_file(conn, tmp_path) == 0
    assert (
        conn.execute("SELECT active FROM source_board WHERE provider = 'lever'").fetchone()[0] == 0
    )


# =========================================================================
# truthful status
# =========================================================================


def _run(conn, stage: str, finished: datetime, stats: dict[str, Any], status: str = "OK") -> None:
    from career_agent.clock import new_id

    conn.execute(
        "INSERT INTO pipeline_run (id, stage, started_at, finished_at, status, stats_json)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), stage, finished.isoformat(), finished.isoformat(), status, json.dumps(stats)),
    )
    conn.commit()


def test_an_old_success_is_stale_and_a_partial_run_says_why(conn) -> None:
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    _run(conn, "collect-alpha", now - timedelta(days=5), {"postings_seen": 3})
    _run(conn, "collect-beta", now - timedelta(hours=2), {"stopped_early": True})
    rows = {
        p.source_id: p
        for p in read_progress(
            conn,
            stage_for={"a": "collect-alpha", "b": "collect-beta", "c": "collect-gamma"},
            now=now,
        )
    }
    assert rows["a"].state is RefreshState.STALE
    assert rows["b"].state is RefreshState.PARTIAL and rows["b"].reason == "PAGE_LIMIT"
    assert rows["c"].state is RefreshState.NOT_STARTED
    assert partial_reason({"slices_capped": 3}) == "REQUEST_BUDGET"
    assert partial_reason({"ceiling_hit": True}) == "SOURCE_CEILING"
    assert partial_reason({}) is None


def test_the_coverage_report_names_families_with_no_board(conn) -> None:
    from career_agent.sources.coverage import coverage

    rows = {r.provider: r for r in coverage(conn, families={"ashby", "lever"})}
    assert rows["ashby"].status == "NEVER_RUN"
    assert "no employer board" in rows["ashby"].notes[0]
