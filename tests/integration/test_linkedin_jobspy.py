"""LinkedIn through python-jobspy: optional, experimental, off by default.

Nothing here reaches LinkedIn. JobSpy's two entry points are replaced by
fakes, and the rate-limit path is driven through JobSpy's own logger, which is
where the real library reports a 429.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

import pytest
from tests.support import committed_config_dir

from career_agent.discovery.plan import Query, QueryTerm
from career_agent.discovery.scopes import MarketScope
from career_agent.pipeline.linkedin_collect import LinkedInCollector
from career_agent.providers.linkedin_jobspy import (
    EMPTY,
    FAILED,
    OK,
    PARSE_FAILED,
    RATE_LIMITED,
    LinkedInJobSpyProvider,
    classify,
    to_stub,
)
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.sources.catalogue import Permission, resolve
from career_agent.sources.experimental import PREFIX, opted_in, set_opt_in
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.workspace_repo import CandidateStateRepo, ensure_candidate
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "linkedin"
RECORDS = json.loads((FIXTURE / "search.json").read_text(encoding="utf-8"))
SOURCE = "linkedin_br"


def _db(tmp_path: Path, mode: RuntimeMode = RuntimeMode.PERSONAL):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, mode, "linkedin test")
    return conn


@pytest.fixture
def conn(tmp_path: Path):
    connection = _db(tmp_path)
    yield connection
    connection.close()


def _api(tmp_path: Path, mode: RuntimeMode = RuntimeMode.PERSONAL) -> JobsApi:
    _db(tmp_path, mode).close()
    config = tmp_path / "config"
    shutil.copytree(committed_config_dir(), config)
    return JobsApi(ServerConfig(db_path=tmp_path / "db.sqlite", config_dir=config), quiet=True)


# =========================================================================
# the policy is represented, not erased
# =========================================================================


def test_linkedin_stays_forbidden_and_only_declares_an_override() -> None:
    row = next(
        s for s in resolve(path=committed_config_dir() / "source_catalogue.yaml") if s.id == SOURCE
    )
    assert row.permission is Permission.FORBIDDEN
    assert row.provider is None, "an ordinary adapter would make it a normal source"
    assert row.experimental_provider == "linkedin"
    assert "robots.txt" in (row.reason or "")


def test_nothing_ships_opted_in(conn) -> None:
    assert not opted_in(conn, SOURCE)


def test_an_older_warning_is_not_consent_to_the_current_one(conn) -> None:
    with transaction(conn):
        CandidateStateRepo(conn).set(
            ensure_candidate(conn),
            PREFIX + SOURCE,
            json.dumps({"opted_in": True, "warning_version": 0}),
        )
    assert not opted_in(conn, SOURCE)
    with transaction(conn):
        set_opt_in(conn, SOURCE, True)
    assert opted_in(conn, SOURCE)


# =========================================================================
# the switch
# =========================================================================


def _row(api: JobsApi) -> dict[str, Any]:
    data = api.handle_api("GET", "/api/sources", {}, {})
    return next(r for r in data["sources"] if r["id"] == SOURCE)


def test_switching_on_needs_the_warning_acknowledged_and_can_be_undone(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "career_agent.web.source_refresh.experimental_available", lambda provider: True
    )
    api = _api(tmp_path)
    row = _row(api)
    assert row["experimental"]["opted_in"] is False
    assert row["can_refresh"] is False
    assert row["permission"] == "FORBIDDEN"

    with pytest.raises(ApiError) as caught:
        api.handle_api(
            "PATCH", "/api/sources/experimental", {}, {"source_id": SOURCE, "opted_in": True}
        )
    assert caught.value.status == 400
    assert _row(api)["experimental"]["opted_in"] is False

    api.handle_api(
        "PATCH",
        "/api/sources/experimental",
        {},
        {"source_id": SOURCE, "opted_in": True, "acknowledged": True},
    )
    row = _row(api)
    assert row["experimental"]["opted_in"] is True
    assert row["can_refresh"] is True
    assert row["permission"] == "FORBIDDEN", "opting in never rewrites what LinkedIn says"

    api.handle_api(
        "PATCH", "/api/sources/experimental", {}, {"source_id": SOURCE, "opted_in": False}
    )
    assert _row(api)["can_refresh"] is False


def test_an_ordinary_source_has_no_experimental_switch(tmp_path: Path) -> None:
    api = _api(tmp_path)
    with pytest.raises(ApiError) as caught:
        api.handle_api(
            "PATCH",
            "/api/sources/experimental",
            {},
            {"source_id": "gupy", "opted_in": True, "acknowledged": True},
        )
    assert caught.value.status == 400


def test_the_demo_cannot_switch_it_on(tmp_path: Path) -> None:
    api = _api(tmp_path, RuntimeMode.DEMO)
    with pytest.raises(ApiError) as caught:
        api.handle_api(
            "PATCH",
            "/api/sources/experimental",
            {},
            {"source_id": SOURCE, "opted_in": True, "acknowledged": True},
        )
    assert caught.value.status == 409


def test_find_jobs_runs_linkedin_only_after_the_opt_in(tmp_path: Path, monkeypatch) -> None:
    # Whatever this Python has installed: availability is tested elsewhere.
    monkeypatch.setattr(
        "career_agent.web.source_refresh.experimental_available", lambda provider: True
    )
    api = _api(tmp_path)
    ran: list[str] = []

    def feed(db, stage, **_):
        return lambda state, cancel: ran.append(stage)

    monkeypatch.setattr("career_agent.web.source_refresh.feed_work", feed)
    monkeypatch.setattr(
        api, "_collect_work", lambda limit, provider=None: lambda s, c: ran.append(provider)
    )
    monkeypatch.setattr(
        "career_agent.web.source_refresh.employer_board_work",
        lambda app, families: lambda s, c: None,
    )
    monkeypatch.setattr(api.rescore, "start", lambda work, run_id: None)

    api.handle_api("POST", "/api/sources/refresh-all", {}, {})
    api.retrieval.join(10)
    assert "collect-linkedin" not in ran

    api.handle_api(
        "PATCH",
        "/api/sources/experimental",
        {},
        {"source_id": SOURCE, "opted_in": True, "acknowledged": True},
    )
    ran.clear()
    api.handle_api("POST", "/api/sources/refresh-all", {}, {})
    api.retrieval.join(10)
    assert ran.count("collect-linkedin") == 1


# =========================================================================
# the adapter
# =========================================================================


def test_jobspy_silence_is_classified_rather_than_trusted() -> None:
    assert classify([], 3) == OK
    assert classify([], 0) == EMPTY
    assert classify(["429 Response - Blocked by LinkedIn for too many requests"], 0) == RATE_LIMITED
    assert classify(["LinkedIn response status code 500 - "], 0) == FAILED
    assert classify([], 0, ValueError("boom")) == FAILED

    class LinkedInException(Exception):
        pass

    assert classify([], 0, LinkedInException("bad location")) == PARSE_FAILED


def test_a_card_becomes_a_stub_with_the_plain_posting_url() -> None:
    card = dict(RECORDS[0], job_url="https://www.linkedin.com/jobs/view/4000000001?trk=x")
    stub = to_stub(card)
    assert stub is not None
    assert stub.url == "https://www.linkedin.com/jobs/view/4000000001"
    assert stub.external_id == "li-4000000001"
    assert stub.posted_at == "2026-09-20T00:00:00+00:00"
    assert to_stub({"id": float("nan"), "title": "x"}) is None
    assert to_stub(dict(RECORDS[2]))  # no posting date is fine


# =========================================================================
# the collector
# =========================================================================


def _query(term: str, scope: str, origin: str = "anchor") -> Query:
    scopes = {
        "country:BR": MarketScope("country:BR", "Brazil", "BR"),
        "remote_worldwide": MarketScope("remote_worldwide", "Remote Worldwide", remote=True),
    }
    return Query(QueryTerm(term, origin), scopes[scope])


class Fake:
    def __init__(self, answers: list[Any], pages: dict[str, dict[str, Any]] | None = None):
        self.answers = list(answers)
        self.pages = pages or {}
        self.searches: list[dict[str, Any]] = []
        self.enriched: list[str] = []

    def scrape(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.searches.append(kwargs)
        answer = self.answers.pop(0) if self.answers else []
        if answer == "429":
            logging.getLogger("JobSpy:LinkedIn").error(
                "429 Response - Blocked by LinkedIn for too many requests"
            )
            return []
        if isinstance(answer, Exception):
            raise answer
        return answer

    def details(self, job_id: str) -> dict[str, Any]:
        self.enriched.append(job_id)
        return self.pages.get(job_id, {})


def _collector(conn, fake: Fake) -> LinkedInCollector:
    provider = LinkedInJobSpyProvider(scrape=fake.scrape, details=fake.details)
    return LinkedInCollector(conn, provider, sleep=lambda s: None, pause=lambda span: 0.0)


def test_results_are_deduplicated_enriched_and_their_queries_recorded(conn) -> None:
    page = {"description": "**Own** the revenue systems.\n\nWork with HubSpot."}
    fake = Fake(
        [[RECORDS[0], RECORDS[1]], [RECORDS[1], RECORDS[2]]],
        pages={"4000000001": page, "4000000003": page},
    )
    stats = _collector(conn, fake).collect(
        (
            _query("Revenue Operations", "country:BR"),
            _query("Revenue Operations", "remote_worldwide"),
        ),
        max_enrich=10,
    )
    assert stats.queries_planned == stats.queries_succeeded == 2
    assert stats.raw_results == 4 and stats.unique_results == 3
    assert stats.unique_by_scope == {"country:BR": 2, "remote_worldwide": 1}
    assert fake.searches[1]["is_remote"] is True and fake.searches[1]["location"] == "Worldwide"
    # Cards only: no posting page is requested through the search.
    assert all("linkedin_fetch_description" not in s for s in fake.searches)
    # The posting both queries returned is read first.
    assert fake.enriched[0] == "4000000002"
    assert stats.enrich_attempted == 3 and stats.enrich_succeeded == 2
    assert stats.jobs_new == 3 and stats.without_description == 1
    text = conn.execute(
        "SELECT r.description_text FROM job j JOIN job_raw r ON r.content_hash = j.content_hash"
        " WHERE j.external_id = 'li-4000000001'"
    ).fetchone()[0]
    assert "**" not in text and "Own the revenue systems." in text
    lanes = conn.execute(
        "SELECT count(*), count(DISTINCT job_id) FROM job_retrieval_lane WHERE source = 'linkedin'"
    ).fetchone()
    assert tuple(lanes) == (4, 3)

    again = _collector(conn, Fake([[RECORDS[0]]])).collect((_query("x y", "country:BR"),))
    assert again.jobs_seen_again == 1 and again.jobs_new == 0
    # The one posting stored without text in the first run is completed now.
    assert again.enrich_attempted == 1


def test_a_refusal_is_retried_once_then_stops_and_is_never_read_as_no_jobs(conn) -> None:
    fake = Fake(["429", "429", [RECORDS[0]]])
    stats = _collector(conn, fake).collect(
        (_query("a b", "country:BR"), _query("c d", "country:BR")), max_enrich=0
    )
    assert len(fake.searches) == 2, "one retry, and the second query is never asked"
    assert stats.queries_rate_limited == 1 and stats.stopped_reason == "rate_limited"
    run = conn.execute(
        "SELECT status, stats_json FROM pipeline_run WHERE stage = 'collect-linkedin'"
    ).fetchone()
    assert run[0] == "FAILED"
    assert json.loads(run[1])["stopped_early"] is True


def test_one_bad_query_does_not_end_the_run(conn) -> None:
    class LinkedInException(Exception):
        pass

    fake = Fake([LinkedInException("unparseable location"), [], [RECORDS[0]]])
    stats = _collector(conn, fake).collect(
        (_query("a b", "country:BR"), _query("c d", "country:BR"), _query("e f", "country:BR")),
        max_enrich=0,
    )
    assert (stats.queries_failed, stats.queries_empty, stats.queries_succeeded) == (1, 1, 1)
    assert stats.enrich_skipped_budget == 1 and stats.jobs_new == 1


def test_enrichment_stops_after_three_unreadable_pages(conn) -> None:
    many = [dict(RECORDS[0], id=f"li-40000001{i:02d}") for i in range(6)]
    fake = Fake([many])
    stats = _collector(conn, fake).collect((_query("a b", "country:BR"),), max_enrich=10)
    assert stats.enrich_attempted == 3
    assert stats.stopped_reason == "pages_unreadable"
    assert stats.jobs_new == 6, "postings without a description are still stored"


def test_an_employer_link_already_held_is_a_sighting_not_a_second_row(conn) -> None:
    from career_agent.clock import now_utc

    now = now_utc()
    conn.execute(
        "INSERT INTO company (id, slug, name, created_at, updated_at)"
        " VALUES ('c1', 'acme', 'Acme', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO source_board (id, company_id, provider, board_identifier)"
        " VALUES ('b1', 'c1', 'greenhouse', 'acme')"
    )
    conn.execute(
        "INSERT INTO job (id, company_id, source_board_id, provider, external_id, url, title,"
        " collection_status, first_seen_at, last_seen_at, created_at, updated_at)"
        " VALUES ('j1', 'c1', 'b1', 'greenhouse', 'gh-1',"
        " 'https://jobs.example-ats.test/acme/42', 'Revenue Operations Specialist',"
        " 'NORMALISED', ?, ?, ?, ?)",
        (now, now, now, now),
    )
    conn.commit()
    stats = _collector(conn, Fake([[RECORDS[1]]])).collect(
        (_query("a b", "country:BR"),), max_enrich=5
    )
    assert stats.duplicates == {"ORIGIN_URL": 1} and stats.jobs_new == 0
    assert conn.execute("SELECT count(*) FROM job").fetchone()[0] == 1
    assert (
        conn.execute("SELECT job_id FROM job_retrieval_lane WHERE source = 'linkedin'").fetchone()[
            0
        ]
        == "j1"
    )


def test_a_posting_stored_without_text_is_completed_on_a_later_run(conn) -> None:
    first = _collector(conn, Fake([[RECORDS[0]]])).collect(
        (_query("a b", "country:BR"),), max_enrich=0
    )
    assert first.jobs_new == 1 and first.without_description == 1
    page = {"description": "Run the lifecycle automation for the whole company."}
    later = _collector(conn, Fake([[RECORDS[0]]], pages={"4000000001": page})).collect(
        (_query("a b", "country:BR"),), max_enrich=5
    )
    assert later.jobs_new == 0 and later.jobs_seen_again == 1
    assert later.jobs_described_later == 1
    row = conn.execute(
        "SELECT content_hash, collection_status FROM job WHERE external_id = 'li-4000000001'"
    ).fetchone()
    assert row[0] and row[1] == "NORMALISED"
    assert conn.execute("SELECT count(*) FROM job").fetchone()[0] == 1


def test_the_home_market_remote_scope_is_not_asked_of_linkedin() -> None:
    from career_agent.pipeline.linkedin_collect import expressible

    assert not expressible(MarketScope("remote", "Remote", remote=True, home="BR"))
    assert expressible(MarketScope("remote_worldwide", "Remote Worldwide", remote=True))
    assert expressible(MarketScope("country:BR", "Brazil", "BR"))


def test_refusals_are_read_from_status_codes_not_from_loose_words() -> None:
    assert classify([], 0, None, [200, 429]) == RATE_LIMITED
    assert classify([], 0, None, [999]) == RATE_LIMITED
    assert classify([], 0, None, [403]) == RATE_LIMITED
    assert classify([], 0, None, [500]) == FAILED
    assert classify(["LinkedIn response status code 999 - "], 0) == RATE_LIMITED
    # A word inside some other text is not a refusal.
    assert classify(["posting mentions 429 blocked cells"], 2) == OK


def test_three_failed_searches_in_a_row_end_the_run(conn) -> None:
    fake = Fake([ValueError("x"), ValueError("y"), ValueError("z"), [RECORDS[0]]])
    stats = _collector(conn, fake).collect(
        tuple(_query(f"t {i} x", "country:BR") for i in range(4)), max_enrich=0
    )
    assert len(fake.searches) == 3 and stats.stopped_reason == "repeated_failures"


def test_a_recovered_refusal_is_counted_once_as_an_attempt(conn) -> None:
    fake = Fake(["429", [RECORDS[0]]])
    stats = _collector(conn, fake).collect((_query("a b", "country:BR"),), max_enrich=0)
    assert (stats.queries_attempted, stats.retries, stats.refusals_recovered) == (1, 1, 1)
    assert stats.queries_succeeded == 1 and stats.queries_rate_limited == 0


def test_a_posting_already_resolved_as_a_sighting_is_never_read_again(conn) -> None:
    test_an_employer_link_already_held_is_a_sighting_not_a_second_row(conn)
    fake = Fake([[RECORDS[1]]], pages={"4000000002": {"description": "text"}})
    stats = _collector(conn, fake).collect((_query("c d", "country:BR"),), max_enrich=5)
    assert stats.known_sightings == 1 and fake.enriched == []


def test_a_sign_in_wall_on_a_posting_page_is_a_refusal(conn) -> None:
    fake = Fake(
        [[RECORDS[0], RECORDS[2]]],
        pages={"4000000001": {"_signin": True}, "4000000003": {"description": "x y z"}},
    )
    stats = _collector(conn, fake).collect((_query("a b", "country:BR"),), max_enrich=5)
    assert stats.enrich_attempted == 1 and stats.stopped_reason == "rate_limited"
    assert stats.jobs_new == 2 and stats.without_description == 2


def test_switching_off_stops_a_run_in_progress(conn) -> None:
    asked: list[int] = []

    def stop() -> bool:
        asked.append(1)
        return len(asked) > 1

    stats = _collector(conn, Fake([[RECORDS[0]], [RECORDS[1]]])).collect(
        (_query("a b", "country:BR"), _query("c d", "country:BR")), should_stop=stop
    )
    assert stats.queries_attempted == 1 and stats.stopped_reason == "cancelled"


def test_cards_read_before_a_later_page_failed_are_kept(conn) -> None:
    class Partial(Fake):
        def scrape(self, **kwargs):
            self.searches.append(kwargs)
            return [RECORDS[0]], [200, 500]

    stats = _collector(conn, Partial([])).collect((_query("a b", "country:BR"),), max_enrich=0)
    assert stats.queries_failed == 1 and stats.unique_results == 1 and stats.jobs_new == 1


def test_a_retry_that_fails_is_not_a_recovered_refusal(conn) -> None:
    stats = _collector(conn, Fake(["429", ValueError("x")])).collect(
        (_query("a b", "country:BR"),), max_enrich=0
    )
    assert stats.retries == 1 and stats.refusals_recovered == 0 and stats.queries_failed == 1


def test_the_linkedin_row_is_unavailable_without_the_library(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "career_agent.web.source_refresh.experimental_available", lambda provider: False
    )
    api = _api(tmp_path)
    api.handle_api(
        "PATCH",
        "/api/sources/experimental",
        {},
        {"source_id": SOURCE, "opted_in": True, "acknowledged": True},
    )
    row = _row(api)
    assert row["experimental"]["available"] is False and row["can_refresh"] is False


def test_rows_without_text_are_completed_even_when_no_search_returns_them(conn) -> None:
    _collector(conn, Fake([[RECORDS[0], RECORDS[2]]])).collect(
        (_query("a b", "country:BR"),), max_enrich=0
    )
    page = {"description": "Plan the weekly menus for the kitchen team."}
    fake = Fake([], pages={"4000000001": page, "4000000003": page})
    stats = _collector(conn, fake).collect((), max_enrich=1)
    assert stats.queries_attempted == 0 and stats.jobs_described_later == 1
    assert len(fake.enriched) == 1, "the page budget still holds"
    described = conn.execute(
        "SELECT count(*) FROM job WHERE provider = 'linkedin' AND content_hash IS NOT NULL"
    ).fetchone()[0]
    assert described == 1
    # Completing a row is not seeing it again: no search returned it.
    seen = conn.execute(
        "SELECT count(*) FROM job WHERE provider = 'linkedin' AND last_seen_at != first_seen_at"
    ).fetchone()[0]
    assert seen == 0


def test_no_page_is_read_right_after_the_searches_were_refused(conn) -> None:
    _collector(conn, Fake([[RECORDS[0]]])).collect((_query("a b", "country:BR"),), max_enrich=0)
    fake = Fake(["429", "429"], pages={"4000000001": {"description": "x y z"}})
    stats = _collector(conn, fake).collect((_query("c d", "country:BR"),), max_enrich=5)
    assert stats.stopped_reason == "rate_limited" and fake.enriched == []
