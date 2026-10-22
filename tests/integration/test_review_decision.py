"""Review decisions (migration 0060): recorded with their exposure, exported,
evaluated, and never an input to anything that decides.

Synthetic throughout: the committed worked example and the demo postings.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.evaluation import ranking
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.review_decision import ReviewDecisionRepo
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, Download, ServerConfig

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "evaluation" / "demo" / "demo_postings.yaml"
SCRIPT = REPO / "scripts" / "ranking_eval.py"
UTF8 = {**os.environ, "PYTHONIOENCODING": "utf-8"}


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
            stamp_identity(conn, RuntimeMode.PERSONAL, "review")
        search, _ = load_search_config(config)
        seed_demo(conn, search, source=DEMO)
    finally:
        conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=config), quiet=True)


def jobs(api: JobsApi, n: int) -> list[str]:
    items = api.handle_api("GET", "/api/jobs", {"limit": [str(n)]}, {})["items"]
    return [item["job_id"] for item in items]


def decide(
    api: JobsApi,
    job: str,
    decision,
    primary=None,
    secondary=None,
    note=None,  # noqa: ANN001
    served=None,
) -> dict:  # noqa: ANN001
    body = {
        "decision": decision,
        "primary_reason": primary,
        "secondary_reasons": secondary,
        "note": note,
        "served": served,
    }
    return api.handle_api("PATCH", f"/api/jobs/{job}/review", {}, body)


def saved(api: JobsApi) -> list[dict]:
    conn = connect(api.config.db_path)
    try:
        return ReviewDecisionRepo(conn).all()
    finally:
        conn.close()


def test_a_decision_is_saved_with_what_was_on_screen(api: JobsApi) -> None:
    job = jobs(api, 1)[0]
    shown = api.handle_api("GET", f"/api/jobs/{job}", {}, {})
    after = decide(
        api,
        job,
        "REJECT",
        "WRONG_PRIMARY_WORK",
        ["COMPENSATION"],
        "  marketing, not ops  ",
        served={"sort": "score:desc", "rank": 3, "lane": "jobs_list", "query": "fit_band=GOOD"},
    )
    assert after["review_decision"]["decision"] == "REJECT"
    assert after["review_decision"]["primary_reason"] == "WRONG_PRIMARY_WORK"
    assert after["review_decision"]["secondary_reasons"] == ["COMPENSATION"]
    assert after["review_decision"]["note"] == "marketing, not ops"
    row = saved(api)[0]
    assert row["match_score"] == shown["match_score"]
    assert row["fit_band"] == shown["fit_band"]
    assert row["eligibility_status"] == shown["eligibility_status"]
    assert row["content_hash"] and row["match_schema"] is not None
    assert row["deep_state"] == "unread"  # the demo has no deep readings; nothing invented
    assert row["deep_level"] is None and row["deep_assessment"] is None
    assert (row["served_sort"], row["served_rank"], row["review_lane"]) == (
        "score:desc",
        3,
        "jobs_list",
    )
    assert row["origin"] == "APP"


def test_deciding_again_replaces_and_keeps_the_first_time(api: JobsApi) -> None:
    job = jobs(api, 1)[0]
    decide(api, job, "MAYBE", "INSUFFICIENT_INFORMATION")
    first = saved(api)[0]["created_at"]
    after = decide(api, job, "KEEP", None, ["MANDATORY_EXPERIENCE_GAP"])
    assert after["review_decision"]["primary_reason"] is None
    assert after["review_decision"]["secondary_reasons"] == ["MANDATORY_EXPERIENCE_GAP"]
    assert saved(api)[0]["created_at"] == first
    assert decide(api, job, None)["review_decision"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "KEEP", "primary_reason": "COMPENSATION"},
        {"decision": "LOVE"},
        {"decision": "REJECT", "primary_reason": "BORING"},
        {"decision": "REJECT", "secondary_reasons": ["BORING"]},
        {"decision": "REJECT", "secondary_reasons": "COMPENSATION"},
        {"decision": "REJECT", "served": {"rank": 0}},
        {"decision": "REJECT", "served": {"rank": "3"}},
    ],
)
def test_unknown_or_contradictory_answers_are_refused(api: JobsApi, body: dict) -> None:
    job = jobs(api, 1)[0]
    with pytest.raises(ApiError) as refused:
        api.handle_api("PATCH", f"/api/jobs/{job}/review", {}, body)
    assert refused.value.status == 400


def test_the_export_words_decisions_for_a_person(api: JobsApi) -> None:
    job = jobs(api, 1)[0]
    decide(api, job, "MAYBE", "MANDATORY_TOOL_PLATFORM_GAP", ["LOCATION_OR_WORK_AUTH"])
    out = api.handle_api("GET", "/api/review-decisions/export.csv", {}, {})
    assert isinstance(out, Download)
    rows = list(csv.DictReader(io.StringIO(out.body.decode("utf-8")[1:])))
    assert rows[0]["Job id"] == job
    assert rows[0]["Decision"] == "Maybe"
    assert rows[0]["Primary reason"] == "Missing a required tool or platform"
    assert rows[0]["Other reasons"] == "Location or work authorization"
    assert rows[0]["Deep Reading state when decided"] == "not read"


def test_a_decision_never_moves_a_score_an_order_or_a_filter(api: JobsApi) -> None:
    query = {"limit": ["50"]}
    before = api.handle_api("GET", "/api/jobs", query, {})
    for item in before["items"]:
        decide(api, item["job_id"], "REJECT", "WRONG_PRIMARY_WORK")
    after = api.handle_api("GET", "/api/jobs", query, {})
    assert after["total"] == before["total"]
    assert [(i["job_id"], i["match_score"], i["fit_band"]) for i in after["items"]] == [
        (i["job_id"], i["match_score"], i["fit_band"]) for i in before["items"]
    ]


#: The only modules that may name the table: where it is defined, stored,
#: served, exported, cleared, and measured. Everything that scores (Search
#: Fit), reads (Deep Reading), ranks, retrieves, gates eligibility or filters
#: is outside this list, so a new reader anywhere fails here.
MAY_NAME_THE_TABLE = {
    "storage/review_decision.py",
    "storage/catalogue.py",
    "storage/catalogue_split.py",
    "web/api.py",
    "web/export.py",
    "evaluation/ranking.py",
    "cli_local.py",
}


def test_nothing_that_scores_reads_ranks_retrieves_gates_or_filters_reads_decisions() -> None:
    src = REPO / "src" / "career_agent"
    for path in src.rglob("*.py"):
        name = path.relative_to(src).as_posix()
        text = path.read_text(encoding="utf-8")
        if "review_decision" in text or "ReviewDecisionRepo" in text:
            assert name in MAY_NAME_THE_TABLE, f"{name} reads review decisions"
    # Inside the API, only the review routes and the posting detail touch it.
    api_text = (src / "web" / "api.py").read_text(encoding="utf-8")
    for route in ("def list_jobs", "def export_good_strong", "def _filter_from"):
        start = api_text.index(route)
        end = re.search(r"\n(    )?def ", api_text[start + 10 :])
        body = api_text[start : start + 10 + end.start()] if end else api_text[start:]
        assert "review_decision" not in body and "ReviewDecision" not in body, route
    # And the evaluation reads decisions only through the golden file it is given.
    assert "ReviewDecisionRepo" not in (src / "evaluation" / "ranking.py").read_text("utf-8")


def test_the_harness_evaluates_decisions_end_to_end(api: JobsApi) -> None:
    """Decisions in the app -> golden set -> the list's orders -> metrics and gate."""
    ids = jobs(api, 12)
    for i, job in enumerate(ids):
        decide(api, job, "KEEP" if i % 3 == 0 else ("MAYBE" if i % 3 == 1 else "REJECT"))
    conn = connect(api.config.db_path)
    try:
        facts = ranking.posting_facts(conn, ids)
        decisions = {d["job_id"]: d["decision"] for d in ReviewDecisionRepo(conn).all()}
        group_of = ranking.groups(facts)
        split = ranking.assign_frozen_splits(group_of, decisions, seed=3)
        golden = ranking.Golden(
            [ranking.GoldenItem(j, decisions[j], split[group_of[j]], group_of[j]) for j in ids],
            "2026-10-21T00:00:00+00:00",
            3,
        )
        seen = ranking.shown(conn, api.search_config(), ids)
    finally:
        conn.close()
    golden.frozen_digest = ranking.frozen_digest(golden.items)
    assert set(seen) == set(ids)
    report = ranking.evaluate(golden, seen, bootstrap=False)
    surface = report["populations"]["labeled_surface"]["score"]
    assert surface["strict_pursuit"]["positives"] == len(ids[::3])
    assert surface["review_worthy"]["positives"] == len(ids[::3]) + len(ids[1::3])
    assert ranking.leakage(golden.items) == []
    saved_golden = {"frozen_digest": golden.frozen_digest, "labelled": len(golden.frozen())}
    assert ranking.gate(report, report, golden, saved_golden) == []


