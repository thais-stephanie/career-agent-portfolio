"""A board is registered because an aggregator published the employer's own
apply URL AND the employer's own ATS answered for it. Mock transport,
temporary database, invented employers.

The eight employers in the fixture index exercise every outcome:

    northwind-systems   Greenhouse, board answers, posting listed   REGISTERED
    ativa-sistemas      Lever, board answers                        REGISTERED
    lumen-ridge         Workday, all three parts, board answers     REGISTERED
    cobalt-field        Recruitee, answers but does NOT list it     VALIDATION_DEFERRED
    fernmoor-labs       Workable account form                       FEED_COVERED
    tessera-labs        an embedded gh_jid link; the derived board
                        `tessera` does not list the posting             UNSUPPORTED_FAMILY
    quill-and-vane      no applicationUrl                           NO_ORIGIN
    bright-harbor       the page answers 500                        UNREADABLE
    (keyless)           `-join-our-talent-network`, no employer     never walked
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.board_discovery import (
    DISCOVERY_METHOD,
    RUN_NAME,
    BoardDiscovery,
    LeadOutcome,
    classify_origin,
)
from career_agent.providers import remotesource
from career_agent.storage.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "remotesource"
UUID = "0fc20191-55cf-4cf9-85fd-d05496298112"

PAGES = {
    "AAAA1111-gtm-engineer-at-northwind-systems": "job-greenhouse.html",
    "AAAA2222-data-engineer-at-northwind-systems": "job-greenhouse.html",
    "BBBB1111-devops-engineer-at-ativa-sistemas": "job-lever.html",
    "CCCC1111-analyst-at-lumen-ridge": "job-workday.html",
    "DDDD1111-recruiter-at-cobalt-field": "job-recruitee.html",
    "EEEE1111-automation-engineer-at-fernmoor-labs": "job-workable.html",
    "FFFF1111-support-lead-at-tessera-labs": "job-embedded.html",
    "GGGG1111-editor-at-quill-and-vane": "job-no-origin.html",
}


def _transport(
    recruitee_lists_it: bool = False, embedded_lists_it: bool = False
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "www.remotesource.com":
            if path == "/sitemap.xml":
                return httpx.Response(200, text=(FIXTURES / "sitemap.xml").read_text())
            if path.startswith("/sitemap/"):
                shard = "shard-0.xml" if path.endswith("0.xml") else "shard-1.xml"
                return httpx.Response(200, text=(FIXTURES / shard).read_text())
            slug = path.removeprefix("/jobs/")
            if slug in PAGES:
                return httpx.Response(200, text=(FIXTURES / PAGES[slug]).read_text())
            return httpx.Response(500)
        if host == "boards-api.greenhouse.io" and "/tessera/" in path:
            # The embedded board's derived identifier. It carries the linked
            # posting only when the test says so; otherwise a stranger's board.
            listed = 1497543 if embedded_lists_it else 999
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": listed,
                            "title": "Support Lead",
                            "absolute_url": f"https://boards.greenhouse.io/tessera/jobs/{listed}",
                            "location": {"name": "Remote"},
                            "updated_at": "2026-09-01T00:00:00Z",
                            "content": "<p>Help.</p>",
                        }
                    ]
                },
            )
        if host == "boards-api.greenhouse.io" and "/northwind/" not in path:
            return httpx.Response(404, json={"error": "no such board"})
        if host == "boards-api.greenhouse.io":
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 4958779101,
                            "title": "GTM Engineer",
                            "absolute_url": "https://job-boards.eu.greenhouse.io/northwind/jobs/4958779101",
                            "location": {"name": "London, UK (Remote)"},
                            "updated_at": "2026-09-01T00:00:00Z",
                            "content": "<p>Build.</p>",
                        }
                    ]
                },
            )
        if host == "api.lever.co":
            assert "/ativasistemas" in path, path
            return httpx.Response(
                200,
                json=[
                    {
                        "id": UUID,
                        "text": "DevOps Engineer",
                        "hostedUrl": f"https://jobs.lever.co/ativasistemas/{UUID}",
                        "categories": {"location": "Remote"},
                        "createdAt": 1756684800000,
                        "descriptionPlain": "Run things.",
                    }
                ],
            )
        if host == "lumenridge.wd5.myworkdayjobs.com":
            assert request.method == "POST", request.method
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "jobPostings": [
                        {
                            "title": "Analyst",
                            "externalPath": "/job/Remote/Analyst_R-1",
                            "locationsText": "Remote",
                            "postedOn": "Posted Today",
                        }
                    ],
                },
            )
        if host == "cobaltfield.recruitee.com":
            offers = (
                [
                    {
                        "id": 99,
                        "title": "Recruiter",
                        "careers_url": "https://cobaltfield.recruitee.com/o/recruiter",
                        "description": "<p>Hire.</p>",
                    }
                ]
                if recruitee_lists_it
                else [
                    {
                        "id": 98,
                        "title": "Somebody else's opening",
                        "careers_url": "https://cobaltfield.recruitee.com/o/other",
                        "description": "<p>x</p>",
                    }
                ]
            )
            return httpx.Response(200, json={"offers": offers})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return httpx.MockTransport(handler)


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "discovery.db")
    migrate(connection)
    yield connection
    connection.close()


def _fetcher(**kw: Any) -> HttpFetcher:
    return HttpFetcher(client=httpx.Client(transport=_transport(**kw)))


def _walk(conn: Any, *, max_employers: int = 50, **kw: Any):
    fetcher = _fetcher(**kw)
    employers = remotesource.group_by_employer(remotesource.fetch_index(fetcher))
    return BoardDiscovery(conn, fetcher).run(employers, max_employers=max_employers), employers


def _leads(conn: Any) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM board_discovery_lead ORDER BY employer_key").fetchall()
    return {r["employer_key"]: dict(r) for r in rows}


# -- classification, pure ------------------------------------------------------


def test_classifying_an_origin_needs_no_network() -> None:
    assert classify_origin(None, None).outcome is LeadOutcome.NO_ORIGIN
    assert classify_origin("https://x.example/apply", "direct").outcome is LeadOutcome.NO_ORIGIN
    embedded = classify_origin("https://careers.x.example/?gh_jid=1234", "ats")
    assert embedded.outcome is LeadOutcome.VALIDATION_DEFERRED
    assert (embedded.provider, embedded.board_identifier, embedded.external_id) == (
        "greenhouse",
        None,
        "1234",
    )
    assert (
        classify_origin("https://jobs.smartrecruiters.com/Acme/743999920407048", "ats").outcome
        is LeadOutcome.UNSUPPORTED_FAMILY
    )
    feed = classify_origin("https://apply.workable.com/acme/j/9A9ECD49CB/", "ats")
    assert feed.outcome is LeadOutcome.FEED_COVERED
    assert feed.provider == "workable"
    named = classify_origin("https://job-boards.greenhouse.io/acme/jobs/1234567", "ats")
    assert named.outcome is LeadOutcome.VALIDATION_DEFERRED
    assert (named.provider, named.board_identifier, named.external_id) == (
        "greenhouse",
        "acme",
        "1234567",
    )


# -- the walk --------------------------------------------------------------------


def test_the_index_names_employers_and_drops_the_keyless() -> None:
    entries = remotesource.index_entries((FIXTURES / "shard-0.xml").read_text())
    assert len(entries) == 9  # eight with an employer, one without
    grouped = remotesource.group_by_employer(entries)
    assert grouped["northwind-systems"] == [
        "https://www.remotesource.com/jobs/AAAA1111-gtm-engineer-at-northwind-systems",
        "https://www.remotesource.com/jobs/AAAA2222-data-engineer-at-northwind-systems",
    ]
    assert "join-our-talent-network" not in " ".join(grouped)
    assert sum(1 for e in entries if e.employer_key is None) == 1


def test_one_walk_reaches_every_outcome_and_registers_three_boards(conn: Any) -> None:
    stats, employers = _walk(conn)

    assert stats.employers_in_index == 8
    assert stats.employers_walked == 8
    leads = _leads(conn)
    assert {k: v["outcome"] for k, v in leads.items()} == {
        "northwind-systems": "REGISTERED",
        "ativa-sistemas": "REGISTERED",
        "lumen-ridge": "REGISTERED",
        "cobalt-field": "VALIDATION_DEFERRED",
        "fernmoor-labs": "FEED_COVERED",
        "tessera-labs": "UNSUPPORTED_FAMILY",
        "quill-and-vane": "NO_ORIGIN",
        "bright-harbor": "UNREADABLE",
    }
    assert sorted(stats.boards_registered) == [
        "greenhouse:northwind",
        "lever:ativasistemas",
        "workday:lumenridge.wd5/External",
    ]
    # One page per employer, one ATS probe per named board, and the bounded
    # probe for the embedded one (`tessera`, `tesseralabs`, `tessera-labs`
    # from the employer's domain and name): 8 + 4 + 3.
    assert stats.requests_made == 15


def test_a_registered_board_carries_its_provenance(conn: Any) -> None:
    _walk(conn)
    board = conn.execute(
        "SELECT sb.*, c.slug, c.name, c.discovery_source, c.canonical_domain FROM source_board sb"
        " JOIN company c ON c.id = sb.company_id"
        " WHERE sb.provider = 'workday'"
    ).fetchone()
    assert board["board_identifier"] == "lumenridge.wd5/External"
    assert board["discovery_method"] == DISCOVERY_METHOD
    assert board["verified_at"]
    assert board["active"] == 1
    # The company is the index's own naming, anchored on the website it gave.
    assert (board["slug"], board["name"]) == ("lumen-ridge", "Lumen Ridge")
    assert board["discovery_source"] == "remotesource"
    assert board["canonical_domain"] == "lumenridge.example"
    lead = _leads(conn)["lumen-ridge"]
    assert lead["source_board_id"] == board["id"]
    assert lead["origin_url"].startswith("https://lumenridge.wd5.myworkdayjobs.com/")
    assert lead["external_id"] == "/job/Remote/Analyst_R-1"
    assert "linked posting among them" in lead["detail"]


def test_a_host_that_answers_for_any_slug_must_list_the_posting(conn: Any) -> None:
    """Recruitee answers for slugs nobody registered, so answering proves
    nothing: the lead is deferred, never registered. With the posting listed,
    a retry on a page not yet read registers the same board."""
    _walk(conn)
    assert _leads(conn)["cobalt-field"]["outcome"] == "VALIDATION_DEFERRED"
    registered = conn.execute(
        "SELECT COUNT(*) FROM source_board WHERE provider='recruitee'"
    ).fetchone()[0]
    assert registered == 0

    fetcher = _fetcher(recruitee_lists_it=True)
    grouped = remotesource.group_by_employer(remotesource.fetch_index(fetcher))
    urls = grouped["cobalt-field"] + [
        "https://www.remotesource.com/jobs/DDDD1111-recruiter-at-cobalt-field?second=page"
    ]
    employers = {"cobalt-field": urls}
    # Not retried by default.
    stats = BoardDiscovery(conn, fetcher).run(employers, max_employers=5)
    assert stats.employers_walked == 0
    stats = BoardDiscovery(conn, fetcher).run(employers, max_employers=5, retry=True)
    assert stats.employers_walked == 1
    assert _leads(conn)["cobalt-field"]["outcome"] == "REGISTERED"
    board = conn.execute(
        "SELECT board_identifier FROM source_board WHERE provider='recruitee'"
    ).fetchone()
    assert board[0] == "cobaltfield"


def test_the_walk_resumes_after_the_employers_already_answered(conn: Any) -> None:
    stats, _ = _walk(conn, max_employers=3)
    assert stats.employers_walked == 3
    assert stats.employers_already_walked == 0

    stats, _ = _walk(conn, max_employers=3)
    assert stats.employers_already_walked == 3
    assert stats.employers_walked == 3

    stats, _ = _walk(conn, max_employers=50)
    assert stats.employers_already_walked == 6
    assert stats.employers_walked == 2
    assert len(_leads(conn)) == 8

    stats, _ = _walk(conn, max_employers=50)
    assert stats.employers_walked == 0, "nothing left to walk; no page is read twice"
    assert stats.requests_made == 0


def test_a_retry_reads_the_employers_next_page_and_never_the_same_one(conn: Any) -> None:
    _walk(conn)
    before = _leads(conn)["quill-and-vane"]
    assert before["times_walked"] == 1

    fetcher = _fetcher()
    grouped = remotesource.group_by_employer(remotesource.fetch_index(fetcher))
    # Give the employer a second page in the index for the retry to reach.
    grouped["quill-and-vane"].append(
        "https://www.remotesource.com/jobs/AAAA1111-gtm-engineer-at-northwind-systems"
    )
    stats = BoardDiscovery(conn, fetcher).run(grouped, max_employers=50, retry=True)
    after = _leads(conn)["quill-and-vane"]
    assert after["times_walked"] == 2
    assert after["sample_url"] != before["sample_url"]
    # Only the employer with a page not yet read was retried: bright-harbor
    # is UNREADABLE and has one URL in the index, so there is nothing new to
    # ask, and the answered employers were not touched.
    assert stats.employers_walked == 1


def test_an_existing_company_row_is_reused_and_never_rewritten(conn: Any) -> None:
    from career_agent.storage.db import transaction
    from career_agent.storage.records import CompanyRecord
    from career_agent.storage.repositories import CompanyRepo

    with transaction(conn):
        CompanyRepo(conn).upsert(
            CompanyRecord(
                slug="ativa-sistemas",
                name="Ativa Sistemas Ltda",
                website="https://ativa.example",
                canonical_domain="ativa.example",
                discovery_source="curated_technology_sourcing",
            )
        )
    _walk(conn)
    row = conn.execute("SELECT * FROM company WHERE slug='ativa-sistemas'").fetchone()
    assert row["name"] == "Ativa Sistemas Ltda"
    assert row["discovery_source"] == "curated_technology_sourcing"
    board = conn.execute("SELECT company_id FROM source_board WHERE provider='lever'").fetchone()
    assert board["company_id"] == row["id"]
    assert conn.execute("SELECT COUNT(*) FROM company WHERE slug LIKE 'ativa%'").fetchone()[0] == 1


def test_a_board_already_registered_is_reported_not_rewritten(conn: Any) -> None:
    _walk(conn)
    conn.execute("DELETE FROM board_discovery_lead")
    conn.commit()
    stats, _ = _walk(conn)
    assert stats.outcomes["ALREADY_REGISTERED"] == 3
    assert stats.outcomes.get("REGISTERED", 0) == 0
    assert conn.execute("SELECT COUNT(*) FROM source_board").fetchone()[0] == 3


def test_the_run_ledger_sees_the_walk(conn: Any) -> None:
    _walk(conn, max_employers=4)
    run = conn.execute(
        "SELECT * FROM pipeline_run WHERE stage = ? ORDER BY started_at DESC", (RUN_NAME,)
    ).fetchone()
    assert run["status"] == "OK"
    stats = json.loads(run["stats_json"])
    assert stats["employers_walked"] == 4
    assert "by_family" in stats


def test_no_posting_is_stored_by_discovery(conn: Any) -> None:
    """RemoteSource rows never enter the corpus: the boards do, the postings
    arrive later through the family's own adapter."""
    _walk(conn)
    assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM job_discovery_source").fetchone()[0] == 0


