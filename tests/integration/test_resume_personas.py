"""Resume Tailor v1.1: the structure adapts to what each person's confirmed
evidence holds, on synthetic people only. Every person gets the same checks:
no empty section, no placeholder, no line without confirmed evidence, no
number a confirmed claim does not hold, the Master untouched, and files that
read back (JSON, DOCX with real links; PDF where a browser exists). No model,
no network. All names, employers and addresses are invented."""

from __future__ import annotations

import io
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_resume_master import claim, profile
from tests.support_resume import acknowledge_saved

from career_agent.clock import new_id, now_utc
from career_agent.domain.enums import ClaimType
from career_agent.resume_doc import analyze, check, report
from career_agent.resume_doc.ats import blocks, docx_links
from career_agent.resume_doc.export import export_dir, export_revision, find_browser
from career_agent.resume_doc.master import evidence_changes, get_or_create_master
from career_agent.resume_doc.models import ResumeDocument, upgrade_resume_document
from career_agent.resume_doc.render import render_html
from career_agent.resume_doc.store import ResumeStore
from career_agent.resume_doc.tailor import tailor
from career_agent.storage.db import transaction
from career_agent.storage.workspace_repo import ensure_candidate

A, S, C = ClaimType.ACHIEVEMENT, ClaimType.SKILL, ClaimType.CERTIFICATION

CS_AD = """Customer Success Specialist

Requirements
- Experience with customer onboarding
- SQL for reporting
- Training new support hires
- Clear written communication with customers

Nice to have
- Zendesk administration
"""
DATA_AD = """Junior Data Analyst

Requirements
- SQL and Python for analysis
- Dashboards in Excel or a BI tool
- Present findings to non-technical teams

Nice to have
- An internship or project with real data
"""


@dataclass
class Role:
    company: str
    title: str
    start: str
    end: str | None
    kind: str = "EMPLOYMENT"
    #: (key, type, text, tools)
    claims: list[tuple[str, ClaimType, str, list[str]]] = field(default_factory=list)


@dataclass
class Person:
    name: str
    ad: str
    roles: list[Role] = field(default_factory=list)
    #: Claims in no role: (key, type, text)
    loose: list[tuple[str, ClaimType, str]] = field(default_factory=list)
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    links: dict[str, str] = field(default_factory=dict)
    education: list[dict[str, Any]] = field(default_factory=list)


def _experience(conn: sqlite3.Connection, role: Role) -> str:
    candidate = ensure_candidate(conn)
    company_id, exp_id = new_id(), new_id()
    conn.execute(
        "INSERT INTO career_company (id, candidate_id, label) VALUES (?, ?, ?)",
        (company_id, candidate, role.company),
    )
    conn.execute(
        "INSERT INTO career_experience (id, candidate_id, company_id, title, period_start,"
        " period_end, current_role, archived, kind, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
        (exp_id, candidate, company_id, role.title, role.start, role.end,
         int(role.end is None), role.kind, now_utc()),
    )  # fmt: skip
    return exp_id


def build(conn: sqlite3.Connection, person: Person) -> None:
    with transaction(conn):
        for role in person.roles:
            exp = _experience(conn, role)
            for key, kind, text, tools in role.claims:
                claim(conn, key, kind, text, experience=exp, tools=tools)
        for key, kind, text in person.loose:
            claim(conn, key, kind, text)
    master, _ = get_or_create_master(conn)
    data = master.working.model_dump(mode="json")
    data["identity"].update(
        full_name=person.name, email=person.email, phone=person.phone, city=person.city,
        links=[{"id": new_id(), "kind": k, "url": u} for k, u in person.links.items()],
    )  # fmt: skip
    data["education"] = [{"id": new_id(), **e} for e in person.education]
    ResumeStore(conn).save_working_copy(master.id, upgrade_resume_document(data),
                                        expected_sha256=master.working_sha256)  # fmt: skip


