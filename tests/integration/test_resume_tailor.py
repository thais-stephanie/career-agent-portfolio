"""Tailor V2 (PR 8): the Master plus one ad, into a grounded job version.

Synthetic people (`tests.support_tailor`), the invented demo jobs and invented
ads. No model, no network. Every attack in the brief is here: numbers, tools,
seniority, quotes, retired evidence, a Master that moves, other profiles.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_resume_master import claim, experience, profile
from tests.support import committed_config_dir
from tests.support_tailor import (
    MIXED_AD,
    NOT_IN_MASTER,
    NUMBERS_AD,
    PT_AD,
    SENIOR_AD,
    TOOLS_AD,
    E,
    senior,
    thin,
)

from career_agent.config.search_config import load_search_config
from career_agent.domain.claims import VerifiedClaim
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.resume_doc import jd
from career_agent.resume_doc.models import upgrade_resume_document
from career_agent.resume_doc.store import EvidenceNotConfirmed, ResumeStore
from career_agent.resume_doc.tailor import (
    TailorFailed,
    retrieve,
    review,
    sources,
    tailor,
    validate,
)
from career_agent.storage.db import connect, transaction
from career_agent.storage.repositories import ClaimRepo
from career_agent.storage.workspace_repo import ensure_candidate
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "evaluation" / "demo" / "demo_postings.yaml"
AD = {"title": "Director of Revenue Operations", "company": "Synthetic Co", "text": SENIOR_AD}


def make(tmp_path: Path, name: str = "a", who: Any = senior) -> JobsApi:
    conn = profile(tmp_path, name)
    who(conn)
    seed_demo(conn, load_search_config(committed_config_dir())[0], source=DEMO)
    db = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=committed_config_dir(), port=0), quiet=True)


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    return make(tmp_path)


def call(api: JobsApi, method: str, path: str, body: dict | None = None) -> Any:
    return api.handle_api(method, f"/api/resume{path}", {}, body or {})


def pasted(api: JobsApi, ad: dict[str, Any] = AD) -> dict[str, Any]:
    return call(api, "POST", "/tailor", ad)


def texts(doc: dict[str, Any], *, visible: bool = True) -> list[str]:
    out = []
    for entry in doc["experience"]:
        out += [b["text"] for b in entry["bullets"] if not (visible and b.get("hidden"))]
    out += [i["label"] for g in doc["skills"] for i in g["items"]]
    return out


def master_of(api: JobsApi) -> dict[str, Any]:
    return call(api, "GET", f"/documents/{call(api, 'GET', '/master')['master']['id']}")


def run_of(api: JobsApi, doc: dict[str, Any]) -> Any:
    with connect(api.config.db_path) as conn:
        run_id = doc["document"]["provenance"]["tailoring_run_id"]
        return ResumeStore(conn).get_tailoring_run(run_id), ResumeStore(
            conn
        ).list_tailoring_changes(run_id)


def jobs(api: JobsApi) -> list[str]:
    with connect(api.config.db_path) as conn:
        return [r[0] for r in conn.execute("SELECT id FROM job ORDER BY id")]


# ------------------------------------------------------------ the pipeline


def test_a_job_is_tailored_from_a_snapshot_and_the_exact_master_revision(api: JobsApi) -> None:
    job = jobs(api)[0]
    made = call(api, "POST", f"/jobs/{job}/tailor")
    doc = made["document"]
    assert made["kind"] == "TAILORED" and made["version_number"] == 1
    prov = doc["provenance"]
    assert prov["created_from"] == "TAILOR" and prov["tailoring_run_id"]
    with connect(api.config.db_path) as conn:
        store = ResumeStore(conn)
        snap = store.get_jd_snapshot(doc["target"]["jd_snapshot_id"])
        text = conn.execute(
            "SELECT r.description_text FROM job j JOIN job_raw r ON r.content_hash = j.content_hash"
            " WHERE j.id = ?",
            (job,),
        ).fetchone()[0]
        assert snap.job_id == job and snap.text == text.strip() and snap.language
        assert snap.text_sha256 == hashlib.sha256(snap.text.encode()).hexdigest()
        first = store.list_revisions(made["id"])[0]
        assert first.reason == "GENERATED"
        rev = store.get_revision(prov["master_revision_id"])
        assert rev.document_id == prov["master_document_id"]
    run, changes = run_of(api, made)
    assert run.mode == "DETERMINISTIC" and run.provider is None and run.model is None
    assert run.status == "DONE" and run.master_revision_id == prov["master_revision_id"]
    for stage in ("analysis", "retrieval", "strategy", "review", "validation"):
        assert run.stages[stage], stage
    for r in run.stages["analysis"]["requirements"]:
        assert r["quote"] in snap.text
    assert all(c.decision == "ACCEPTED" and c.source == "RULE" for c in changes)


def test_the_senior_dogfood_adds_real_support_reports_gaps_and_invents_nothing(
    api: JobsApi,
) -> None:
    master = master_of(api)
    made = pasted(api)
    doc = made["document"]
    added = [
        b for e in doc["experience"] for b in e["bullets"]
        if b["id"] not in {x["id"] for m in master["document"]["experience"] for x in m["bullets"]}
    ]  # fmt: skip
    cited = {k for b in added for k in b["evidence_ids"]}
    assert cited == {"k-n8n-flows", "k-hubspot-workflows"} <= set(NOT_IN_MASTER) | cited
    n8n = next(b for b in added if "n8n" in b["text"])
    assert n8n["origin"] == "RULE_REWRITE" and n8n["text"].startswith("Built n8n")  # "I" dropped
    assert all(b["requirement_ids"] for b in added)
    view = call(api, "GET", f"/documents/{made['id']}/job")
    gaps = {s["ask"] for s in view["suggestions"] if s["kind"] == "NO_EVIDENCE"}
    assert {"Salesforce Apex is a must", "Workato certification", "Experience with dbt"} <= gaps
    assert "Must be authorized to work in the United States" not in gaps  # eligibility, elsewhere
    eligibility = [c for c in view["coverage"] if c["coverage"] == "ELIGIBILITY"]
    assert len(eligibility) == 1 and view["total"] == len(view["coverage"]) - 1
    changed = {c["op"] for c in view["changes"]}
    assert "ADD_BULLET" in changed
    added_change = next(c for c in view["changes"] if c["op"] == "ADD_BULLET")
    assert added_change["asks"] and added_change["where"]["title"] == "Revenue Operations Analyst"
    # Nothing a confirmed source does not hold: every line is the Master's or a claim's.
    with connect(api.config.db_path) as conn:
        confirmed = {
            c.text for c in ClaimRepo(conn).current(ensure_candidate(conn)) if c.verified
        } | {t for c in ClaimRepo(conn).current(ensure_candidate(conn)) for t in c.tools}
    allowed = {x.casefold() for x in set(texts(master["document"], visible=False)) | confirmed}
    for line in texts(doc):
        assert line.casefold() in allowed or "i " + line.casefold() in allowed, line
    assert doc["headline"] == master["document"]["headline"]
    assert doc["summary"] == master["document"]["summary"]


def test_no_number_tool_or_title_from_the_ad_reaches_the_version(api: JobsApi) -> None:
    for ad in (NUMBERS_AD, TOOLS_AD, SENIOR_AD):
        made = pasted(api, {**AD, "text": ad})
        body = " ".join(texts(made["document"]))
        for invented in ("10M", "$10", "20%", "10+", "Apex", "Workato", "dbt", "Salesforce"):
            assert invented not in body, (invented, ad[:30])
        master = master_of(api)["document"]
        for old, new in zip(master["experience"], made["document"]["experience"], strict=True):
            assert (new["display_title"], new["source_title"], new["employer"]) == (
                old["display_title"], old["source_title"], old["employer"],
            )  # fmt: skip
            assert (new["start"], new["end"]) == (old["start"], old["end"])
    titles = {e["display_title"] for e in made["document"]["experience"]}
    assert not any("Director" in t for t in titles)  # the ad's seniority stays the ad's


def test_versions_number_on_and_manual_ones_are_never_called_tailored(api: JobsApi) -> None:
    job = jobs(api)[0]
    manual = call(api, "POST", f"/jobs/{job}/versions")
    second = call(api, "POST", f"/jobs/{job}/tailor")
    third = call(api, "POST", f"/jobs/{job}/tailor")
    assert [manual["version_number"], second["version_number"], third["version_number"]] == [
        1,
        2,
        3,
    ]
    versions = {v["version_number"]: v for v in call(api, "GET", f"/jobs/{job}")["versions"]}
    assert versions[1]["tailored"] is False and versions[2]["tailored"] is True
    assert not any(v["preferred"] for v in versions.values())  # never chosen for the person
    assert call(api, "GET", f"/documents/{manual['id']}/job")["tailored"] is False


def test_the_master_is_never_written_and_a_later_edit_does_not_move_a_version(
    api: JobsApi,
) -> None:
    before = master_of(api)
    made = pasted(api)
    after = master_of(api)
    assert after["document"] == before["document"] and after["sha256"] == before["sha256"]
    edited = {**after["document"], "title": "Changed after tailoring"}
    call(api, "PATCH", f"/documents/{after['id']}/working",
         {"document": edited, "expected_sha256": after["sha256"]})  # fmt: skip
    call(api, "POST", f"/documents/{after['id']}/checkpoint", {"reason": "MANUAL_CHECKPOINT"})
    again = call(api, "GET", f"/documents/{made['id']}")
    assert again["document"]["provenance"] == made["document"]["provenance"]
    with connect(api.config.db_path) as conn:
        rev = ResumeStore(conn).get_revision(made["document"]["provenance"]["master_revision_id"])
    assert rev.content.title == before["document"]["title"]


# --------------------------------------------------------------- the race


def test_evidence_retired_mid_run_saves_nothing(api: JobsApi) -> None:
    with connect(api.config.db_path) as conn:
        before = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in ("resume_document", "jd_snapshot", "tailoring_run", "tailoring_change")
        }
        repo = ClaimRepo(conn)
        candidate = ensure_candidate(conn)

        def retire(stage: str) -> None:
            if stage == "draft":
                last = repo.history(candidate, "k-n8n-flows")[-1]
                repo.supersede(candidate, last.next_revision(verified=False))

        with pytest.raises((EvidenceNotConfirmed, TailorFailed)):
            tailor(conn, ad=AD, pause=retire)
        after = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in before
        }
        assert after == before
        assert repo.history(candidate, "k-n8n-flows")[-1].verified  # rolled back with it


def test_a_master_that_moves_mid_run_is_refused(api: JobsApi) -> None:
    with connect(api.config.db_path) as conn:
        store = ResumeStore(conn)
        master = store.current_master()

        def move(stage: str) -> None:
            if stage == "draft":
                now = store.get_document(master.id)
                store.save_working_copy(
                    master.id, now.working.model_copy(update={"title": "Moved"}),
                    expected_sha256=now.working_sha256,
                )  # fmt: skip
                store.checkpoint_revision(master.id, "MANUAL_CHECKPOINT")

        with pytest.raises(TailorFailed) as caught:
            tailor(conn, ad=AD, pause=move)
        assert {f["check"] for f in caught.value.findings} == {"MASTER"}
        assert store.current_master().title != "Moved"  # rolled back too


# ------------------------------------------------------ the checks, attacked


def _draft(api: JobsApi) -> tuple[Any, Any, Any, Any]:
    made = pasted(api)
    with connect(api.config.db_path) as conn:
        store = ResumeStore(conn)
        prov = made["document"]["provenance"]
        master = store.get_revision(prov["master_revision_id"]).content
        pool = sources(conn, master)
    supports = retrieve(jd.analyse(SENIOR_AD), pool)
    return made["document"], master, pool, supports


@pytest.mark.parametrize(
    ("change", "check"),
    [
        (lambda b: b.update(text=b["text"] + " for 45 regions"), "NUMBERS"),
        (lambda b: b.update(text=b["text"] + " in Salesforce"), "NAMED_TOOLS"),
        (lambda b: b.update(text=b["text"] + " and led the company"), "OVERSTATEMENT"),
        (lambda b: b.update(evidence_ids=["k-draft"]), "GROUNDING"),
    ],
)
def test_the_reviewer_fails_a_line_its_evidence_does_not_hold(
    api: JobsApi, change: Any, check: str
) -> None:
    doc, master, pool, supports = _draft(api)
    master_ids = {b.id for e in master.experience for b in e.bullets}
    added = next(b for e in doc["experience"] for b in e["bullets"] if b["id"] not in master_ids)
    change(added)
    findings = review(upgrade_resume_document(doc), master, pool, supports)
    assert check in {f["check"] for f in findings if f["outcome"] == "FAIL"}


@pytest.mark.parametrize(
    ("field", "value", "check"),
    [
        ("display_title", "Director of Revenue Operations", "SENIORITY"),
        ("source_title", "Director of Revenue Operations", "SENIORITY"),
        ("employer", "Another Company", "EMPLOYER"),
        ("start", {"year": 2001}, "DATES"),
    ],
)
def test_the_reviewer_fails_a_changed_title_employer_or_date(
    api: JobsApi, field: str, value: Any, check: str
) -> None:
    doc, master, pool, supports = _draft(api)
    doc["experience"][0][field] = value
    if field == "start":
        doc["experience"][0]["current"] = True
    findings = review(upgrade_resume_document(doc), master, pool, supports)
    assert check in {f["check"] for f in findings if f["outcome"] == "FAIL"}
    doc2, *_ = _draft(api)
    doc2["experience"].reverse()
    findings = review(upgrade_resume_document(doc2), master, pool, supports)
    assert "CHRONOLOGY" in {f["check"] for f in findings if f["outcome"] == "FAIL"}


def test_a_quote_that_is_not_in_the_ad_is_refused(api: JobsApi) -> None:
    doc, master, _, _ = _draft(api)
    analysis = jd.analyse(SENIOR_AD)
    first = analysis.requirements[0]
    analysis.requirements[0] = jd.Requirement(
        **{**first.__dict__, "source_quote": "Ten years at a FAANG company"}
    )
    with connect(api.config.db_path) as conn:
        rev_id = doc["provenance"]["master_revision_id"]
        problems = validate(
            conn, upgrade_resume_document(doc), analysis, SENIOR_AD, rev_id,
            doc["provenance"]["master_document_id"],
        )  # fmt: skip
    assert "QUOTE" in {p["check"] for p in problems}


# ------------------------------------------------------- other people, ads


def test_a_thin_profile_gets_no_filler_and_sees_its_gaps(tmp_path: Path) -> None:
    api = make(tmp_path, "thin", thin)
    master = master_of(api)
    made = pasted(api)
    assert texts(made["document"]) == texts(master["document"])  # nothing to add, nothing added
    assert made["document"]["summary"] is None and made["document"]["headline"] is None
    view = call(api, "GET", f"/documents/{made['id']}/job")
    assert view["supported"] == 0 < view["total"]
    assert sum(s["kind"] == "NO_EVIDENCE" for s in view["suggestions"]) == view["total"]


def test_portuguese_and_mixed_ads_tailor_and_stay_grounded(api: JobsApi) -> None:
    for ad in (PT_AD, MIXED_AD):
        made = pasted(api, {"title": "Especialista", "company": None, "text": ad})
        view = call(api, "GET", f"/documents/{made['id']}/job")
        covered = {c["ask"] for c in view["coverage"] if c["coverage"] != "NOT_FOUND"}
        assert any("n8n" in ask for ask in covered), ad[:20]
        assert "Salesforce" not in " ".join(texts(made["document"]))
    gaps = {
        s["ask"] for s in call(api, "GET", f"/documents/{made['id']}/job")["suggestions"]
        if s["kind"] == "NO_EVIDENCE"
    }  # fmt: skip
    assert gaps  # the mixed ad's Zapier/English asks stay asks


def test_another_profile_cannot_reach_any_of_it(api: JobsApi, tmp_path: Path) -> None:
    made = pasted(api)
    other = make(tmp_path, "b", thin)
    with pytest.raises(ApiError) as hidden:
        call(other, "GET", f"/documents/{made['id']}/job")
    assert hidden.value.status == 404
    theirs = pasted(other)
    keys = {k for e in theirs["document"]["experience"] for b in e["bullets"]
            for k in b["evidence_ids"]}  # fmt: skip
    assert keys and not keys & {"k-n8n-flows", "k-hubspot-workflows", "k-hubspot-routing"}
    with connect(other.config.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tailoring_run WHERE id = ?",
                            (made["document"]["provenance"]["tailoring_run_id"],)
                            ).fetchone()[0] == 0  # fmt: skip


def test_no_master_no_tailoring(tmp_path: Path) -> None:
    def nobody(conn: Any) -> None:
        with transaction(conn):
            claim(
                conn,
                "k-only",
                E,
                "Did a synthetic thing.",
                experience=experience(conn, "Co", "Role", "2020-01", None, current=True),
            )

    api = make(tmp_path, "none", nobody)
    with pytest.raises(ApiError) as refused:
        pasted(api)
    assert refused.value.code == "no_master"
    with pytest.raises(ApiError):
        pasted(api, {"title": "", "text": "x"})


# -------------------------------------------------------------- invariants

SEARCH_TABLES = ("job_match", "job_score_revision", "search_fit_feedback", "semantic_evaluation",
                 "job_application", "verified_claim", "career_experience", "career_evidence_link",
                 "candidate")  # fmt: skip


def _state(api: JobsApi) -> dict[str, Any]:
    with connect(api.config.db_path) as conn:
        tables = {
            t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")]  # noqa: S608
            for t in SEARCH_TABLES
        }
    config = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(api.config.config_dir).glob("*.yaml"))
    }
    return {"tables": tables, "config": config}


def test_tailoring_moves_no_search_fit_score_preference_or_evidence(api: JobsApi) -> None:
    before = _state(api)
    job = jobs(api)[0]
    call(api, "POST", f"/jobs/{job}/tailor")
    pasted(api)
    view = call(
        api, "GET", f"/documents/{call(api, 'GET', f'/jobs/{job}')['versions'][0]['id']}/job"
    )
    assert view["coverage"]
    assert _state(api) == before


# ---------------------------------------------------------- make it better


def test_make_it_better_offers_confirmed_lines_gaps_and_dismissals(api: JobsApi) -> None:
    # A manual version for the senior ad: the same people, the same ad, by hand.
    made = pasted(api)
    copy = call(api, "POST", f"/documents/{made['id']}/copy", {})
    view = call(api, "GET", f"/documents/{copy['id']}/job")
    assert view["tailored"] is False
    gaps = [s for s in view["suggestions"] if s["kind"] == "NO_EVIDENCE"]
    assert gaps and all(s["action"] is None for s in gaps)  # no fake Apply for a gap
    call(api, "POST", f"/documents/{copy['id']}/dismissals", {"key": gaps[0]["key"]})
    again = call(api, "GET", f"/documents/{copy['id']}/job")
    assert gaps[0]["key"] not in {s["key"] for s in again["suggestions"]}
    assert gaps[0]["key"] in {
        s["key"] for s in call(api, "GET", f"/documents/{made['id']}/job")["suggestions"]
    }  # fmt: skip  -- another version keeps it
    # Remove an added confirmed line: Make it better offers it back, exactly.
    doc = copy["document"]
    n8n = next(b for e in doc["experience"] for b in e["bullets"] if "n8n" in b["text"])
    for e in doc["experience"]:
        e["bullets"] = [b for b in e["bullets"] if b["id"] != n8n["id"]]
    call(api, "PATCH", f"/documents/{copy['id']}/working",
         {"document": doc, "expected_sha256": copy["sha256"]})  # fmt: skip
    offered = [
        s for s in call(api, "GET", f"/documents/{copy['id']}/job")["suggestions"]
        if s["kind"] == "UNSHOWN_EVIDENCE"
    ]  # fmt: skip
    add = next(s["action"] for s in offered if s["action"]["type"] == "add_bullet")
    assert add["evidence_ids"] == ["k-n8n-flows"] and add["text"] == n8n["text"]


def test_a_long_line_is_pointed_at_not_rewritten(api: JobsApi) -> None:
    made = pasted(api)
    doc = made["document"]
    first = doc["experience"][0]["bullets"][0]
    first.update(text=" ".join(["Wrote"] + ["clear"] * 45), origin="USER_AUTHORED",
                 evidence_ids=[], override="NONE", original_text=None)  # fmt: skip
    call(api, "PATCH", f"/documents/{made['id']}/working",
         {"document": doc, "expected_sha256": made["sha256"]})  # fmt: skip
    long = [s for s in call(api, "GET", f"/documents/{made['id']}/job")["suggestions"]
            if s["kind"] == "LONG_LINE"]  # fmt: skip
    assert long and long[0]["words"] == 46 and long[0]["action"] is None


# ------------------------------------------------------------- performance


def _large(conn: Any) -> None:
    with transaction(conn):
        for n in range(15):
            role = experience(conn, f"Company {n}", f"Role {n}", f"{2000 + n}-01", f"{2000 + n}-12")
            for k in range(10):
                claim(conn, f"k-{n}-{k}", E,
                      f"Built HubSpot and n8n automation number {k} for team {n} with SQL reports.",
                      experience=role, tools=["HubSpot"])  # fmt: skip
    from career_agent.resume_doc.master import get_or_create_master

    get_or_create_master(conn)


@pytest.mark.parametrize(("size", "who", "ad", "limit"), [
    ("small", thin, MIXED_AD, 2.0),
    ("normal", senior, SENIOR_AD, 2.0),
    ("large", _large, "\n".join([SENIOR_AD] + [f"- Build HubSpot report {i} for revenue teams"
                                              for i in range(60)]), 5.0),
])  # fmt: skip
def test_tailoring_is_fast(tmp_path: Path, size: str, who: Any, ad: str, limit: float) -> None:
    conn = profile(tmp_path, size)
    who(conn)
    started = time.perf_counter()
    _, report = tailor(conn, ad={**AD, "text": ad})
    elapsed = time.perf_counter() - started
    stages = report["validation"]["timings_ms"]
    print(f"\nTAILOR {size}: {elapsed * 1000:.0f} ms total, stages {stages}")
    assert elapsed < limit


def test_the_run_says_no_ai_ran(api: JobsApi) -> None:
    made = pasted(api)
    run, _ = run_of(api, made)
    text = json.dumps(run.stages)
    for vendor in ("openai", "anthropic", "gemini", "ollama", "claude"):
        assert vendor not in text.lower()
    assert run.stages["options"] == {"ai": False}


def test_a_claim_never_confirmed_is_never_used(api: JobsApi) -> None:
    made = pasted(api)
    keys = {
        k for e in made["document"]["experience"] for b in e["bullets"] for k in b["evidence_ids"]
    }
    assert "k-draft" not in keys
    assert "Apex rewrite" not in " ".join(texts(made["document"]))
    assert isinstance(VerifiedClaim, type)


# ------------------------------------------------------- review regressions


def _support_for(api: JobsApi, ask: str, extra: Any = None) -> Any:
    with connect(api.config.db_path) as conn:
        if extra:
            extra(conn)
        master = ResumeStore(conn).current_master()
        pool = sources(conn, master.working)
    (sup,) = retrieve(jd.analyse(f"Requirements\n- {ask}\n"), pool)
    return sup


@pytest.mark.parametrize("ask", ["Experience with Excel", "Experience with Power BI",
                                 "Experience with Go"])  # fmt: skip
def test_an_ordinary_word_never_answers_a_named_ask(api: JobsApi, ask: str) -> None:
    def coach(conn: Any) -> None:
        with transaction(conn):
            role = experience(conn, "Coaching Co", "Coach", "2010-01", "2011-01")
            claim(conn, "k-excel-word", E, "Excel at coaching new account executives.",
                  experience=role)  # fmt: skip
            claim(conn, "k-power-word", E,
                  "Helped the sales team go to market with new power dashboards and BI reporting.",
                  experience=role)  # fmt: skip

    sup = _support_for(api, ask, coach)
    assert sup.state == "NO_EVIDENCE" and sup.coverage == "NOT_FOUND", ask


def test_evidence_of_an_archived_role_stays_out(api: JobsApi) -> None:
    def archived(conn: Any) -> None:
        with transaction(conn):
            role = experience(conn, "Old Co", "Platform Engineer", "2005-01", "2006-01",
                              archived=True)  # fmt: skip
            claim(conn, "k-k8s", E, "Ran Kubernetes clusters.", experience=role,
                  tools=["Kubernetes"])  # fmt: skip

    assert _support_for(api, "Experience with Kubernetes", archived).state == "NO_EVIDENCE"


def test_a_line_only_typed_by_the_person_is_said_not_covered(api: JobsApi) -> None:
    made = pasted(api)
    doc = made["document"]
    doc["experience"][0]["bullets"].append(
        {"id": "01" + "Z" * 24, "text": "Led a Salesforce Apex rewrite.", "origin": "USER_AUTHORED"}
    )
    call(api, "PATCH", f"/documents/{made['id']}/working",
         {"document": doc, "expected_sha256": made["sha256"]})  # fmt: skip
    view = call(api, "GET", f"/documents/{made['id']}/job")
    apex = next(c for c in view["coverage"] if c["ask"] == "Salesforce Apex is a must")
    assert apex["coverage"] == "SAID"


def test_apply_is_made_by_the_server_from_the_confirmed_text_now(api: JobsApi) -> None:
    made = pasted(api)
    doc = made["document"]
    for entry in doc["experience"]:
        entry["bullets"] = [b for b in entry["bullets"] if "k-n8n-flows" not in b["evidence_ids"]]
    saved = call(api, "PATCH", f"/documents/{made['id']}/working",
                 {"document": doc, "expected_sha256": made["sha256"]})  # fmt: skip
    # The person corrects the claim after the suggestion was first shown.
    with connect(api.config.db_path) as conn, transaction(conn):
        repo, candidate = ClaimRepo(conn), ensure_candidate(conn)
        last = repo.history(candidate, "k-n8n-flows")[-1]
        repo.supersede(candidate, last.next_revision(text="Built n8n integrations for invoicing."))
    view = call(api, "GET", f"/documents/{made['id']}/job")
    key = next(s["key"] for s in view["suggestions"] if s["kind"] == "UNSHOWN_EVIDENCE")
    with pytest.raises(ApiError) as unknown:
        call(api, "POST", f"/documents/{made['id']}/accept",
             {"key": "add:nothing", "expected_sha256": saved["sha256"]})  # fmt: skip
    assert unknown.value.status == 409
    out = call(api, "POST", f"/documents/{made['id']}/accept",
               {"key": key, "expected_sha256": saved["sha256"]})  # fmt: skip
    lines = texts(out["document"])
    assert "Built n8n integrations for invoicing." in lines
    assert "Built n8n integrations between HubSpot and the billing system." not in lines
    with pytest.raises(ApiError) as twice:
        call(api, "POST", f"/documents/{made['id']}/accept",
             {"key": key, "expected_sha256": out["sha256"]})  # fmt: skip
    assert twice.value.status == 409  # applied: it no longer stands


def test_only_a_real_suggestion_is_dismissed(api: JobsApi) -> None:
    made = pasted(api)
    with pytest.raises(ApiError) as unknown:
        call(api, "POST", f"/documents/{made['id']}/dismissals", {"key": "gap:invented"})
    assert unknown.value.status == 404


def test_a_padded_ad_is_refused_at_the_door(api: JobsApi) -> None:
    with pytest.raises(ApiError) as big:
        pasted(api, {**AD, "text": "Requirements\n- HubSpot\n" + " " * 70_000})
    assert big.value.status == 400
