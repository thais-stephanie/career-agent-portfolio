"""Re-scoring reaches nothing. Asserted by making every exit raise.

This is the property that makes preference editing a UI action rather than a
budget decision, and a comment claiming it would be worth nothing. So the test
does not inspect intentions: it breaks `socket.socket`, `httpx.Client`,
`httpx.request` and every vendor client this repository can construct, then
runs a full corpus pass and requires it to succeed anyway.

If any code path in scoring ever grows a network call -- a geocoder, a currency
rate lookup, a "just enrich the top ten" shortcut -- this test fails loudly at
the moment it is added rather than on someone's metered connection later.
"""

from __future__ import annotations

import socket
import sqlite3
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import CollectionStatus
from career_agent.pipeline.rescore import rescore
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.mvp_repo import MatchRepo
from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, JobRawRepo, JobRepo, SourceBoardRepo

REPO_ROOT = Path(__file__).resolve().parents[2]
#: The committed configuration, copied without the owner's private
#: overrides. A test that read `search.local.yaml` would pass or fail on
#: what is in one person's gitignored file. See `tests/support.py`.
CONFIG_DIR = committed_config_dir()

DESCRIPTIONS = [
    (
        "Business Systems Engineer",
        "You will own our HubSpot CRM architecture, build workflow automation across "
        "our business systems, and maintain REST API integrations and webhooks between "
        "them. We use n8n for orchestration. We hire globally and work from anywhere.",
    ),
    (
        "Account Executive",
        "Carry a quota, close deals, and own outbound prospecting including cold calling "
        "for your territory. Build your own book of business.",
    ),
    (
        "Integration Specialist",
        "Build and maintain REST API integrations, webhooks and the data synchronization "
        "between our internal systems. Comfortable with JSON and SQL. Remote in Brazil.",
    ),
]


class NetworkReached(AssertionError):
    """Raised the instant anything tries to open a socket."""


@pytest.fixture
def severed_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every way out of this process, closed."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise NetworkReached(
            "scoring attempted a network call. Re-scoring must cost nothing: "
            "zero collection calls, zero hosted inference, zero local inference."
        )

    # The floor: nothing can build a socket, so nothing above it can connect.
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)

    # And the layers above it, so a failure names the culprit rather than
    # surfacing as an opaque socket error from inside a library.
    import httpx

    monkeypatch.setattr(httpx, "Client", refuse)
    monkeypatch.setattr(httpx, "request", refuse)
    monkeypatch.setattr(httpx, "get", refuse)
    monkeypatch.setattr(httpx, "post", refuse)


def _seed(conn: sqlite3.Connection) -> list[str]:
    companies = CompanyRepo(conn)
    boards = SourceBoardRepo(conn)
    raws = JobRawRepo(conn)
    jobs = JobRepo(conn)
    ids: list[str] = []
    with transaction(conn):
        company_id = companies.upsert(CompanyRecord(slug="acme", name="Acme"))
        board_id = boards.upsert(
            SourceBoardRecord(
                company_id=company_id,
                provider="manual_import",
                board_identifier="acme",
                board_url="manual://acme",
                active=False,
            )
        )
        for index, (title, text) in enumerate(DESCRIPTIONS):
            digest = raws.put(text)
            ids.append(
                jobs.upsert_seen(
                    JobRecord(
                        company_id=company_id,
                        source_board_id=board_id,
                        provider="manual_import",
                        external_id=f"seed-{index}",
                        url=f"manual://seed-{index}",
                        title=title,
                        content_hash=digest,
                    ),
                    status=CollectionStatus.NORMALISED,
                )
            )
    return ids


def test_a_full_rescore_makes_no_network_call(tmp_path: Path, severed_network: None) -> None:
    conn = connect(tmp_path / "offline.db")
    migrate(conn)
    _seed(conn)
    config, _ = load_search_config(CONFIG_DIR)

    stats = rescore(conn, config)

    assert stats.jobs_scored == len(DESCRIPTIONS)
    assert stats.errors == 0, stats.error_samples
    conn.close()


def test_rescoring_after_a_configuration_change_also_makes_no_call(
    tmp_path: Path, severed_network: None
) -> None:
    """The behaviour that matters: edit preferences, re-run, look at the result.

    A second pass with `force` recomputes every row -- the expensive case --
    and still reaches nothing.
    """
    conn = connect(tmp_path / "offline.db")
    migrate(conn)
    _seed(conn)
    config, _ = load_search_config(CONFIG_DIR)

    first = rescore(conn, config)
    second = rescore(conn, config, force=True)

    assert first.jobs_scored == second.jobs_scored
    assert second.jobs_skipped_already_scored == 0, "force must recompute, not skip"
    conn.close()


def test_a_second_pass_without_force_resumes_instead_of_recomputing(tmp_path: Path) -> None:
    conn = connect(tmp_path / "resume.db")
    migrate(conn)
    _seed(conn)
    config, _ = load_search_config(CONFIG_DIR)

    rescore(conn, config)
    again = rescore(conn, config)

    assert again.jobs_scored == 0
    assert again.jobs_skipped_already_scored == len(DESCRIPTIONS)
    conn.close()


def test_rescoring_is_deterministic_across_runs(tmp_path: Path) -> None:
    """Same text, same configuration, same numbers -- so a diff means a change."""
    config, _ = load_search_config(CONFIG_DIR)

    scores: list[list[int]] = []
    for name in ("a.db", "b.db"):
        conn = connect(tmp_path / name)
        migrate(conn)
        job_ids = _seed(conn)
        rescore(conn, config)
        repo = MatchRepo(conn)
        scores.append(
            [
                repo.get(job_id, config.config_id, config.config_version).match_score
                for job_id in job_ids
            ]
        )
        conn.close()
    assert scores[0] == scores[1]


def test_no_hosted_inference_ledger_row_is_written_by_scoring(tmp_path: Path) -> None:
    """The 400-call ledger is untouched by the product path.

    ADR-0010 paused that programme; this asserts the pause is real rather than
    a matter of nobody happening to call it.
    """
    conn = connect(tmp_path / "ledger.db")
    migrate(conn)
    _seed(conn)
    config, _ = load_search_config(CONFIG_DIR)

    before = conn.execute("SELECT COUNT(*) FROM llm_call").fetchone()[0]
    rescore(conn, config)
    after = conn.execute("SELECT COUNT(*) FROM llm_call").fetchone()[0]

    assert before == after == 0
    assert conn.execute("SELECT COUNT(*) FROM fingerprint").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM job_enrichment").fetchone()[0] == 0
    conn.close()


def test_manual_import_also_scores_without_a_network(tmp_path: Path, severed_network: None) -> None:
    """The path every unsupported source reaches the product through."""
    from career_agent.pipeline.manual_import import import_posting

    conn = connect(tmp_path / "import.db")
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)

    job_id = import_posting(
        conn,
        config,
        title="Workflow Automation Engineer",
        company="Pasted Co",
        description=DESCRIPTIONS[0][1],
        config_id=config.config_id,
        config_version=config.config_version,
        now="2026-09-04T00:00:00Z",
    )
    stored = MatchRepo(conn).get(job_id, config.config_id, config.config_version)
    assert stored is not None
    assert stored.match_score > 0
    conn.close()
