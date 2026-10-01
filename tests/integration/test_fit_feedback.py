"""Search Fit feedback (migration 0046): recorded, exported, and never an input.

Synthetic throughout: the committed worked example and the demo postings.
"""

from __future__ import annotations

import csv
import io
import shutil
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.export import FEEDBACK_COLUMNS
from career_agent.web.server import ApiError, Download, ServerConfig

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "evaluation" / "demo" / "demo_postings.yaml"


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    config = tmp_path / "config"
    shutil.copytree(committed_config_dir(), config, ignore=shutil.ignore_patterns("*.local.*"))
    shutil.copyfile(config / "search.worked-example.yaml", config / "search.local.yaml")
    db = tmp_path / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "feedback")
        search, _ = load_search_config(config)
        seed_demo(conn, search, source=DEMO)
    finally:
        conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=config), quiet=True)


def a_job(api: JobsApi) -> str:
    return api.handle_api("GET", "/api/jobs", {"limit": ["1"]}, {})["items"][0]["job_id"]


def answer(api: JobsApi, job: str, verdict: str, reason=None, note=None) -> dict:  # noqa: ANN001
    body = {"verdict": verdict, "reason": reason, "note": note}
    return api.handle_api("PATCH", f"/api/jobs/{job}/fit-feedback", {}, body)


def export(api: JobsApi) -> list[dict[str, str]]:
    out = api.handle_api("GET", "/api/fit-feedback/export.csv", {}, {})
    assert isinstance(out, Download)
    text = out.body.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.DictReader(io.StringIO(text[1:])))


