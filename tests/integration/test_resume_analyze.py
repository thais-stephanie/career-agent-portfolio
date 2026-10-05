"""Analyze (PR 11): named findings about one exact resume revision, and what it
shows against what one job ad explicitly asks. Never a score.

Synthetic people only (`tests.support_tailor`): a strong senior profile, a
thin one, a messy Master and a job ad with every kind of support. No model.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_resume_drafter import _retire
from tests.integration.test_resume_tailor import AD, _large, make
from tests.support_tailor import MIXED_AD, SENIOR_AD, senior, thin

from career_agent.clock import new_id
from career_agent.resume_doc import analyze
from career_agent.resume_doc.models import upgrade_resume_document
from career_agent.resume_doc.store import ResumeStore
from career_agent.resume_doc.tailor import explain, tailor
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError


def master(api: JobsApi) -> Any:
    conn = api.connect()
    try:
        return ResumeStore(conn).current_master()
    finally:
        conn.close()


def run(api: JobsApi, doc_id: str, **job: Any) -> dict[str, Any]:
    conn = api.connect()
    sha = ResumeStore(conn).get_document(doc_id).working_sha256
    conn.close()
    return api.handle_api(
        "POST", f"/api/resume/documents/{doc_id}/analyze", {}, {"expected_sha256": sha, **job}
    )


def edit(api: JobsApi, doc_id: str, change: Any) -> None:
    conn = api.connect()
    store = ResumeStore(conn)
    stored = store.get_document(doc_id)
    data = stored.working.model_dump(mode="json")
    change(data)
    store.save_working_copy(
        doc_id, upgrade_resume_document(data), expected_sha256=stored.working_sha256
    )
    conn.close()


def kinds(result: dict[str, Any]) -> set[str]:
    return {f["kind"] for f in result["findings"]}


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    return make(tmp_path)


def _messy(data: dict[str, Any]) -> None:
    """A messy Master: duplicates, a long summary and line, no contact, a
    typed and an imported line, a heading twice, an end on a current role."""
    data["identity"].update(email=None, phone=None, links=[])
    data["summary"] = {"id": new_id(), "text": "Summary. " * 120, "origin": "USER_AUTHORED"}
    role = data["experience"][0]
    first = role["bullets"][0]
    role["bullets"] += [
        {**first, "id": new_id()},
        {
            "id": new_id(),
            "text": "Responsible for " + "many things " * 40,
            "origin": "USER_AUTHORED",
        },
        {
            "id": new_id(),
            "text": "Built dashboards in Tableau for leadership.",
            "origin": "IMPORTED",
        },
    ]
    data["custom_sections"] = [
        {
            "id": new_id(),
            "heading": "Volunteering",
            "items": [{"id": new_id(), "text": "Mentored students.", "origin": "USER_AUTHORED"}],
        },
        {
            "id": new_id(),
            "heading": "Volunteering",
            "items": [{"id": new_id(), "text": "Ran a book club.", "origin": "USER_AUTHORED"}],
        },
    ]
    data["experience"][1]["end"] = None  # two current roles: a date check, not an error
    data["experience"][1]["current"] = True


# ------------------------------------------------------------ resume only


def test_a_strong_resume_reads_clean_and_counts_facts_not_quality(api: JobsApi) -> None:
    doc = master(api).id
    edit(api, doc, lambda d: d["identity"].update(email="robin@example.invalid"))
    result = run(api, doc)
    assert result["version"] == analyze.VERSION and result["revision_id"]
    blocking = [f for f in result["findings"] if f["severity"] == "BLOCKING"]
    assert blocking == []
    assert result["overview"]["roles"] == 5 and result["overview"]["evidence"]["CONFIRMED"] > 0
    assert result["export"] is None and "EXPORT_NOT_CHECKED" in kinds(result)


def test_a_messy_resume_names_each_problem_with_an_action(api: JobsApi) -> None:
    doc = master(api).id
    edit(api, doc, _messy)
    result = run(api, doc)
    found = kinds(result)
    for kind in (
        "NO_CONTACT",
        "EMAIL_MISSING",
        "LONG_SUMMARY",
        "LONG_BULLET",
        "DUPLICATE_BULLET",
        "GENERIC_PHRASE",
        "NOT_FROM_EVIDENCE",
        "DUPLICATE_SECTION",
        "CHECK_DATES",
    ):
        assert kind in found, kind
    by = {f["kind"]: f for f in result["findings"]}
    assert by["NO_CONTACT"]["severity"] == "BLOCKING" and by["NO_CONTACT"]["nature"] == "OBJECTIVE"
    assert by["LONG_SUMMARY"]["nature"] == "ADVISORY"
    assert by["DUPLICATE_BULLET"]["action"] == "APPLY"
    assert all(
        f["severity"] in ("BLOCKING", "WARNING", "OPPORTUNITY", "INFO") for f in result["findings"]
    )
    counts = result["overview"]["evidence"]
    assert counts["USER_AUTHORED"] >= 1 and counts["IMPORTED"] == 1


def test_typed_and_imported_text_is_never_called_confirmed(api: JobsApi) -> None:
    doc = master(api).id
    edit(api, doc, _messy)
    result = run(api, doc)
    typed = [f for f in result["findings"] if f["kind"] == "NOT_FROM_EVIDENCE"]
    assert typed and all(f["severity"] == "INFO" for f in typed)
    # Nothing typed became evidence.
    conn = api.connect()
    keys = [r[0] for r in conn.execute("SELECT claim_key FROM verified_claim")]
    conn.close()
    assert not any("tableau" in k for k in keys)


def test_stale_evidence_is_named_precisely(api: JobsApi) -> None:
    doc = master(api).id
    conn = api.connect()
    _retire(conn, "k-hubspot-routing")
    conn.close()
    result = run(api, doc)
    stale = [f for f in result["findings"] if f["kind"] == "STALE_EVIDENCE"]
    assert len(stale) == 1 and stale[0]["evidence_ids"] == ["k-hubspot-routing"]
    assert result["overview"]["evidence"]["NO_LONGER_CONFIRMED"] == 1
    # The working copy could not be a milestone: it is analyzed by its hash.
    assert result["revision_id"] and result["content_sha256"]


def test_an_edited_number_on_an_evidence_line_is_flagged(api: JobsApi) -> None:
    doc = master(api).id

    def inflate(data: dict[str, Any]) -> None:
        for e in data["experience"]:
            for b in e["bullets"]:
                if "4 regional teams" in b["text"]:
                    b.update(
                        text=b["text"].replace("4 regional", "40 regional"),
                        override="EDITED",
                        original_text=b["text"],
                    )

    edit(api, doc, inflate)
    found = kinds(run(api, doc))
    assert "UNSUPPORTED_NUMBER" in found or "NUMBER_NOT_IN_EVIDENCE" in found


def test_analysis_is_of_an_exact_revision_and_never_of_a_stale_page(api: JobsApi) -> None:
    doc = master(api).id
    first = run(api, doc)
    edit(api, doc, lambda d: d["identity"].update(full_name="Robin Changed"))
    second = run(api, doc)
    assert second["revision_id"] != first["revision_id"]
    assert second["content_sha256"] != first["content_sha256"]
    with pytest.raises(ApiError) as stale:
        api.handle_api(
            "POST",
            f"/api/resume/documents/{doc}/analyze",
            {},
            {"expected_sha256": first["content_sha256"]},
        )
    assert stale.value.code == "stale"


def test_export_checks_are_read_never_made(api: JobsApi) -> None:
    doc = master(api).id
    conn = api.connect()
    before = conn.execute("SELECT COUNT(*) FROM resume_export").fetchone()[0]
    conn.close()
    run(api, doc)
    conn = api.connect()
    assert conn.execute("SELECT COUNT(*) FROM resume_export").fetchone()[0] == before
    conn.close()


def test_the_editor_and_analyze_share_one_finding_model(api: JobsApi) -> None:
    from career_agent.resume_doc.check import KINDS, findings

    doc = master(api)
    for f in findings(doc.working):
        assert f["kind"] in KINDS and {"category", "severity", "nature", "action"} <= set(f)


# ------------------------------------------------------------- with a job


def test_job_coverage_has_every_state_and_keeps_eligibility_apart(api: JobsApi) -> None:
    doc = master(api).id

    def tableau(data: dict[str, Any]) -> None:
        data["experience"][0]["bullets"].append(
            {"id": new_id(), "text": "Built Salesforce Apex triggers.", "origin": "USER_AUTHORED"}
        )

    edit(api, doc, tableau)
    result = run(api, doc, ad=AD)
    rows = result["job"]["requirements"]
    states = {r["state"] for r in rows}
    assert {analyze.SHOWN, analyze.NOT_SHOWN, analyze.NOT_FOUND, analyze.ELIGIBILITY} <= states
    assert analyze.UNCONFIRMED in states  # the typed Apex line: mentioned, not confirmed
    authorization = [r for r in rows if "authorized" in r["quote"]]
    assert authorization and authorization[0]["state"] == analyze.ELIGIBILITY
    counts = result["job"]["counts"]
    assert counts["total"] == len([r for r in rows if r["state"] != analyze.ELIGIBILITY])
    assert (
        sum(
            counts[s]
            for s in (analyze.SHOWN, analyze.NOT_SHOWN, analyze.UNCONFIRMED, analyze.NOT_FOUND)
        )
        == counts["total"]
    )
    # Every quote is the employer's own words; the order is required first.
    text = AD["text"]
    assert all(r["quote"] in text for r in rows)
    hard = [r["hardness"] for r in rows if r["state"] != analyze.ELIGIBILITY]
    assert hard.index("REQUIRED") <= min(
        (i for i, h in enumerate(hard) if h == "PREFERRED"), default=len(hard)
    )
    shown = next(r for r in rows if r["state"] == analyze.SHOWN)
    assert shown["lines"], "where in my resume"
    have = next(r for r in rows if r["state"] == analyze.NOT_SHOWN)
    assert have["evidence"], "which confirmed experience"


def test_analyze_and_tailor_agree_on_the_same_inputs(api: JobsApi) -> None:
    conn = api.connect()
    made, _ = tailor(conn, ad=AD)
    conn.close()
    result = run(api, made.id, job="own")
    conn = api.connect()
    said = explain(conn, ResumeStore(conn).get_document(made.id))
    conn.close()
    assert [r["quote"] for r in result["job"]["requirements"]] and len(
        result["job"]["requirements"]
    ) == len(said["coverage"])
    by_quote = {c["ask"]: c["coverage"] for c in said["coverage"]}
    for r in result["job"]["requirements"]:
        if r["state"] == analyze.SHOWN:
            assert by_quote[r["quote"]] in ("COVERED", "PARTLY")
        if r["state"] == analyze.ELIGIBILITY and r["kind"] != "LANGUAGE":
            assert by_quote[r["quote"]] == "ELIGIBILITY"


def test_a_career_agent_job_and_a_pasted_ad_both_work(api: JobsApi) -> None:
    doc = master(api).id
    conn = api.connect()
    job = conn.execute("SELECT id FROM job ORDER BY id LIMIT 1").fetchone()[0]
    conn.close()
    assert run(api, doc, job_id=job)["job"]["job_id"] == job
    assert run(api, doc, ad=AD)["job"]["title"] == AD["title"]
    with pytest.raises(ApiError):
        run(api, doc, job="own")  # a Master is not for a job


# --------------------------------------------------------------- actions


def test_apply_shows_confirmed_experience_and_a_gap_has_no_apply(api: JobsApi) -> None:
    doc = master(api).id
    result = run(api, doc, ad=AD)
    rows = result["job"]["requirements"]
    assert all(r["apply"] is None for r in rows if r["state"] == analyze.NOT_FOUND)
    target = next(r for r in rows if r["state"] == analyze.NOT_SHOWN and r["apply"])
    snapshot = result["job"]["snapshot_id"]
    conn = api.connect()
    sha = ResumeStore(conn).get_document(doc).working_sha256
    conn.close()
    api.handle_api(
        "POST",
        f"/api/resume/documents/{doc}/analysis/changes",
        {},
        {"key": target["apply"], "expected_sha256": sha, "jd_snapshot_id": snapshot},
    )
    after = run(api, doc, ad=AD)
    row = next(r for r in after["job"]["requirements"] if r["quote"] == target["quote"])
    assert row["state"] == analyze.SHOWN


def test_an_exact_duplicate_can_be_removed_and_only_that(api: JobsApi) -> None:
    doc = master(api).id
    edit(api, doc, _messy)
    result = run(api, doc)
    dup = next(f for f in result["findings"] if f["kind"] == "DUPLICATE_BULLET")
    conn = api.connect()
    stored = ResumeStore(conn).get_document(doc)
    conn.close()
    before = sum(len(e.bullets) for e in stored.working.experience)
    api.handle_api(
        "POST",
        f"/api/resume/documents/{doc}/analysis/changes",
        {},
        {"key": dup["key"], "expected_sha256": stored.working_sha256},
    )
    conn = api.connect()
    after = sum(len(e.bullets) for e in ResumeStore(conn).get_document(doc).working.experience)
    conn.close()
    assert after == before - 1 and "DUPLICATE_BULLET" not in kinds(run(api, doc))
    long = next(f for f in result["findings"] if f["kind"] == "LONG_SUMMARY")
    with pytest.raises(ApiError):
        api.handle_api(
            "POST",
            f"/api/resume/documents/{doc}/analysis/changes",
            {},
            {"key": long["key"], "expected_sha256": "x"},
        )


def test_a_dismissal_is_for_this_resume_and_this_condition(api: JobsApi) -> None:
    doc = master(api).id
    edit(api, doc, _messy)
    long = next(f for f in run(api, doc)["findings"] if f["kind"] == "LONG_SUMMARY")
    api.handle_api(
        "POST", f"/api/resume/documents/{doc}/analysis/dismissals", {}, {"key": long["key"]}
    )
    result = run(api, doc)
    assert "LONG_SUMMARY" not in kinds(result) and result["dismissed"] == 1
    with pytest.raises(ApiError):
        api.handle_api(
            "POST",
            f"/api/resume/documents/{doc}/analysis/dismissals",
            {},
            {"key": "LONG_SUMMARY:invented"},
        )
    # Another resume still hears about its own long summary.
    conn = api.connect()
    copy = ResumeStore(conn).copy_document(doc, title="Copy")
    conn.close()
    assert "LONG_SUMMARY" in kinds(run(api, copy.id))


# ----------------------------------------------------- boundaries, invariants


_SCORE = re.compile(
    r"\b(ats|resume|match|fit)\s+score\b|hiring probability|\d+\s?%\s*(match|fit)", re.IGNORECASE
)


def test_nothing_analyze_returns_is_a_score(api: JobsApi) -> None:
    result = run(api, master(api).id, ad=AD)
    keys = json.dumps(
        {k: v for k, v in result.items() if k != "job"} | {"counts": result["job"]["counts"]}
    )
    for word in ("score", "probability", "percent", "rating"):
        assert word not in keys.casefold(), word


def test_the_catalogue_says_no_score_either() -> None:
    catalogue = Path("src/career_agent/web/static/js/i18n.js").read_text(encoding="utf-8")
    analyze_lines = [ln for ln in catalogue.splitlines() if "'rv.an." in ln]
    assert analyze_lines
    for ln in analyze_lines:
        assert not _SCORE.search(ln), ln


def test_analyze_changes_nothing_else(api: JobsApi) -> None:
    conn = api.connect()

    def digest(table: str) -> str:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()  # noqa: S608
        return hashlib.sha256(repr([tuple(r) for r in rows]).encode()).hexdigest()

    watched = (
        "job_match",
        "verified_claim",
        "career_experience",
        "job_application",
        "application_resume",
        "resume_finding_dismissal",
    )
    before = {t: digest(t) for t in watched}
    docs = conn.execute("SELECT id, preferred, working_sha256 FROM resume_document").fetchall()
    run(api, master(api).id, ad=AD)
    assert {t: digest(t) for t in watched} == before
    assert (
        conn.execute("SELECT id, preferred, working_sha256 FROM resume_document").fetchall() == docs
    )
    conn.close()


def test_another_profile_cannot_analyze_or_dismiss(tmp_path: Path) -> None:
    a, b = make(tmp_path, "a"), make(tmp_path, "b", who=thin)
    doc = master(a).id
    for path, body in [
        ("/analyze", {"expected_sha256": "x"}),
        ("/analysis/dismissals", {"key": "NO_CONTACT:identity"}),
    ]:
        with pytest.raises(ApiError) as missing:
            b.handle_api("POST", f"/api/resume/documents/{doc}{path}", {}, body)
        assert missing.value.status == 404


@pytest.mark.parametrize(
    ("size", "who", "ad", "limit"),
    [
        ("sparse", thin, MIXED_AD, 1.0),
        ("normal", senior, SENIOR_AD, 1.0),
        (
            "large",
            _large,
            "\n".join(
                [SENIOR_AD] + [f"- Build HubSpot report {i} for revenue teams" for i in range(60)]
            ),
            3.0,
        ),
    ],
)
def test_analyze_is_fast(tmp_path: Path, size: str, who: Any, ad: str, limit: float) -> None:
    import time

    from tests.integration.test_resume_master import profile

    conn = profile(tmp_path, size)
    who(conn)
    doc = ResumeStore(conn).current_master().id
    started = time.perf_counter()
    analyze.run(conn, doc)
    alone = time.perf_counter() - started
    snap = ResumeStore(conn).create_jd_snapshot(text=ad, title="Synthetic job")
    started = time.perf_counter()
    analyze.run(conn, doc, snapshot_id=snap.id)
    with_job = time.perf_counter() - started
    conn.close()
    print(f"\nANALYZE {size}: resume {alone * 1000:.0f} ms, with job {with_job * 1000:.0f} ms")
    assert alone < limit and with_job < limit


# ------------------------------------------------- review round 1 findings


def _retire_and_job(api: JobsApi) -> dict[str, Any]:
    conn = api.connect()
    _retire(conn, "k-hubspot-routing")
    conn.close()
    return run(api, master(api).id, ad=AD)


def test_evidence_no_longer_confirmed_is_not_support(api: JobsApi) -> None:
    rows = _retire_and_job(api)["job"]["requirements"]
    hubspot = next(r for r in rows if r["quote"] == "Experience with HubSpot")
    assert hubspot["state"] != analyze.SHOWN or not any(
        "lead routing" in line for line in hubspot["lines"]
    )


def test_typed_or_edited_words_are_never_confirmed_support(api: JobsApi) -> None:
    doc = master(api).id

    def claims_apex(data: dict[str, Any]) -> None:
        role = data["experience"][0]
        role["bullets"].append(
            {
                "id": new_id(),
                "origin": "USER_AUTHORED",
                "text": "Wrote Salesforce Apex triggers and dbt models.",
                "evidence_ids": ["k-hubspot-routing"],
            }
        )
        edited = role["bullets"][0]
        edited.update(
            text=edited["text"] + " Also Workato certification.",
            override="EDITED",
            original_text=edited["text"],
        )

    edit(api, doc, claims_apex)
    result = run(api, doc, ad=AD)
    rows = {r["quote"]: r["state"] for r in result["job"]["requirements"]}
    for ask in ("Salesforce Apex is a must", "Experience with dbt", "Workato certification"):
        assert rows[ask] != analyze.SHOWN, ask
    assert result["overview"]["evidence"]["USER_AUTHORED"] >= 1


def test_hidden_text_is_never_counted(api: JobsApi) -> None:
    doc = master(api).id

    def hide(data: dict[str, Any]) -> None:
        data["summary"] = {
            "id": new_id(),
            "text": "Salesforce Apex expert.",
            "origin": "USER_AUTHORED",
        }
        custom = {
            "id": new_id(),
            "heading": "Extra",
            "items": [{"id": new_id(), "text": "Salesforce Apex.", "origin": "USER_AUTHORED"}],
        }
        data["custom_sections"] = [custom]
        data["layout"]["hidden_sections"] = ["summary", f"custom:{custom['id']}"]

    edit(api, doc, hide)
    result = run(api, doc, ad=AD)
    apex = next(r for r in result["job"]["requirements"] if "Apex" in r["quote"])
    assert apex["state"] == analyze.NOT_FOUND and apex["lines"] == []
    assert result["overview"]["evidence"]["USER_AUTHORED"] == 0


def test_tenure_is_partly_never_simply_shown(api: JobsApi) -> None:
    result = run(api, master(api).id, ad=AD)
    years = next(r for r in result["job"]["requirements"] if "10+ years" in r["quote"])
    assert years["state"] != analyze.SHOWN or years["partly"]
    assert result["job"]["counts"]["partly"] >= 0


def test_bad_snapshots_and_blocking_dismissals_are_said(api: JobsApi) -> None:
    doc = master(api).id
    for snapshot in (["x"], 5):
        with pytest.raises(ApiError) as bad:
            api.handle_api(
                "POST",
                f"/api/resume/documents/{doc}/analysis/changes",
                {},
                {"key": "add:r", "expected_sha256": "x", "jd_snapshot_id": snapshot},
            )
        assert bad.value.status == 400
    with pytest.raises(ApiError) as missing:
        api.handle_api(
            "POST",
            f"/api/resume/documents/{doc}/analysis/changes",
            {},
            {"key": "add:r", "expected_sha256": "x", "jd_snapshot_id": "nope"},
        )
    assert missing.value.status in (404, 409)
    edit(api, doc, lambda d: d["identity"].update(email=None, phone=None, links=[]))
    with pytest.raises(ApiError) as blocking:
        api.handle_api(
            "POST",
            f"/api/resume/documents/{doc}/analysis/dismissals",
            {},
            {"key": "NO_CONTACT:identity"},
        )
    assert blocking.value.code == "blocking"


def test_hidden_entries_raise_no_findings(api: JobsApi) -> None:
    doc = master(api).id

    def hidden_copy(data: dict[str, Any]) -> None:
        copy = {
            **data["experience"][0],
            "id": new_id(),
            "hidden": True,
            "bullets": [{**b, "id": new_id()} for b in data["experience"][0]["bullets"]],
        }
        data["experience"].append(copy)
        data["layout"]["hidden_sections"] = ["skills"]

    edit(api, doc, hidden_copy)
    found = kinds(run(api, doc))
    assert "DUPLICATE_ROLE" not in found and "DUPLICATE_BULLET" not in found
    assert "DUPLICATE_SKILL" not in found


def test_a_repeat_keeps_the_evidence_copy(api: JobsApi) -> None:
    doc = master(api).id

    def typed_first(data: dict[str, Any]) -> None:
        role = data["experience"][0]
        role["bullets"].insert(
            0, {"id": new_id(), "text": role["bullets"][0]["text"], "origin": "USER_AUTHORED"}
        )

    edit(api, doc, typed_first)
    dup = next(f for f in run(api, doc)["findings"] if f["kind"] == "DUPLICATE_BULLET")
    conn = api.connect()
    stored = ResumeStore(conn).get_document(doc)
    conn.close()
    api.handle_api(
        "POST",
        f"/api/resume/documents/{doc}/analysis/changes",
        {},
        {"key": dup["key"], "expected_sha256": stored.working_sha256},
    )
    conn = api.connect()
    role = ResumeStore(conn).get_document(doc).working.experience[0]
    conn.close()
    assert role.bullets[0].origin.value != "USER_AUTHORED"
