"""A way of working she said never to show is set aside from Discover, and
put back by one control, exactly like a level she excluded.

Seeded AFTER the answer is written, so the stored scores answer the search in
force; the narrowing itself reads only the stored `work_model` column.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from tests.support import committed_config_dir

from career_agent.config.candidate_writer import set_candidate_fields
from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO = Path(__file__).resolve().parents[2]
DEMO_FILE = REPO / "evaluation" / "demo" / "demo_postings.yaml"
WIDE = "include_ineligible=1&include_off_target=1&include_unresolved=1&group_duplicates=0"


def _serve(tmp_path: Path, excluded: list[str]) -> JobsApi:
    config_dir = tmp_path / "config"
    shutil.copytree(committed_config_dir(), config_dir)
    if excluded:
        set_candidate_fields(config_dir, {"excluded_work_models": excluded})
    db = tmp_path / "demo.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.DEMO, "demo")
        config, _ = load_search_config(config_dir)
        seed_demo(conn, config, source=DEMO_FILE)
    finally:
        conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=config_dir, port=0), quiet=True)


def _jobs(api: JobsApi, qs: str) -> dict:
    return api.handle_api("GET", "/api/jobs", parse_qs(qs, keep_blank_values=True), {})


def _models(payload: dict) -> list[str | None]:
    return [item.get("work_model") for item in payload["items"]]


@pytest.fixture
def plain(tmp_path: Path) -> Iterator[JobsApi]:
    yield _serve(tmp_path / "plain", [])


@pytest.fixture
def never_remote(tmp_path: Path) -> Iterator[JobsApi]:
    yield _serve(tmp_path / "never", ["REMOTE"])


def test_nothing_is_set_aside_until_she_says_so(plain: JobsApi) -> None:
    payload = _jobs(plain, WIDE + "&limit=200")
    assert payload["hidden_by_work_model"] == 0


def test_never_show_sets_aside_exactly_what_it_reports(
    plain: JobsApi, never_remote: JobsApi
) -> None:
    everything = _jobs(plain, WIDE + "&limit=200")
    # The demo corpus is remote work, so "never show remote" is the case with
    # something to set aside.
    remote = sum(1 for model in _models(everything) if model == "REMOTE")
    assert remote, "the demo corpus has no remote posting; this test is blind"

    narrowed = _jobs(never_remote, WIDE + "&limit=200")
    assert "REMOTE" not in _models(narrowed)
    assert narrowed["hidden_by_work_model"] == remote
    # A posting that did not state a way of working is never set aside by it.
    assert _models(narrowed).count(None) == _models(everything).count(None)

    revealed = _jobs(never_remote, WIDE + "&limit=200&include_excluded_work_model=1")
    assert revealed["total"] == narrowed["total"] + narrowed["hidden_by_work_model"]
    assert revealed["hidden_by_work_model"] == 0


def test_never_show_is_not_eligibility(plain: JobsApi, never_remote: JobsApi) -> None:
    def verdicts(api: JobsApi) -> list[tuple[str, str, str]]:
        # Two databases, so two sets of ids: compared by what the posting is.
        items = _jobs(api, WIDE + "&limit=200&include_excluded_work_model=1")["items"]
        return sorted(
            (item["title"], item["company_name"], item["eligibility_status"]) for item in items
        )

    assert verdicts(never_remote) == verdicts(plain)