def test_the_walk_is_ordered_by_the_index_and_nothing_else() -> None:
    """ADR-0019: nothing about a person may decide which boards exist. The
    module names no preference, no seniority, no country of anybody's."""
    import ast

    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "career_agent"
        / "pipeline"
        / "board_discovery.py"
    ).read_text(encoding="utf-8")
    names = {node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)
    }
    forbidden = {
        "seniority",
        "work_model",
        "contract_regime",
        "eligible_countries",
        "preferences",
        "candidate",
        "search_config",
        "load_search_config",
    }
    assert not names & forbidden, sorted(names & forbidden)


def test_an_embedded_board_is_registered_only_when_it_lists_the_linked_posting(
    conn: Any,
) -> None:
    """`?gh_jid=` on the employer's own site names the family and the posting
    and not the board. A handful of identifiers derived from the employer's
    domain and name are probed; `tessera` answering is not enough -- it must
    list posting 1497543. Then it is registered under its own, weaker
    `discovery_method`."""
    _walk(conn)
    lead = _leads(conn)["tessera-labs"]
    assert lead["outcome"] == "UNSUPPORTED_FAMILY"
    assert "none of" in lead["detail"]
    assert lead["provider"] == "greenhouse" and lead["board_identifier"] is None

    conn.execute("DELETE FROM board_discovery_lead WHERE employer_key = 'tessera-labs'")
    conn.commit()
    fetcher = _fetcher(embedded_lists_it=True)
    grouped = remotesource.group_by_employer(remotesource.fetch_index(fetcher))
    stats = BoardDiscovery(conn, fetcher).run(
        {"tessera-labs": grouped["tessera-labs"]}, max_employers=5
    )
    assert stats.boards_registered == ["greenhouse:tessera"]
    board = conn.execute(
        "SELECT discovery_method FROM source_board WHERE board_identifier = 'tessera'"
    ).fetchone()
    assert board["discovery_method"] == "aggregator_origin_confirmed_probe"
    lead = _leads(conn)["tessera-labs"]
    assert lead["outcome"] == "REGISTERED" and lead["external_id"] == "1497543"