def test_the_script_imports_decisions_from_a_job_export(api: JobsApi, tmp_path: Path) -> None:
    ids = jobs(api, 3)
    sheet = tmp_path / "reviewed.csv"
    sheet.write_text(
        "﻿Job id,Search Fit,Decision,Primary reason,Other reasons,Note\n"
        f"{ids[0]},77,Manter,,,\n"
        f"{ids[1]},66,Descartar,O trabalho principal não é o que eu quero,COMPENSATION,QA\n"
        f"{ids[2]},55,,,,\n",
        encoding="utf-8",
    )
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(api.config.db_path), "import", str(sheet)],
        capture_output=True,
        text=True,
        check=True,
        env=UTF8,
    )
    assert "2 decisions in 3 rows" in run.stdout
    rows = {r["job_id"]: r for r in saved(api)}
    assert rows[ids[0]]["decision"] == "KEEP" and rows[ids[0]]["origin"] == "IMPORT"
    assert rows[ids[1]]["primary_reason"] == "WRONG_PRIMARY_WORK"
    assert rows[ids[1]]["secondary_reasons"] == ["COMPENSATION"]
    assert rows[ids[1]]["match_score"] == 66


def test_panel_summary_reports_every_answer_and_estimate() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import ranking_eval  # noqa: PLC0415
    finally:
        sys.path.pop(0)
    key = {
        "sizes": {"S1": 100, "S2": 10},
        "items": [{"panel": i, "stratum": "S1" if i <= 4 else "S2"} for i in range(1, 7)],
    }
    labels = {
        "1": ("KEEP", None, [], None),
        "2": ("MAYBE", "LOCATION_OR_WORK_AUTH", [], None),
        "3": ("REJECT", "WRONG_PRIMARY_WORK", ["COMPENSATION"], None),
        "5": ("MAYBE", "INSUFFICIENT_INFORMATION", [], None),
    }
    s = ranking_eval.panel_summary(key, labels)
    assert s["S1"]["labelled"] == 3 and s["S1"]["drawn"] == 4
    assert s["S1"]["keep"]["count"] == 1 and s["S1"]["maybe"]["count"] == 1
    assert s["S1"]["keep_or_maybe"]["rate"] == pytest.approx(2 / 3)
    assert s["S1"]["estimated_hidden_keep"]["estimate"] == pytest.approx(100 / 3)
    low, high = s["S1"]["keep"]["ci95"]
    assert 0 < low < 1 / 3 < high < 1
    assert s["S1"]["primary_reasons"] == {"LOCATION_OR_WORK_AUTH": 1, "WRONG_PRIMARY_WORK": 1}
    assert s["S2"]["maybe"]["count"] == 1 and s["S2"]["keep"]["count"] == 0
    assert s["S2"]["primary_reasons"] == {"INSUFFICIENT_INFORMATION": 1}
    # Unreadable is not relevant: the MAYBE that only says "could not judge"
    # leaves the review-worthy count in the second view, and nothing else does.
    assert s["S2"]["keep_or_maybe"]["count"] == 1
    assert s["S2"]["keep_or_maybe_excluding_insufficient"]["count"] == 0
    assert s["S2"]["estimated_hidden_review_worthy_excluding_insufficient"]["estimate"] == 0
    assert s["S1"]["keep_or_maybe_excluding_insufficient"]["count"] == 2


