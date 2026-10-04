"""Resume Workspace V2 PR 6: import a PDF or DOCX, review it, then save it.

Reading stores nothing. Saving stores exactly the reviewed values, once, as
imported text that cites no evidence; Career Evidence, the candidate and
scores are never touched. Synthetic files only; the PDF tests print in the
installed Edge (or Chrome) and SKIP, saying so, when neither exists.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_resume_editor_api import call, make_api
from tests.support_import import (
    AMBIGUOUS,
    FOREIGN_HTML,
    PORTUGUESE,
    SIMPLE,
    SPANISH,
    SPARSE,
    TABLE_HEADER,
    UPPERCASE,
    docx,
    long_senior,
    pdf_of,
)
from tests.support_resume import rich

from career_agent.resume_doc.export import docx_bytes, find_browser, print_pdf
from career_agent.resume_doc.models import upgrade_resume_document
from career_agent.resume_doc.render import render_html
from career_agent.resume_doc.store import ResumeStore
from career_agent.storage.db import connect
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError

needs_browser = pytest.mark.skipif(
    find_browser() is None, reason="no Edge or Chrome to print a PDF: NOT verified"
)
TABLES = (
    "resume_document",
    "resume_revision",
    "verified_claim",
    "candidate",
    "cv_import",
    "cv_proposal",
    "job_match",
)


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    return make_api(tmp_path, "a")


def counts(api: JobsApi) -> dict[str, int]:
    with connect(api.config.db_path) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}  # noqa: S608


def read(api: JobsApi, data: bytes, name: str = "resume.docx") -> dict[str, Any]:
    body = {"filename": name, "content_base64": base64.b64encode(data).decode()}
    return call(api, "POST", "/import/read", body)


def save(api: JobsApi, proposal: dict[str, Any], destination: str = "IMPORTED") -> dict[str, Any]:
    body = {"proposal": {**proposal, "source": []}, "destination": destination}
    return call(api, "POST", "/import/save", body)


# ------------------------------------------------------- nothing before Save


def test_reading_saves_nothing_anywhere(api: JobsApi) -> None:
    before = counts(api)
    proposal = read(api, docx(SIMPLE))
    assert proposal["identity"]["name"]["value"] == "Morgan Example"
    assert counts(api) == before, "a read writes no document, claim, candidate or score"


def test_a_refused_file_says_why_and_saves_nothing(api: JobsApi) -> None:
    before = counts(api)
    with pytest.raises(ApiError) as caught:
        read(api, b"%PDF-1.4\n" + b"\x00" * 300, "scan.pdf")
    assert caught.value.code == "import_refused"
    assert caught.value.data["reason"] in ("NO_TEXT", "CORRUPT")
    assert counts(api) == before


# ------------------------------------------------------------------- saving


def test_the_saved_document_is_the_reviewed_one_and_all_imported(api: JobsApi) -> None:
    proposal = read(api, docx(SIMPLE))
    proposal["identity"]["name"]["value"] = "Morgan Q. Example"
    proposal["experience"][0]["lines"][0]["text"]["value"] = "Rebuilt the weekly sales report."
    before = counts(api)
    made = save(api, proposal)
    doc = upgrade_resume_document(made["document"])
    assert doc.identity.full_name == "Morgan Q. Example", "the review, not the guess"
    assert doc.experience[0].bullets[0].text == "Rebuilt the weekly sales report."
    assert made["kind"] == "IMPORTED" and doc.provenance.created_from == "IMPORT"
    assert doc.provenance.import_id and doc.provenance.import_id.startswith("resume-import-1:DOCX:")
    after = counts(api)
    assert after["resume_document"] == before["resume_document"] + 1
    for unchanged in ("verified_claim", "candidate", "cv_import", "cv_proposal", "job_match"):
        assert after[unchanged] == before[unchanged], unchanged
    with connect(api.config.db_path) as conn:
        [revision] = ResumeStore(conn).list_revisions(doc.id)
        assert revision.reason == "IMPORTED"


def test_a_retried_save_is_one_document_and_a_second_import_is_another(api: JobsApi) -> None:
    proposal = read(api, docx(SIMPLE))
    first = save(api, proposal)
    again = save(api, proposal)  # the response was lost; the page sends it again
    assert again["id"] == first["id"] and again["sha256"] == first["sha256"]
    second = save(api, read(api, docx(SIMPLE)))  # the person imports the file again
    assert second["id"] != first["id"]
    assert counts(api)["resume_document"] == 2


def test_a_different_save_under_a_used_id_is_refused(api: JobsApi) -> None:
    proposal = read(api, docx(SIMPLE))
    save(api, proposal)
    proposal["title"] = "Changed after saving"
    with pytest.raises(ApiError) as caught:
        save(api, proposal)
    assert caught.value.status == 409 and caught.value.code == "import_conflict"


def test_an_empty_required_field_is_named_never_invented(api: JobsApi) -> None:
    proposal = read(api, docx(SIMPLE))
    entry = proposal["experience"][0]
    entry["org"]["value"] = "  "
    entry["start"]["value"] = "Spring"
    with pytest.raises(ApiError) as caught:
        save(api, proposal)
    assert caught.value.code == "import_incomplete"
    assert set(caught.value.data["fields"]) == {f"{entry['id']}/org", f"{entry['id']}/start"}
    assert counts(api)["resume_document"] == 0


def test_a_manipulated_proposal_cannot_claim_evidence(api: JobsApi) -> None:
    proposal = read(api, docx(SIMPLE))
    proposal["experience"][0]["lines"][0]["origin"] = "EVIDENCE_VERBATIM"
    with pytest.raises(ApiError) as caught:
        save(api, proposal)
    assert caught.value.status == 400, "the proposal has no origin to set: refused whole"
    proposal = read(api, docx(SIMPLE))
    proposal["report"]["parser_version"] = "pretend"
    made = upgrade_resume_document(save(api, proposal)["document"])
    assert all(b.origin == "IMPORTED" and not b.evidence_ids for b in made.experience[0].bullets)


# ------------------------------------------------------------------- Master


def test_first_resume_may_become_the_master(api: JobsApi) -> None:
    made = save(api, read(api, docx(SIMPLE)), "MASTER")
    assert made["kind"] == "MASTER"


def test_an_existing_master_is_never_overwritten_silently(api: JobsApi) -> None:
    first = save(api, read(api, docx(SIMPLE)), "MASTER")
    with pytest.raises(ApiError) as caught:
        save(api, read(api, docx(PORTUGUESE)), "MASTER")
    assert caught.value.status == 409 and caught.value.code == "master_exists"
    proposal = read(api, docx(PORTUGUESE))
    replaced = save(api, proposal, "REPLACE_MASTER")
    again = save(api, proposal, "REPLACE_MASTER")  # retried: nothing archived twice
    assert again["id"] == replaced["id"]
    with connect(api.config.db_path) as conn:
        store = ResumeStore(conn)
        current = store.current_master()
        assert current is not None and current.id == replaced["id"]
        old = store.get_document(first["id"])
        assert old.kind == "MASTER" and old.archived_at is not None, "kept, archived"
        assert store.list_revisions(first["id"]), "its history stays"
        masters = conn.execute(
            "SELECT COUNT(*) FROM resume_document WHERE kind = 'MASTER' AND archived_at IS NULL"
        ).fetchone()[0]
        assert masters == 1


# --------------------------------------------------------------- isolation


def test_one_profile_never_reaches_anothers_import(api: JobsApi, tmp_path: Path) -> None:
    other = make_api(tmp_path, "b")
    proposal = read(api, docx(SIMPLE))
    made = save(api, proposal)
    with pytest.raises(ApiError) as hidden:
        call(other, "GET", f"/documents/{made['id']}")
    assert hidden.value.status == 404
    # A read keeps nothing on the server: there is no token for B to reuse,
    # and what B saves is B's own document, in B's database.
    copied = save(other, proposal)
    assert copied["id"] == made["id"]
    assert counts(api)["resume_document"] == 1 and counts(other)["resume_document"] == 1


# -------------------------------------------------- Career Evidence, apart


def test_proposing_to_my_experience_confirms_nothing(api: JobsApi) -> None:
    data = docx(SIMPLE)
    save(api, read(api, data))
    before = counts(api)
    staged = api.handle_api(
        "POST",
        "/api/cv/import",
        {},
        {"filename": "resume.docx", "content_base64": base64.b64encode(data).decode()},
    )
    assert staged["summary"]["waiting"] > 0 and staged["summary"]["confirmed"] == 0
    after = counts(api)
    assert after["verified_claim"] == before["verified_claim"], "proposals, not claims"
    assert after["job_match"] == before["job_match"]


# --------------------------------------------------------------- round trip


def _semantic(doc: Any) -> dict[str, Any]:
    """The meaning a reader gets: text without its **emphasis** markup (bold is
    not carried back), and education as printed ("BSc, Information Systems")."""

    def plain(text: str) -> str:
        return text.replace("**", "")

    return {
        "name": doc.identity.full_name,
        "email": doc.identity.email,
        "phone": doc.identity.phone,
        "links": sorted(link.kind for link in doc.identity.links),
        "headline": doc.headline.text if doc.headline else None,
        "summary": doc.summary.text if doc.summary else None,
        "experience": [
            (
                e.display_title,
                e.employer,
                e.start,
                e.end,
                e.current,
                [plain(b.text) for b in e.bullets],
            )
            for e in doc.experience
            if not e.hidden
        ],
        "education": [
            (", ".join(filter(None, [e.degree, e.field_of_study])), e.institution, e.start, e.end)
            for e in doc.education
        ],
        "certifications": [(c.name, c.issuer, c.issued) for c in doc.certifications],
        "skills": [(g.name, [i.label for i in g.items]) for g in doc.skills],
    }


def _roundtrip(api: JobsApi, data: bytes, name: str) -> None:
    source = rich()
    made = upgrade_resume_document(save(api, read(api, data, name))["document"])
    want, got = _semantic(source), _semantic(made)
    want["experience"] = [
        (*e[:5], [b.text.replace("**", "") for b in entry.bullets if not b.hidden])
        for e, entry in zip(
            want["experience"], [x for x in source.experience if not x.hidden], strict=True
        )
    ]
    assert got == want


def test_our_own_docx_export_reads_back_whole(api: JobsApi) -> None:
    doc = rich()
    _roundtrip(api, docx_bytes(doc, render_html(doc, mode="print").html), "rich.docx")


@needs_browser
def test_our_own_pdf_export_reads_back_whole(api: JobsApi) -> None:
    doc = rich()
    _roundtrip(api, print_pdf(render_html(doc, mode="print").html)[0], "rich.pdf")


# ---------------------------------------------------- foreign layouts, timing


@pytest.mark.parametrize(
    "spec",
    [SIMPLE, TABLE_HEADER, UPPERCASE, PORTUGUESE, SPANISH, SPARSE, AMBIGUOUS, long_senior()],
)
def test_every_foreign_layout_saves_after_review(api: JobsApi, spec: Any) -> None:
    proposal = read(api, docx(spec))
    made = upgrade_resume_document(save(api, proposal)["document"])
    assert made.identity.full_name, "a name was proposed and kept"
    lines = sum(len(e.bullets) for e in made.experience) + sum(len(g.items) for g in made.skills)
    lines += sum(len(c.items) for c in made.custom_sections) + len(made.education)
    assert lines, "content survived"


@needs_browser
def test_a_foreign_pdf_reads_its_entries(api: JobsApi) -> None:
    proposal = read(api, pdf_of(FOREIGN_HTML), "avery.pdf")
    first, second = proposal["experience"]
    assert first["start"]["value"] == "2019-03" and first["current"]
    assert first["lines"][0]["text"]["value"].endswith(
        "which used to take three people a full week."
    )
    assert (second["title"]["value"], second["org"]["value"]) == (
        "Shift Coordinator",
        "Example Ground Services",
    )


def test_reading_is_quick(api: JobsApi) -> None:
    data = docx(long_senior())
    started = time.perf_counter()
    read(api, data)
    assert time.perf_counter() - started < 2.0