def table(api: JobsApi, sql: str) -> list[tuple]:
    conn = connect(api.config.db_path)
    try:
        return [tuple(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def test_an_answer_is_saved_and_changing_it_updates_the_same_row(api: JobsApi) -> None:
    job = a_job(api)
    first = answer(api, job, "TOO_HIGH", "TOOLS_NOT_WORK", "  only Python matched  ")
    assert first["fit_feedback"]["verdict"] == "TOO_HIGH"
    assert first["fit_feedback"]["note"] == "only Python matched"
    second = answer(api, job, "ACCURATE")
    assert second["fit_feedback"] == {
        "verdict": "ACCURATE",
        "reason": None,
        "note": None,
        "updated_at": second["fit_feedback"]["updated_at"],
    }
    assert table(api, "SELECT COUNT(*) FROM search_fit_feedback") == [(1,)]


@pytest.mark.parametrize(
    "body",
    [
        {"verdict": "GREAT"},
        {"verdict": ""},
        {"verdict": "ACCURATE", "reason": "SENIORITY"},
        {"verdict": "TOO_LOW", "reason": "TOOLS_NOT_WORK"},
        {"verdict": "TOO_HIGH", "note": "x" * 2001},
        {"verdict": 3},
    ],
)
def test_an_answer_outside_the_vocabulary_is_refused(api: JobsApi, body: dict) -> None:
    with pytest.raises(ApiError) as caught:
        api.handle_api("PATCH", f"/api/jobs/{a_job(api)}/fit-feedback", {}, body)
    assert caught.value.status == 400
    assert table(api, "SELECT COUNT(*) FROM search_fit_feedback") == [(0,)]


def test_feedback_changes_no_score_finding_application_or_order(api: JobsApi) -> None:
    def state() -> tuple:
        return (
            table(api, "SELECT * FROM job_match ORDER BY job_id, config_version"),
            table(api, "SELECT * FROM semantic_evaluation ORDER BY id"),
            table(api, "SELECT * FROM job_application ORDER BY job_id"),
            table(api, "SELECT * FROM job_dirty ORDER BY job_id"),
            [
                j["job_id"]
                for j in api.handle_api("GET", "/api/jobs", {"limit": ["50"]}, {})["items"]
            ],
        )

    before = state()
    for job in before[-1][:3]:
        answer(api, job, "TOO_HIGH", "SECONDARY_DUTY", "secondary only")
        answer(api, job, "TOO_LOW")
    assert state() == before


def test_an_answer_survives_a_restart(api: JobsApi) -> None:
    job = a_job(api)
    answer(api, job, "NOT_ENOUGH_INFORMATION")
    again = JobsApi(api.config, quiet=True)
    assert again.handle_api("GET", f"/api/jobs/{job}", {}, {})["fit_feedback"]["verdict"] == (
        "NOT_ENOUGH_INFORMATION"
    )


def test_an_answer_stays_with_the_score_it_judged_after_a_rescore(api: JobsApi) -> None:
    job = a_job(api)
    judged = answer(api, job, "TOO_HIGH", "SENIORITY")
    # A later scoring under a newer schema, written the way a rescore writes it.
    conn = connect(api.config.db_path)
    try:
        with transaction(conn):
            conn.execute(
                "UPDATE job_match SET schema_version = schema_version + 1,"
                " match_score = match_score - 7 WHERE job_id = ?",
                (job,),
            )
    finally:
        conn.close()
    assert api.handle_api("GET", f"/api/jobs/{job}", {}, {})["fit_feedback"] is None, (
        "an answer about the old score must not be shown as an answer about the new one"
    )
    rows = export(api)
    assert len(rows) == 1
    assert float(rows[0]["Search Fit when judged"]) == judged["match_score"]
    answer(api, job, "ACCURATE")
    rows = export(api)
    assert sorted(r["Verdict"] for r in rows) == ["ACCURATE", "TOO_HIGH"]
    assert len({r["Search Fit schema"] for r in rows}) == 2


def test_the_export_holds_only_answers_and_public_posting_facts(api: JobsApi) -> None:
    job = a_job(api)
    api.handle_api("PATCH", f"/api/jobs/{job}/notes", {}, {"notes": "PRIVATE APPLICATION NOTE"})
    answer(api, job, "TOO_LOW", "WORDING_MISSED", "they call it something else")
    rows = export(api)
    assert [list(r) for r in rows][0] == [name for name, _ in FEEDBACK_COLUMNS]
    assert len(rows) == 1 and rows[0]["Job id"] == job and rows[0]["Title"]
    text = "\n".join(",".join(r.values()) for r in rows)
    assert "PRIVATE APPLICATION NOTE" not in text
    search, _ = load_search_config(api.config.config_dir)
    phrases = {
        str(p).strip() for s in search.lexicon.values() for p in s.patterns if len(str(p)) > 12
    }
    # Title, company and URL are the employer's words and may share them.
    ours = " ".join(v for k, v in rows[0].items() if k not in ("Title", "Company", "URL"))
    leaked = [p for p in phrases if p.casefold() in ours.casefold()]
    assert not leaked, f"search phrases in the export: {leaked}"


def test_an_unscored_posting_cannot_be_judged(api: JobsApi) -> None:
    with pytest.raises(ApiError) as caught:
        answer(api, "no-such-posting", "ACCURATE")
    assert caught.value.status == 400


def test_a_rescore_that_moves_only_the_number_keeps_the_judged_answer(api: JobsApi) -> None:
    """Same configuration and schema, different number: a posting edited and
    rescored in place. The old answer must neither show under the new number
    nor be rewritten by the next answer."""
    job = a_job(api)
    judged = answer(api, job, "TOO_HIGH", "TOOLS_NOT_WORK", "judged this one")["match_score"]
    conn = connect(api.config.db_path)
    try:
        with transaction(conn):
            conn.execute(
                "UPDATE job_match SET match_score = match_score - 9 WHERE job_id = ?", (job,)
            )
    finally:
        conn.close()
    assert api.handle_api("GET", f"/api/jobs/{job}", {}, {})["fit_feedback"] is None
    answer(api, job, "ACCURATE", note="the new number")
    rows = {r["Verdict"]: r for r in export(api)}
    assert float(rows["TOO_HIGH"]["Search Fit when judged"]) == judged
    assert rows["TOO_HIGH"]["Note"] == "judged this one"
    assert float(rows["ACCURATE"]["Search Fit when judged"]) == judged - 9


def test_an_answer_about_a_score_no_longer_stored_is_refused(api: JobsApi) -> None:
    job = a_job(api)
    shown = api.handle_api("GET", f"/api/jobs/{job}", {}, {})["match_score"]
    with pytest.raises(ApiError) as caught:
        api.handle_api(
            "PATCH",
            f"/api/jobs/{job}/fit-feedback",
            {},
            {"verdict": "ACCURATE", "match_score": shown + 1},
        )
    assert caught.value.status == 409
    body = {"verdict": "ACCURATE", "match_score": shown}
    api.handle_api("PATCH", f"/api/jobs/{job}/fit-feedback", {}, body)
    assert table(api, "SELECT COUNT(*) FROM search_fit_feedback") == [(1,)]


def test_forget_tracking_also_forgets_the_answers(api: JobsApi) -> None:
    from typer.testing import CliRunner

    from career_agent.cli import app

    answer(api, a_job(api), "TOO_LOW", note="mine")
    result = CliRunner().invoke(
        app,
        [
            "forget",
            "tracking",
            "--yes",
            "--db",
            str(api.config.db_path),
            "--config-dir",
            str(api.config.config_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "1 Search Fit answers" in result.output
    assert table(api, "SELECT COUNT(*) FROM search_fit_feedback") == [(0,)]
