"""The Professional Summary of a version for a job (spec v1, S3), on synthetic
people only: composed from confirmed evidence when the Master has none,
offered (never applied) when it has one, never padded, never invented, kept
through the review, the fit and every export. No model, no network."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_resume_master import claim, experience, profile
from tests.integration.test_resume_page_gate import printer
from tests.integration.test_resume_tailor_v1 import AD, avery
from tests.support_resume import acknowledge_saved, confirm_cited, long

from career_agent.clock import new_id
from career_agent.domain.enums import ClaimType
from career_agent.resume_doc import analyze, budget, report, review, summary
from career_agent.resume_doc.export import export_dir, export_revision
from career_agent.resume_doc.master import get_or_create_master
from career_agent.resume_doc.models import Origin, upgrade_resume_document
from career_agent.resume_doc.render import render_html
from career_agent.resume_doc.store import ResumeStore
from career_agent.resume_doc.tailor import apply_suggestion, explain, tailor
from career_agent.storage.db import transaction

E = ClaimType.ACHIEVEMENT
JOB = {"title": "Customer Success Engineer", "text": AD}

#: A fuller synthetic person: enough confirmed statements for 70 to 110 words.
HARPER = [
    (
        "h-renew",
        "now",
        "Raised renewal rates from 81% to 92% across 140 mid-market accounts by running "
        "quarterly account reviews with each customer's operations lead.",
    ),
    (
        "h-onboard",
        "now",
        "Ran customer onboarding for 35 new accounts with a shared launch checklist, "
        "a kickoff call and a written handover to the support team.",
    ),
    (
        "h-sql",
        "now",
        "Wrote SQL reports that showed churn risk to account managers every Monday, "
        "ranked by contract value and days since the last login.",
    ),
    ("h-train", "now", "Trained 12 support hires on the escalation process and its runbooks."),
    ("h-tickets", "prev", "Resolved 1,200 support tickets a quarter for analytics customers."),
    ("h-docs", "prev", "Wrote the help-centre articles for the reporting module."),
    ("h-old", "old", "Kept the warehouse stock list up to date."),
]


def harper(conn: sqlite3.Connection) -> None:
    with transaction(conn):
        roles = {
            "now": experience(conn, "Brightfield Example Inc", "Customer Success Engineer",
                              "2021-04", None, current=True),
            "prev": experience(conn, "Northdesk Fictional Ltd", "Technical Support Specialist",
                               "2017-02", "2021-03"),
            "old": experience(conn, "Old Mill Example", "Warehouse Assistant", "2012-01",
                              "2014-06"),
        }  # fmt: skip
        for key, role, text in HARPER:
            tools = ["SQL"] if key == "h-sql" else []
            claim(conn, key, E, text, experience=roles[role], tools=tools)
        claim(conn, "h-skill-sql", ClaimType.SKILL, "SQL")
        claim(conn, "h-onboarding", ClaimType.SKILL, "Customer onboarding", experience=roles["now"])
        claim(conn, "h-zendesk", ClaimType.TOOL, "Zendesk", experience=roles["prev"])
        claim(conn, "h-unconfirmed", E, "Cut churn by 40% in one quarter.", experience=roles["now"],
              verified=False)  # fmt: skip
    master, _ = get_or_create_master(conn)
    data = master.working.model_dump(mode="json")
    data["identity"]["full_name"] = "Harper Example"
    ResumeStore(conn).save_working_copy(master.id, upgrade_resume_document(data),
                                        expected_sha256=master.working_sha256)  # fmt: skip


def confirmed_texts(conn: sqlite3.Connection) -> dict[str, str]:
    rows = analyze._career(conn)[1]
    return {r["claim_key"]: r["text"] for r in rows if r["state"] == "CONFIRMED"}


def composed_change(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    ops = [c.op for c in ResumeStore(conn).list_tailoring_changes(run_id)]
    return next(op for op in ops if op["op"] == "ADD_SUMMARY")


def set_master_summary(conn: sqlite3.Connection, text: str) -> None:
    store = ResumeStore(conn)
    master = store.current_master()
    assert master is not None
    data = master.working.model_dump(mode="json")
    data["summary"] = {"id": new_id(), "text": text, "origin": "USER_AUTHORED"}
    store.save_working_copy(master.id, upgrade_resume_document(data),
                            expected_sha256=master.working_sha256)  # fmt: skip


# ------------------------------------------------------- Master and Tailor


def test_a_master_without_a_summary_is_never_given_one(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    avery(conn)
    store = ResumeStore(conn)
    master = store.current_master()
    assert master is not None and master.working.summary is None
    before = master.working_sha256
    tailor(conn, ad=JOB)
    assert store.get_document(master.id).working_sha256 == before  # the Master is not written


def test_a_version_of_a_master_without_one_gets_a_summary_from_confirmed_evidence(
    tmp_path: Path,
) -> None:
    conn = profile(tmp_path)
    avery(conn)
    stored, _ = tailor(conn, ad=JOB)
    said = stored.working.summary
    assert said is not None and said.origin is Origin.RULE_REWRITE
    texts = confirmed_texts(conn)
    assert said.evidence_ids and set(said.evidence_ids) <= set(texts)
    assert said.text.startswith("Customer Success Engineer with experience at Quillfeather")
    change = composed_change(conn, stored.working.provenance.tailoring_run_id)
    sentences = change["sentences"]
    assert summary.MIN_SENTENCES <= len(sentences) <= summary.MAX_SENTENCES
    assert len(said.text.split()) <= summary.MAX_WORDS and len(said.text) <= summary.MAX_CHARS
    # Every statement sentence is one confirmed statement of a job, word for word.
    for s in sentences:
        if s["kind"] == "STATEMENT":
            [key] = s["evidence_ids"]
            assert s["text"].rstrip(".") == texts[key].rstrip(".")
    assert "Kubernetes" not in said.text  # a lab's skill is no job's
    # Nothing waits for review and Analyze finds nothing on it.
    assert not [i for i in review.pending(conn, stored.working) if i.ref == said.id]
    assert not [f for f in analyze.run(conn, stored.id)["findings"] if f.get("ref") == said.id]


def test_rich_evidence_gives_two_to_four_sentences_with_little_repetition(tmp_path: Path) -> None:
    """Resume Tailor v1.1: 2 to 4 sentences, at most 80 words, and at most one
    sentence that repeats a bullet the page shows."""
    conn = profile(tmp_path)
    harper(conn)
    stored, _ = tailor(conn, ad=JOB)
    said = stored.working.summary
    assert said is not None
    sentences = composed_change(conn, stored.working.provenance.tailoring_run_id)["sentences"]
    assert 2 <= len(sentences) <= 4 and len(said.text.split()) <= summary.MAX_WORDS, said.text
    assert sum(bool(s.get("repeats_bullet")) for s in sentences) <= 1
    # Job skills only (the ad names SQL); a proven number stays as proven.
    skills = next(s for s in said.text.split(". ") if s.startswith("Skilled in"))
    assert "SQL" in skills
    # The most relevant statement leads; any number is a confirmed one, as written.
    texts = confirmed_texts(conn)
    for s in sentences:
        if s["kind"] == "STATEMENT":
            assert s["text"].rstrip(".") == texts[s["evidence_ids"][0]].rstrip(".")
    # An unconfirmed statement never reaches it, nor its number.
    assert "40%" not in said.text and "h-unconfirmed" not in said.evidence_ids
    flags = {f["code"] for f in report.build(conn, stored.working, AD, page_limit=2)["risk_flags"]}
    assert "SUMMARY_MISSING" not in flags


def test_little_evidence_gives_a_short_summary_never_padded_nor_flagged(tmp_path: Path) -> None:
    """Avery holds four job statements and one job skill: a short summary is
    what the evidence holds. Short and specific is no fault: never flagged."""
    conn = profile(tmp_path)
    avery(conn)
    stored, _ = tailor(conn, ad=JOB)
    said = stored.working.summary
    assert said is not None and len(said.text.split()) <= summary.MAX_WORDS
    sentences = composed_change(conn, stored.working.provenance.tailoring_run_id)["sentences"]
    assert {s["kind"] for s in sentences} <= {"ROLE", "SKILLS", "STATEMENT"}
    flags = {f["code"] for f in report.build(conn, stored.working, AD, page_limit=2)["risk_flags"]}
    assert not flags & {"SUMMARY_MISSING", "SUMMARY_SHORT"}


def test_not_enough_evidence_leaves_no_summary_and_says_so(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    with transaction(conn):
        role = experience(conn, "Quiet Example Co", "Analyst", "2020-01", None, current=True)
        claim(conn, "q-skill", ClaimType.SKILL, "SQL", experience=role)
    get_or_create_master(conn)
    stored, _ = tailor(conn, ad=JOB)
    assert stored.working.summary is None
    flags = {f["code"] for f in report.build(conn, stored.working, AD, page_limit=2)["risk_flags"]}
    assert "SUMMARY_MISSING" in flags


def test_a_summary_the_person_hid_stays_hidden(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    avery(conn)
    store = ResumeStore(conn)
    master = store.current_master()
    assert master is not None
    data = master.working.model_dump(mode="json")
    data["layout"]["hidden_sections"] = [*data["layout"]["hidden_sections"], "summary"]
    store.save_working_copy(master.id, upgrade_resume_document(data),
                            expected_sha256=master.working_sha256)  # fmt: skip
    stored, _ = tailor(conn, ad=JOB)
    assert stored.working.summary is None
    assert not [s for s in explain(conn, stored)["suggestions"] if s["kind"] ==
                "SUMMARY_FROM_EVIDENCE"]  # fmt: skip


def test_the_masters_summary_is_kept_and_a_composed_one_only_offered(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    avery(conn)
    own = "Engineer who connects business systems so nobody copies data by hand."
    set_master_summary(conn, own)
    store = ResumeStore(conn)
    master = store.current_master()
    assert master is not None
    stored, _ = tailor(conn, ad=JOB)
    assert stored.working.summary is not None and stored.working.summary.text == own
    offered = [s for s in explain(conn, stored)["suggestions"] if s["key"] == "summary:compose"]
    assert offered and offered[0]["replaces"] == own and offered[0]["text"] != own
    revisions = len(store.list_revisions(stored.id))
    after = apply_suggestion(conn, stored.id, "summary:compose",
                             expected_sha256=stored.working_sha256)  # fmt: skip
    assert after.working.summary is not None
    assert after.working.summary.text == offered[0]["text"]
    assert after.working.summary.origin is Origin.RULE_REWRITE
    # The copy it replaced is the newest revision (kept, or already there).
    history = store.list_revisions(stored.id)
    assert len(history) >= revisions and history[-1].content.summary.text == own
    assert store.get_document(master.id).working_sha256 == master.working_sha256
    assert not [s for s in explain(conn, after)["suggestions"] if s["key"] == "summary:compose"]


def test_portuguese_versions_get_portuguese_frames(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    avery(conn)
    store = ResumeStore(conn)
    master = store.current_master()
    assert master is not None
    data = master.working.model_dump(mode="json")
    data["language"] = "pt-BR"
    store.save_working_copy(master.id, upgrade_resume_document(data),
                            expected_sha256=master.working_sha256)  # fmt: skip
    stored, _ = tailor(conn, ad=JOB)
    assert stored.working.summary is not None
    assert "com experiência em" in stored.working.summary.text
    assert "Competências em SQL." in stored.working.summary.text


# ------------------------------------------------------- what it may not say


@pytest.mark.parametrize(
    "edit",
    [" Cut churn by 40% in one quarter.", " Led a team of 50 engineers at Salesforce."],
)
def test_a_number_or_name_no_evidence_holds_is_stopped_before_export(
    tmp_path: Path, edit: str
) -> None:
    conn = profile(tmp_path)
    avery(conn)
    stored, _ = tailor(conn, ad=JOB)
    acknowledge_saved(conn, stored.id)
    store = ResumeStore(conn)
    data = stored.working.model_dump(mode="json")
    data["summary"]["text"] += edit  # still claims to be composed from evidence
    store.save_working_copy(stored.id, upgrade_resume_document(data),
                            expected_sha256=stored.working_sha256)  # fmt: skip
    saved = store.get_document(stored.id)
    found = [f for f in analyze.run(conn, stored.id)["findings"]
             if f.get("ref") == saved.working.summary.id]  # fmt: skip
    assert found
    assert [i for i in review.pending(conn, saved.working) if i.ref == saved.working.summary.id]
    with pytest.raises(review.ReviewRequired):
        export_revision(conn, stored.id, "PDF", expected_sha256=saved.working_sha256,
                        pdf=printer(1))  # fmt: skip


def test_the_grounds_say_no_number(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    harper(conn)
    stored, _ = tailor(conn, ad=JOB)
    assert not any(ch.isdigit() for ch in summary.grounds(stored.working).replace(
        stored.working.experience[0].employer, ""))  # fmt: skip


# ------------------------------------------- kept through review, fit, export


def test_the_summary_survives_the_review_and_every_export(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    avery(conn)
    stored, _ = tailor(conn, ad=JOB)
    acknowledge_saved(conn, stored.id)
    store = ResumeStore(conn)
    sha = store.get_document(stored.id).working_sha256
    text = stored.working.summary.text  # type: ignore[union-attr]
    made = export_revision(conn, stored.id, "JSON", expected_sha256=sha, pdf=printer(1))
    held = json.loads((export_dir(conn) / made.file_path).read_text(encoding="utf-8"))
    assert held["summary"]["text"] == text
    from docx import Document

    made = export_revision(conn, stored.id, "DOCX", expected_sha256=sha, pdf=printer(1))
    word = Document(io.BytesIO((export_dir(conn) / made.file_path).read_bytes()))
    styles = [(p.style.name, p.text) for p in word.paragraphs]
    heads = [t for s, t in styles if s == "Heading 1"]
    assert heads.index("Summary") < heads.index("Experience")
    assert ("Normal", text) in styles or any(t == text for _, t in styles)


def test_the_summary_comes_right_after_the_header(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    avery(conn)
    stored, _ = tailor(conn, ad=JOB)
    html = render_html(stored.working).html
    assert html.index('data-ref="identity') < html.index('data-ref="section/summary"')
    assert html.index('data-ref="section/summary"') < html.index('data-ref="section/experience"')


def test_a_fit_never_hides_the_summary(tmp_path: Path) -> None:
    """A long version with a composed summary: the cuts take lines, never it."""
    conn = profile(tmp_path)
    avery(conn)
    stored, _ = tailor(conn, ad=JOB)
    store = ResumeStore(conn)
    own = stored.working.model_dump(mode="json")
    data = long().model_dump(mode="json")
    for key in ("id", "kind", "target", "provenance", "identity", "summary"):
        data[key] = own[key]
    doc = upgrade_resume_document(data)
    confirm_cited(conn, doc)
    store.save_working_copy(stored.id, doc, expected_sha256=stored.working_sha256)
    acknowledge_saved(conn, stored.id)
    sha = store.get_document(stored.id).working_sha256
    plan = budget.propose(conn, stored.id, expected_sha256=sha, measure=budget.estimate_pages)
    assert plan["status"] == "CUT" and all(not c["ref"].startswith("summary") for c in plan["cuts"])
    after = budget.apply_cuts(conn, stored.id, [c["ref"] for c in plan["cuts"]],
                              expected_sha256=sha, proposal=plan["proposal"])  # fmt: skip
    assert after.working.summary is not None
    assert after.working.summary.text == own["summary"]["text"]
    assert "summary" not in after.working.layout.hidden_sections


def test_one_profiles_evidence_never_reaches_anothers_summary(tmp_path: Path) -> None:
    first = profile(tmp_path / "a")
    harper(first)
    other = profile(tmp_path / "b")
    avery(other)
    stored, _ = tailor(other, ad=JOB)
    said = stored.working.summary
    assert said is not None
    assert set(said.evidence_ids) <= set(confirmed_texts(other))
    statements = [t for k, t in confirmed_texts(first).items() if k.startswith("h-") and " " in t]
    assert statements and not any(t.rstrip(".") in said.text for t in statements)
    assert "Brightfield" not in said.text and "Northdesk" not in said.text


# ------------------------------------------------------------ the real PDF


def _edge() -> bool:
    from career_agent.resume_doc.export import find_browser

    return find_browser() is not None


@pytest.mark.skipif(not _edge(), reason="no Edge or Chrome on this machine")
def test_the_real_pdf_holds_the_summary_first_and_fits_two_pages(tmp_path: Path) -> None:
    from career_agent.resume_doc.ats import read_pdf

    conn = profile(tmp_path)
    harper(conn)
    stored, _ = tailor(conn, ad=JOB)
    acknowledge_saved(conn, stored.id)
    sha = ResumeStore(conn).get_document(stored.id).working_sha256
    made = export_revision(conn, stored.id, "PDF", expected_sha256=sha)
    assert made.page_count is not None and made.page_count <= 2
    text = " ".join("\n".join(read_pdf((export_dir(conn) / made.file_path).read_bytes())).split())
    first = stored.working.summary.text.split(". ")[0]  # type: ignore[union-attr]
    # Headings print in capitals; the summary comes after the name, before Experience.
    assert text.index("Harper Example") < text.upper().index("SUMMARY") < text.index(first)
    assert text.index(first) < text.upper().index("EXPERIENCE")