def test_panel_decisions_never_enter_the_golden_set(api: JobsApi, tmp_path: Path) -> None:
    ids = jobs(api, 4)
    frozen = ranking.Golden(
        [ranking.GoldenItem(ids[0], "KEEP", "development", "g0")], "2026-01-01T00:00:00+00:00", 1
    )
    ranking.save(tmp_path / "ranking-golden.json", frozen)
    conn = connect(api.config.db_path)
    try:
        with transaction(conn):
            repo = ReviewDecisionRepo(conn)
            repo.save(ids[1], decision="KEEP", origin="PANEL")
            repo.save(ids[2], decision="REJECT", primary_reason="COMPENSATION", origin="PANEL")
            repo.save(ids[3], decision="MAYBE", origin="APP")
    finally:
        conn.close()
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(api.config.db_path),
            "--dir",
            str(tmp_path),
            "golden",
        ],
        capture_output=True,
        text=True,
        check=True,
        env=UTF8,
    )
    golden = ranking.load(tmp_path / "ranking-golden.json")
    assert {i.job_id for i in golden.items} == {ids[0], ids[3]}
    assert {i.job_id: i.split for i in golden.items}[ids[3]] in (ranking.LOCKBOX, "development")


GOLDEN = Path(os.environ.get("CAREER_AGENT_GOLDEN_DIR", REPO / "data" / "evaluation" / "golden"))


@pytest.mark.skipif(
    not (GOLDEN / "ranking-golden.json").exists() or not (GOLDEN / "baseline.json").exists(),
    reason="the owner's private golden set is not on this machine",
)
def test_the_owners_ranking_passes_the_regression_gate() -> None:
    """The release gate on real decisions. Private data, so it runs only where
    it lives (`data/evaluation/golden`, or CAREER_AGENT_GOLDEN_DIR) and is
    skipped everywhere else. Read-only; the lockbox stays closed."""
    root = GOLDEN.parents[2]
    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(root / "data" / "personal.db"),
            "--config-dir",
            str(root / "config"),
            "--dir",
            str(GOLDEN),
            "evaluate",
        ],
        capture_output=True,
        text=True,
        env=UTF8,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "gate passed" in run.stdout
    assert json.loads((GOLDEN / "baseline.json").read_text("utf-8"))["golden"]["labelled"] > 0
