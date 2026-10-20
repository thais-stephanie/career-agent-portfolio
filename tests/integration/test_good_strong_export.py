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
        "Job description",
        # The posting's own facts, as the list shows them.
        "Salary",
        "Freshness",
        # The Deep Reading verdict on the ADVERT: level, state, relation and
        # grounds quote the employer, never the person's evidence text.
        "Deep Reading level",
        "Deep Reading level when read",
        "Deep Reading state",
        "Deep Reading work relation",
        "Deep Reading grounds",
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


def test_the_export_walks_every_page(api: JobsApi, monkeypatch) -> None:
    from career_agent.web import export

    monkeypatch.setattr(export, "PAGE_SIZE", 2)
    expected = api.handle_api(
        "GET", "/api/jobs", {**EVERYTHING, "fit_band": ["GOOD", "STRONG"], "limit": ["500"]}, {}
    )["total"]
    assert expected > 4, "the corpus must need several pages of two"
    rows = _rows(_export(api, EVERYTHING))
    assert len(rows) == expected
    assert len({r["Job id"] for r in rows}) == expected, "a page was read twice"


def test_grouping_on_exports_what_the_list_shows(api: JobsApi) -> None:
    grouped = {**EVERYTHING, "group_duplicates": ["1"]}
    expected = api.handle_api(
        "GET", "/api/jobs", {**grouped, "fit_band": ["GOOD", "STRONG"], "limit": ["500"]}, {}
    )["total"]
    assert len(_rows(_export(api, grouped))) == expected


def test_leading_tab_and_carriage_return_are_neutralised() -> None:
    body = good_strong_csv([{"title": "\t=cmd", "company_name": "\r@x"}])
    row = next(csv.DictReader(io.StringIO(body.decode("utf-8")[1:])))
    assert row["Title"].startswith("'") and row["Company"].startswith("'")


def test_the_file_is_sent_as_an_attachment(api: JobsApi) -> None:
    import dataclasses
    import http.client
    import threading

    from career_agent.web.server import build_server

    # An ephemeral port: the default (8765) belongs to the person's own running
    # app, and a test must neither depend on it being free nor close it.
    api.config = dataclasses.replace(api.config, port=0)
    httpd = build_server(api)
    port = int(httpd.server_address[1])
    api.config = dataclasses.replace(api.config, port=port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        conn.request("GET", "/api/jobs/export.csv?include_ineligible=1")
        response = conn.getresponse()
        body = response.read()
        assert response.status == 200
        assert response.getheader("Content-Type").startswith("text/csv")
        disposition = response.getheader("Content-Disposition")
        assert disposition.startswith('attachment; filename="career-agent-good-strong-')
        assert response.getheader("Cache-Control") == "no-store"
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert body.startswith(b"\xef\xbb\xbf"), "the byte-order mark reaches the file"
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- A Deep Reading shortlist exports exactly what the list shows ------------
#
# The readings are stood in for at the one place both the list and the export
# read them (`deep_api.deep_levels` and `_review_rows`), so the test is about
# WHICH postings the file holds, with every other list filter still applied.

LEVELS = ("STRONG", "PREPARE", "CHECK_FIRST", "LOW", "OTHER_WORK")


@pytest.fixture
def read(api: JobsApi, monkeypatch) -> dict[str, tuple[str, bool, str]]:
    from career_agent.web import deep_api

    listed = api.handle_api("GET", "/api/jobs", {**EVERYTHING, "limit": ["500"]}, {})["items"]
    ids = sorted(i["job_id"] for i in listed)
    levels = {job: (LEVELS[n % len(LEVELS)], False, "current") for n, job in enumerate(ids[:-2])}
    levels[ids[-2]] = ("STRONG", True, "stale")  # a level that no longer holds
    levels[ids[-1]] = ("STRONG", False, "evidence")  # evidence moved, level holds

    def rows(_app, _conn, job_ids=None, *, detail=False):
        return [
            {
                "job_id": job,
                "level_then": level,
                "stale": stale,
                "state": state,
                "assessment": {
                    "alignment": "CENTRAL",
                    "history": "PARTLY",
                    "counts": {"mandatory": 2, "shown": 1, "not_shown": 1},
                    "requirements": [{"quote": "Five years of Kotlin"}],
                    "central_gaps": [0],
                },
                "review": {"work_relation": {"value": "central"}},
            }
            for job, (level, stale, state) in levels.items()
            if job_ids is None or job in job_ids
        ]

    monkeypatch.setattr(deep_api, "deep_levels", lambda _app, _conn: dict(levels))
    monkeypatch.setattr(deep_api, "_review_rows", rows)
    return levels


@pytest.mark.parametrize(
    "chosen", [["STRONG"], ["STRONG", "PREPARE"], ["STRONG", "PREPARE", "CHECK_FIRST"]]
)
def test_a_deep_shortlist_exports_exactly_the_filtered_list(api, read, chosen) -> None:
    query = {**EVERYTHING, "deep": chosen}
    listed = api.handle_api("GET", "/api/jobs", {**query, "limit": ["500"]}, {})
    download = _export(api, query)
    rows = _rows(download)
    assert listed["total"] > 0 and len(rows) == listed["total"]
    assert {r["Job id"] for r in rows} == {i["job_id"] for i in listed["items"]}
    assert download.filename.startswith("career-agent-deep-shortlist-")
    names = {"STRONG": "Apply", "PREPARE": "Apply, preparing", "CHECK_FIRST": "Check first"}
    assert {r["Deep Reading level"] for r in rows} <= {names[c] for c in chosen}


def test_the_shortlist_keeps_postings_of_every_local_band(api, read) -> None:
    rows = _rows(_export(api, {**EVERYTHING, "deep": list(LEVELS)}))
    assert {"MODERATE", "WEAK"} & {r["Search Fit band"] for r in rows}, (
        "a Deep Reading shortlist is not narrowed to GOOD and STRONG"
    )


def test_the_shortlist_still_obeys_every_other_filter(api, read) -> None:
    for extra in ({"fit_band": ["MODERATE", "WEAK"]}, {"search": ["data"]}):
        query = {**EVERYTHING, "deep": ["STRONG", "PREPARE"], **extra}
        listed = api.handle_api("GET", "/api/jobs", {**query, "limit": ["500"]}, {})
        rows = _rows(_export(api, query))
        assert len(rows) == listed["total"], extra


def test_a_level_that_no_longer_holds_is_never_written_as_current(api, read) -> None:
    rows = {r["Job id"]: r for r in _rows(_export(api, {**EVERYTHING, "deep": ["STALE"]}))}
    stale = [job for job, (_l, is_stale, _s) in read.items() if is_stale]
    assert list(rows) == stale
    row = rows[stale[0]]
    assert row["Deep Reading level"] == "Not current"
    assert row["Deep Reading level when read"] == "Apply"
    assert row["Deep Reading state"] == "stale"


def test_deep_columns_say_state_relation_and_grounds(api, read) -> None:
    rows = _rows(_export(api, {**EVERYTHING, "deep": ["STRONG"]}))
    states = {r["Deep Reading state"] for r in rows}
    assert states == {"current", "evidence_changed"}
    one = rows[0]
    assert one["Deep Reading work relation"] == "central"
    assert "central gap: Five years of Kotlin" in one["Deep Reading grounds"]
    assert one["Deep Reading level when read"] == ""


def test_without_a_deep_filter_the_export_is_unchanged(api, read) -> None:
    rows = _rows(_export(api, EVERYTHING))
    assert rows and all(r["Search Fit band"] in ("GOOD", "STRONG") for r in rows)
