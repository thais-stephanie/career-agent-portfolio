"""Export GOOD + STRONG: every matching row, band-restricted, safe to open.

Synthetic demo postings only.
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
from career_agent.web.export import COLUMNS, good_strong_csv
from career_agent.web.server import Download, ServerConfig

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "evaluation" / "demo" / "demo_postings.yaml"
EVERYTHING = {
    "include_ineligible": ["1"],
    "include_off_target": ["1"],
    "include_unresolved": ["1"],
}


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
            stamp_identity(conn, RuntimeMode.PERSONAL, "export")
        search, _ = load_search_config(config)
        seed_demo(conn, search, source=DEMO)
    finally:
        conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=config), quiet=True)


def _rows(download: Download) -> list[dict[str, str]]:
    text = download.body.decode("utf-8")
    assert text.startswith("﻿"), "Excel on Windows needs the byte-order mark"
    return list(csv.DictReader(io.StringIO(text[1:])))


def _export(api: JobsApi, query: dict) -> Download:
    out = api.handle_api("GET", "/api/jobs/export.csv", query, {})
    assert isinstance(out, Download)
    return out


def test_every_good_and_strong_row_across_all_pages(api: JobsApi) -> None:
    query = {**EVERYTHING, "limit": ["2"], "offset": ["0"]}
    expected = api.handle_api("GET", "/api/jobs", {**query, "fit_band": ["GOOD", "STRONG"]}, {})
    assert expected["total"] > 2, "the corpus must span more than one page"
    download = _export(api, query)
    rows = _rows(download)
    assert len(rows) == expected["total"], "the export stopped at the visible page"
    assert {r["Search Fit band"] for r in rows} <= {"GOOD", "STRONG"}
    assert download.filename.startswith("career-agent-good-strong-")
    assert download.content_type.startswith("text/csv")


def test_weaker_rows_on_screen_never_reach_the_file(api: JobsApi) -> None:
    listed = api.handle_api("GET", "/api/jobs", {**EVERYTHING, "limit": ["500"]}, {})
    assert any(i["fit_band"] in ("MODERATE", "WEAK") for i in listed["items"])
    rows = _rows(_export(api, EVERYTHING))
    assert rows and all(r["Search Fit band"] in ("GOOD", "STRONG") for r in rows)


def test_a_chosen_band_narrows_and_a_weak_only_filter_exports_nothing(api: JobsApi) -> None:
    strong = _rows(_export(api, {**EVERYTHING, "fit_band": ["STRONG"]}))
    assert strong and {r["Search Fit band"] for r in strong} == {"STRONG"}
    weak = _rows(_export(api, {**EVERYTHING, "fit_band": ["WEAK"]}))
    assert weak == []


def test_the_list_filters_and_search_still_apply(api: JobsApi) -> None:
    rows = _rows(_export(api, EVERYTHING))
    company = rows[0]["Company"]
    listed = api.handle_api(
        "GET", "/api/jobs", {**EVERYTHING, "fit_band": ["GOOD", "STRONG"], "limit": ["500"]}, {}
    )
    slug = next(i["company_slug"] for i in listed["items"] if i["company_name"] == company)
    narrowed = _rows(_export(api, {**EVERYTHING, "company": [slug]}))
    assert narrowed and {r["Company"] for r in narrowed} == {company}
    assert len(narrowed) < len(rows)


def test_only_public_facts_and_the_status_are_columns() -> None:
    names = {name for name, _ in COLUMNS}
    assert names == {
        "Search Fit band",
        "Search Fit",
        "Title",
        "Company",
        "Location",
        "Work model",
        "Source",
        "Posted",
        "Status",
        "URL",
        "Job id",
    }
    for private in ("note", "evidence", "intent", "phrase", "anchor", "query", "lane", "cv"):
        assert not any(private in n.lower() for n in names), private


def test_awkward_values_survive_a_spreadsheet() -> None:
    row = {
        "fit_band": "STRONG",
        "match_score": 91,
        "title": 'Engenheiro "Sênior", Automação\nRemoto',
        "company_name": '=HYPERLINK("http://example.invalid")',
        "location_raw": None,
        "work_model": "",
        "provider": "+greenhouse",
        "posted_at": "2026-09-20T10:00:00Z",
        "application_status": "SHORTLISTED",
        "url": "https://example.invalid/a,b",
        "job_id": "01SYNTHETIC",
    }
    body = good_strong_csv([row])
    assert b"\r\n" in body
    parsed = list(csv.DictReader(io.StringIO(body.decode("utf-8")[1:])))
    assert len(parsed) == 1
    got = parsed[0]
    assert got["Title"] == 'Engenheiro "Sênior", Automação\nRemoto'
    assert got["Company"].startswith("'="), "a formula must stay text"
    assert got["Source"] == "'+greenhouse"
    assert got["Location"] == "" and got["Work model"] == ""
    assert got["Posted"] == "2026-09-20" and got["Status"] == "Interested"
    assert got["URL"] == "https://example.invalid/a,b"


def test_the_route_is_not_a_job_id(api: JobsApi) -> None:
    # `/api/jobs/<id>` must not swallow the export.
    assert isinstance(api.handle_api("GET", "/api/jobs/export.csv", EVERYTHING, {}), Download)