SENIOR = Person(
    "Rowan Example", CS_AD, email="rowan@example.com", phone="+1 555 0100",
    city="Denver, CO", links={"LINKEDIN": "https://www.linkedin.com/in/rowan-example"},
    roles=[
        Role("Brightfield Example Inc", "Senior Customer Success Manager", "2019-03", None, claims=[
            ("s1", A, "Raised renewal rates from 81% to 92% across 140 accounts by running "
                      "quarterly reviews with each customer's operations lead.", []),
            ("s2", A, "Ran customer onboarding for 35 new accounts with a shared launch checklist.",
             []),
            ("s3", A, "Trained 12 support hires on the escalation process.", []),
            ("s4", A, "Wrote SQL reports that showed churn risk to account managers every week.",
             ["SQL"]),
        ]),
        Role("Northdesk Fictional Ltd", "Customer Success Manager", "2015-01", "2019-02", claims=[
            ("s5", A, "Resolved 1,200 support tickets a quarter for analytics customers.", []),
            ("s6", S, "Customer onboarding", []),
        ]),
        Role("Old Mill Example", "Support Specialist", "2011-06", "2014-12", claims=[
            ("s7", A, "Wrote the help-centre articles for the reporting module.", ["Zendesk"]),
        ]),
    ],
    loose=[("s8", C, "Certified Customer Success Manager")],
)  # fmt: skip
MID = Person(
    "Sasha Example", CS_AD, email="sasha@example.com",
    roles=[
        Role("Lumen Synthetic Ltd", "Customer Support Analyst", "2021-02", None, claims=[
            ("m1", A, "Answered customer questions about billing and account setup by email.", []),
            ("m2", A, "Kept the onboarding guide current after each product release.", []),
            ("m3", S, "Customer onboarding", []),
        ]),
        Role("Quillfeather Example", "Support Assistant", "2019-05", "2021-01", claims=[
            ("m4", A, "Logged and routed incoming tickets in Zendesk.", ["Zendesk"]),
        ]),
    ],
)  # fmt: skip
TRAINEE = Person(
    "Kai Example", DATA_AD, email="kai@example.com",
    roles=[
        Role("Harbor Fictional Co", "Operations Trainee", "2025-02", None, claims=[
            ("t1", A, "Updated the weekly stock spreadsheet for the warehouse team.", ["Excel"]),
        ]),
        Role("Example State University", "Retail sales dashboard in Python", "2024-03", "2024-12",
             kind="ACADEMIC", claims=[
            ("t2", A, "Built a sales dashboard in Python and SQL from a public retail dataset, "
                      "and presented the findings to the class.", ["Python", "SQL"]),
        ]),
    ],
    education=[{"institution": "Example State University", "degree": "BSc",
                "field_of_study": "Statistics", "start": {"year": 2022}}],
)  # fmt: skip
INTERN = Person(
    "Noor Example", DATA_AD, phone="+1 555 0101",
    roles=[
        Role("Coastline Example Analytics", "Data Analyst Intern", "2025-06", "2025-08", claims=[
            ("i1", A, "Cleaned survey data in Python and wrote SQL queries for the monthly report.",
             ["Python", "SQL"]),
            ("i2", A, "Presented the survey findings to the marketing team.", []),
        ]),
    ],
    education=[{"institution": "Example Technical College", "degree": "BSc",
                "field_of_study": "Information Systems", "start": {"year": 2023}}],
)  # fmt: skip
NEW_GRAD = Person(
    "Jordan Example", DATA_AD, email="jordan@example.com",
    links={"GITHUB": "https://github.com/jordan-example"},
    roles=[
        Role("Example State University", "Capstone: bus ridership analysis", "2024-09", "2025-05",
             kind="ACADEMIC", claims=[
            ("g1", A, "Analysed two years of bus ridership data in Python and SQL and presented "
                      "route changes to the city transit office.", ["Python", "SQL"]),
        ]),
    ],
    loose=[("g2", S, "Excel")],
    education=[{"institution": "Example State University", "degree": "BA",
                "field_of_study": "Economics", "start": {"year": 2021}, "end": {"year": 2025}}],
)  # fmt: skip
NO_JOB = Person(
    "Avery Lane Example", DATA_AD, email="avery.lane@example.com",
    roles=[
        Role("", "Household budget tracker", "2025-01", None, kind="PERSONAL", claims=[
            ("n1", A, "Built a budget tracker in Python that reads bank exports.", ["Python"]),
        ]),
    ],
)  # fmt: skip
ONLY_PROJECTS = Person(
    "Riley Example", DATA_AD, email="riley@example.com",
    links={"PORTFOLIO": "https://riley-example.dev"},
    roles=[
        Role("", "Library loans dashboard", "2024-04", "2024-06", kind="PERSONAL", claims=[
            ("p1", A, "Built a library loans dashboard in Excel from open city data.", ["Excel"]),
        ]),
        Role("Example Community College", "Course project: rainfall trends", "2023-09", "2023-12",
             kind="ACADEMIC", claims=[
            ("p2", A, "Wrote SQL queries over ten years of rainfall records.", ["SQL"]),
        ]),
    ],
)  # fmt: skip
NO_PROJECTS = MID
NO_LINKS = MID
ONE_LINK = Person(
    "Morgan Lee Example",
    CS_AD,
    email="morgan.lee@example.com",
    links={"LINKEDIN": "https://www.linkedin.com/in/morgan-lee-example"},
    roles=MID.roles,
)
CHANGER = Person(
    "Taylor Example", CS_AD, email="taylor@example.com",
    roles=[
        Role("Example Middle School", "Science Teacher", "2016-08", None, claims=[
            ("c1", A, "Trained 6 new teachers on the school's grading system.", []),
            ("c2", A, "Wrote weekly progress notes to 120 families.", []),
            ("c3", S, "Training", []),
        ]),
    ],
)  # fmt: skip
RETURNING = Person(
    "Casey Example", CS_AD, email="casey@example.com",
    roles=[
        Role("Northwind Example Ltd", "Customer Support Specialist", "2014-03", "2019-06", claims=[
            ("r1", A, "Ran customer onboarding calls for new small-business accounts.", []),
            ("r2", A, "Trained 4 support hires on the ticket queue.", []),
            ("r3", S, "Customer onboarding", []),
        ]),
    ],
)  # fmt: skip
INSUFFICIENT = Person("Dana Example", CS_AD, loose=[("x1", S, "Customer service")])
FULL = Person(
    "Harper Full Example", CS_AD, email="harper.full@example.com", phone="+1 555 0102",
    city="Austin, TX",
    links={"LINKEDIN": "https://www.linkedin.com/in/harper-full-example",
           "PORTFOLIO": "https://harper-full-example.dev",
           "GITHUB": "https://github.com/harper-full-example"},
    roles=[
        *SENIOR.roles[:2],
        Role("", "Support macros library", "2023-01", "2023-06", kind="PERSONAL", claims=[
            ("f1", A, "Built a shared library of 40 support macros for onboarding questions.",
             ["Zendesk"]),
        ]),
    ],
    loose=[("f2", C, "Zendesk Support Administrator")],
    education=[{"institution": "Example State University", "degree": "BA",
                "field_of_study": "Communication", "start": {"year": 2007}, "end": {"year": 2011}}],
)  # fmt: skip

