# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Product flows on synthetic data: candidate isolation, backup roundtrip, the
demo candidate end to end through the candidate-scoped API, evidence-safe
editing, and the simple/advanced payload contract."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from resume_tailor.api.app import create_app
from resume_tailor.presentation.labels import assert_simple_payload
from resume_tailor.workspace import WorkspaceStore
from resume_tailor.workspace.backup import export_backup, import_backup
from resume_tailor.workspace.demo import DEMO_JD, create_demo_candidate


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    store = WorkspaceStore(tmp_path_factory.mktemp("home"))
    ws = create_demo_candidate(store)
    client = TestClient(create_app(home=store.home))
    run_id = client.post(
        f"/api/candidates/{ws.id}/tailor",
        json={
            "jd_text": DEMO_JD,
            "resume_id": "demo_systems_engineer",
            "options": {"use_llm": False},
        },
    ).json()["application_id"]
    for _ in range(120):
        resp = client.get(f"/api/candidates/{ws.id}/applications/{run_id}").json()
        assert "status" in resp, resp
        st = resp["status"]
        if st["status"] in ("done", "error"):
            break
        time.sleep(0.4)
    assert st["status"] == "done", st
    return client, store, ws.id, run_id


def test_demo_candidate_works_from_a_clean_install(rig):
    client, _store, cid, run_id = rig
    view = client.get(f"/api/candidates/{cid}/applications/{run_id}").json()["view"]
    assert_simple_payload(view)
    assert view["match"]["strong"] >= 2 and view["resume"]["headline"]
    assert view["filename"].startswith("Alex Morgan - ")
    exp = client.get(f"/api/candidates/{cid}/experience").json()
    assert exp["total"] >= 10 and exp["confirmed"] >= 1 and exp["conflicting"] >= 1
    conflicts = client.get(f"/api/candidates/{cid}/conflicts").json()
    assert any(c["topic"] == "Cobalt Peak end date" for c in conflicts)


def test_candidate_isolation(rig):
    client, _store, cid, _run = rig
    other = client.post("/api/candidates", json={"name": "Blank Person"}).json()["id"]
    assert client.get(f"/api/candidates/{other}/applications").json() == []
    assert client.get(f"/api/candidates/{other}/experience").json()["total"] == 0
    assert client.get(f"/api/candidates/{other}/resumes").json() == []
    demo_exp = client.get(f"/api/candidates/{cid}/experience").json()
    assert demo_exp["total"] > 0  # untouched by the other candidate


def test_backup_roundtrip(rig):
    _client, store, cid, _run = rig
    ws = store.get(cid)
    imported = import_backup(store, export_backup(ws))
    assert imported.id != cid
    assert imported.load_index().bank.candidate.name == "Alex Morgan"


def test_evidence_safe_editing(rig):
    client, _store, cid, run_id = rig
    draft = client.get(f"/api/candidates/{cid}/applications/{run_id}/draft").json()
    bullet = draft["resume"]["experience"][0]["bullets"][0]
    out = client.post(
        f"/api/candidates/{cid}/applications/{run_id}/draft/edit",
        json={
            "op": "bullet_text",
            "bullet_id": bullet["id"],
            "text": "Administered Salesforce company-wide for a decade.",
        },
    ).json()
    check = out["edit_check"]
    assert check["ok"] is False and check["message"].startswith("This wording goes beyond")
    assert_simple_payload(check)
    md = client.get(f"/api/candidates/{cid}/applications/{run_id}/export/md").content.decode(
        "utf-8"
    )
    assert "Administered Salesforce" not in md  # evidence-only export ships validated wording


def test_multi_format_export_shares_one_final_draft(rig, monkeypatch):
    """Word, PDF and Markdown all come from the same saved draft: a hidden bullet is
    absent from every format and the filenames are friendly. The PDF path is proven
    without a renderer by capturing the DOCX it converts."""
    import io

    from docx import Document
    from resume_tailor.export import pagination

    client, _store, cid, run_id = rig
    draft = client.get(f"/api/candidates/{cid}/applications/{run_id}/draft").json()
    pos = draft["resume"]["experience"][0]
    hidden = pos["bullets"][-1]
    client.post(
        f"/api/candidates/{cid}/applications/{run_id}/draft/edit",
        json={"op": "bullet_hide", "bullet_id": hidden["id"], "hidden": True},
    )
    seen: list[bytes] = []
    monkeypatch.setattr(
        pagination, "docx_to_pdf", lambda b: (seen.append(b), (b"%PDF-1.7 test", "test"))[1]
    )
    try:
        texts = {}
        for fmt in ("docx", "pdf", "md"):
            r = client.get(f"/api/candidates/{cid}/applications/{run_id}/export/{fmt}")
            assert r.status_code == 200, (fmt, r.text)
            assert 'filename="Alex Morgan - ' in r.headers["content-disposition"]
            assert r.headers["content-disposition"].endswith(f'.{fmt}"')
            body = seen[-1] if fmt == "pdf" else r.content
            texts[fmt] = (
                "\n".join(p.text for p in Document(io.BytesIO(body)).paragraphs)
                if fmt != "md"
                else body.decode("utf-8")
            )
        for fmt, text in texts.items():
            assert hidden["auto_text"][:40] not in text, fmt
            assert pos["bullets"][0]["auto_text"][:40] in text, fmt
        assert texts["docx"] == texts["pdf"]  # the PDF renders the very DOCX the Word export ships
    finally:
        client.post(f"/api/candidates/{cid}/applications/{run_id}/draft/restore")


def test_pdf_without_a_local_renderer_explains_itself(rig, monkeypatch):
    from resume_tailor.export import pagination

    client, _store, cid, run_id = rig
    monkeypatch.setattr(pagination, "docx_to_pdf", lambda _b: None)
    r = client.get(f"/api/candidates/{cid}/applications/{run_id}/export/pdf")
    assert r.status_code == 501
    assert "Word" in r.json()["detail"]["message"] and "Markdown" in r.json()["detail"]["message"]
    assert_simple_payload(r.json())