PEOPLE = {
    "senior": SENIOR, "mid": MID, "trainee": TRAINEE, "intern": INTERN, "new_grad": NEW_GRAD,
    "no_job": NO_JOB, "only_projects": ONLY_PROJECTS, "no_projects": NO_PROJECTS,
    "no_links": NO_LINKS, "one_link": ONE_LINK, "changer": CHANGER, "returning": RETURNING,
    "insufficient": INSUFFICIENT, "full": FULL,
}  # fmt: skip


# ----------------------------------------------------------------- checks

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_PLACEHOLDER = re.compile(r"(?i)lorem|ipsum|example_\d|\bTBD\b|\bN/A\b|\{\w+\}|your name|seu nome")


def made(tmp_path: Path, person: Person) -> tuple[sqlite3.Connection, ResumeDocument, str]:
    conn = profile(tmp_path)
    build(conn, person)
    master = ResumeStore(conn).current_master()
    assert master is not None
    before = master.working_sha256
    stored, _ = tailor(conn, ad={"title": person.ad.splitlines()[0], "text": person.ad})
    assert ResumeStore(conn).get_document(master.id).working_sha256 == before, "Master written"
    return conn, stored.working, stored.id


def confirmed(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["claim_key"]: r["text"] for r in analyze._career(conn)[1]
            if r["state"] == "CONFIRMED"}  # fmt: skip


def headings(doc: ResumeDocument) -> list[str]:
    return [b.ref.split("/", 1)[1] for b in blocks(render_html(doc, mode="print").html)
            if b.kind == "heading"]  # fmt: skip


def invariants(conn: sqlite3.Connection, doc: ResumeDocument) -> None:
    html = render_html(doc, mode="print").html
    # No section without content: every heading is followed by something of its own.
    for section in re.findall(r'<section class="rv-section".*?</section>', html, re.S):
        assert re.search(r"<li|rv-text|rv-skills|rv-entry", section), section[:120]
    shown = blocks(html)
    text = "\n".join(b.text for b in shown)
    assert not _PLACEHOLDER.search(text), _PLACEHOLDER.search(text)
    claims = confirmed(conn)
    for line in _evidenced(doc.model_dump(mode="json")):
        assert set(line) <= set(claims), line
    held = {n for t in claims.values() for n in _NUMBER.findall(t)}
    said = [b for b in shown if b.kind in ("bullet", "text", "skills")]
    for block in said:
        assert set(_NUMBER.findall(block.text)) <= held, block.text


def _evidenced(node: Any) -> list[list[str]]:
    found: list[list[str]] = []
    if isinstance(node, dict):
        if node.get("evidence_ids"):
            found.append(node["evidence_ids"])
        for value in node.values():
            found += _evidenced(value)
    elif isinstance(node, list):
        for value in node:
            found += _evidenced(value)
    return found


def flags(conn: sqlite3.Connection, doc_id: str, ad: str) -> set[str]:
    stored = ResumeStore(conn).get_document(doc_id)
    return {f["code"] for f in report.build(conn, stored.working, ad, page_limit=2)["risk_flags"]}


# ------------------------------------------------------------------ tests


@pytest.mark.parametrize("who", sorted(PEOPLE))
def test_every_person_gets_a_resume_of_what_they_have_and_nothing_else(
    tmp_path: Path, who: str
) -> None:
    conn, doc, _ = made(tmp_path, PEOPLE[who])
    invariants(conn, doc)
    shown = headings(doc)
    person = PEOPLE[who]
    jobs = [r for r in person.roles if r.kind == "EMPLOYMENT"]
    others = [r for r in person.roles if r.kind != "EMPLOYMENT"]
    assert ("experience" in shown) == bool(jobs)
    assert ("projects" in shown) == bool(others)
    assert ("education" in shown) == bool(person.education)
    assert ("certifications" in shown) == any(k == C for _, k, _ in person.loose)
    # Projects keep their true class: no academic or personal work under Experience.
    assert {e.employer for e in doc.experience} <= {r.company for r in jobs}
    assert all("Languages" not in h and "Idiomas" not in h for h in shown)


def test_the_summary_follows_the_evidence_and_never_invents_a_role(tmp_path: Path) -> None:
    expected = {"senior": True, "mid": True, "intern": True, "changer": True, "returning": True,
                "new_grad": False, "no_job": False, "only_projects": False,
                "insufficient": False}  # fmt: skip
    for who, has in expected.items():
        conn, doc, doc_id = made(tmp_path / who, PEOPLE[who])
        assert (doc.summary is not None) == has, who
        if has:
            assert doc.summary is not None
            sentences = doc.summary.text.count(". ") + 1
            assert 2 <= sentences <= 4 and len(doc.summary.text.split()) <= 80, doc.summary.text
            # The role is the person's own, never the job's title.
            first = PEOPLE[who].roles[0].title
            assert doc.summary.text.startswith(first), (who, doc.summary.text)
        else:
            assert "SUMMARY_MISSING" in flags(conn, doc_id, PEOPLE[who].ad), who
        assert "SUMMARY_SHORT" not in flags(conn, doc_id, PEOPLE[who].ad)


def test_the_summary_repeats_at_most_one_shown_bullet(tmp_path: Path) -> None:
    conn, doc, _ = made(tmp_path, SENIOR)
    assert doc.summary is not None
    bullets = {b.text.rstrip(".") for e in doc.experience for b in e.bullets if not b.hidden}
    repeated = [s for s in doc.summary.text.split(". ") if s.rstrip(".") in bullets]
    assert len(repeated) <= 1, doc.summary.text


def test_no_job_history_puts_education_and_projects_first(tmp_path: Path) -> None:
    for who, first in (("new_grad", ["education", "projects"]), ("only_projects", ["projects"])):
        _, doc, _ = made(tmp_path / who, PEOPLE[who])
        assert headings(doc)[: len(first)] == first, (who, headings(doc))


def test_an_experienced_person_keeps_experience_first(tmp_path: Path) -> None:
    for who in ("senior", "full", "returning"):
        _, doc, _ = made(tmp_path / who, PEOPLE[who])
        shown = headings(doc)
        assert (
            shown.index("experience")
            < min(shown.index(s) for s in ("projects", "education") if s in shown)
            if {"projects", "education"} & set(shown)
            else True
        )
        assert shown[0] in ("summary", "experience"), shown


def test_an_order_the_person_chose_is_kept(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    build(conn, NEW_GRAD)
    store = ResumeStore(conn)
    master = store.current_master()
    assert master is not None
    data = master.working.model_dump(mode="json")
    chosen = ["headline", "summary", "skills", "experience", "projects", "education",
              "certifications"]  # fmt: skip
    data["layout"]["section_order"] = chosen
    store.save_working_copy(master.id, upgrade_resume_document(data),
                            expected_sha256=master.working_sha256)  # fmt: skip
    stored, _ = tailor(conn, ad={"title": "Junior Data Analyst", "text": DATA_AD})
    assert stored.working.layout.section_order == chosen


def test_a_career_gap_is_shown_as_it_is(tmp_path: Path) -> None:
    _, doc, _ = made(tmp_path, RETURNING)
    [role] = doc.experience
    assert (role.start.year, role.start.month) == (2014, 3)  # type: ignore[union-attr]
    assert (role.end.year, role.end.month) == (2019, 6) and not role.current  # type: ignore[union-attr]
    text = " ".join(b.text for b in blocks(render_html(doc, mode="print").html)).casefold()
    for invented in ("career break", "sabbatical", "caregiving", "gap"):
        assert invented not in text


def test_a_career_changer_is_not_given_the_new_field(tmp_path: Path) -> None:
    _, doc, _ = made(tmp_path, CHANGER)
    text = " ".join(b.text for b in blocks(render_html(doc, mode="print").html))
    assert "Science Teacher" in text
    assert "Customer Success" not in text.replace("Customer Success Specialist", "")


def test_contact_is_one_way_to_reach_the_person_or_a_blocking_finding(tmp_path: Path) -> None:
    for who, blocking in (("insufficient", True), ("intern", False), ("mid", False)):
        conn, doc, _ = made(tmp_path / who, PEOPLE[who])
        found = {f["kind"] for f in check.findings(doc)} | {
            f["kind"] for f in analyze._structure(doc)
        }
        assert ("NO_CONTACT" in found) == blocking, who
        # Phone alone is enough: email is not demanded on top of it.
        assert ("EMAIL_MISSING" in found) == blocking, who


@pytest.mark.parametrize("who", ["full", "one_link", "no_links", "new_grad"])
def test_the_files_read_back_with_their_links(tmp_path: Path, who: str) -> None:
    conn, doc, doc_id = made(tmp_path, PEOPLE[who])
    acknowledge_saved(conn, doc_id)
    sha = ResumeStore(conn).get_document(doc_id).working_sha256
    js = export_revision(conn, doc_id, "JSON", expected_sha256=sha)
    assert js.ats_check["verified"]
    data = (export_dir(conn) / js.file_path).read_bytes()
    back = upgrade_resume_document(data.decode("utf-8"))
    assert back.identity.links == doc.identity.links and back.projects == doc.projects
    word = export_revision(conn, doc_id, "DOCX", expected_sha256=sha)
    checks = {c["check"]: c["status"] for c in word.ats_check["checks"]}
    assert word.ats_check["verified"], word.ats_check
    urls = {link.url for link in doc.identity.links}
    held = docx_links((export_dir(conn) / word.file_path).read_bytes())
    assert held == urls
    assert checks["LINKS"] == ("PASS" if urls else "NOT_MEASURED")


def test_profiles_never_share_a_line(tmp_path: Path) -> None:
    _, senior, _ = made(tmp_path / "a", SENIOR)
    _, grad, _ = made(tmp_path / "b", NEW_GRAD)
    a = " ".join(b.text for b in blocks(render_html(senior, mode="print").html))
    b = " ".join(x.text for x in blocks(render_html(grad, mode="print").html))
    assert "ridership" not in a and "renewal" not in b


def test_an_older_master_with_projects_under_experience_still_tailors(tmp_path: Path) -> None:
    """A Master made before v1.1 holds academic work under Experience. It is
    never rewritten: the version keeps what the Master holds."""
    conn = profile(tmp_path)
    build(conn, TRAINEE)
    store = ResumeStore(conn)
    master = store.current_master()
    assert master is not None
    data = master.working.model_dump(mode="json")
    old = data.pop("projects")
    data["projects"] = []
    for p in old:
        data["experience"].append({"id": p["id"], "employer": "Example State University",
                                   "display_title": "Retail sales dashboard in Python",
                                   "start": p["start"], "end": p["end"], "bullets": p["bullets"],
                                   "hidden": False, "current": False})  # fmt: skip
    store.save_working_copy(master.id, upgrade_resume_document(data),
                            expected_sha256=master.working_sha256)  # fmt: skip
    stored, _ = tailor(conn, ad={"title": "Junior Data Analyst", "text": DATA_AD})
    assert len(stored.working.experience) == 2 and not stored.working.projects
    assert not evidence_changes(conn, store.current_master().working)["added"]  # type: ignore[union-attr]


def test_a_new_master_holds_academic_work_as_a_project_and_reports_nothing_missing(
    tmp_path: Path,
) -> None:
    conn = profile(tmp_path)
    build(conn, TRAINEE)
    master = ResumeStore(conn).current_master()
    assert master is not None
    assert [p.name for p in master.working.projects] == [
        "Retail sales dashboard in Python, Example State University"
    ]
    assert [e.display_title for e in master.working.experience] == ["Operations Trainee"]
    changes = evidence_changes(conn, master.working)
    assert not changes["added"] and not changes["experiences_added"]


# ------------------------------------------------------------ the real PDF


def _edge() -> bool:
    return find_browser() is not None


@pytest.mark.skipif(not _edge(), reason="no Edge or Chrome on this machine")
@pytest.mark.parametrize(("who", "most"), [("full", 2), ("new_grad", 1)])
def test_the_real_pdf_keeps_order_contacts_and_links(tmp_path: Path, who: str, most: int) -> None:
    from pypdf import PdfReader

    from career_agent.resume_doc.ats import pdf_links, read_pdf

    conn, doc, doc_id = made(tmp_path, PEOPLE[who])
    acknowledge_saved(conn, doc_id)
    sha = ResumeStore(conn).get_document(doc_id).working_sha256
    pdf = export_revision(conn, doc_id, "PDF", expected_sha256=sha)
    assert pdf.page_count is not None and pdf.page_count <= most
    data = (export_dir(conn) / pdf.file_path).read_bytes()
    assert len(PdfReader(io.BytesIO(data)).pages) == pdf.page_count
    text = " ".join(" ".join(read_pdf(data)).split())
    person = PEOPLE[who]
    assert text.startswith(person.name)
    for value in (person.email, person.phone):
        if value:
            assert value in text
    held = {u.rstrip("/") for u in pdf_links(data)}
    assert held >= {link.url.rstrip("/") for link in doc.identity.links}
    names = {"experience": "EXPERIENCE", "projects": "PROJECTS", "education": "EDUCATION"}
    order = [names[h] for h in headings(doc) if h in names]
    assert [text.index(h) for h in order] == sorted(text.index(h) for h in order)
    failed = [c for c in pdf.ats_check["checks"] if c["status"] == "FAIL"]
    assert not failed, failed


def test_skills_only_a_project_shows_are_never_shown_as_a_jobs(tmp_path: Path) -> None:
    """Python and SQL appear only in the trainee's academic project: they sit
    under Independent projects, and the summary names only the job's skill."""
    _, doc, _ = made(tmp_path, TRAINEE)
    groups = {g.name: {i.label for i in g.items} for g in doc.skills if not g.hidden}
    assert {"Python", "SQL"} <= groups.get("Independent projects", set()), groups
    assert "Excel" in groups.get("Skills", set()), groups
    assert doc.summary is not None
    assert "Python" not in doc.summary.text and "Excel" in doc.summary.text
