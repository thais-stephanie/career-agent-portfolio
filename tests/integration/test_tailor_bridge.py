"""Resume Tailor follows the active Career Agent profile (the profile bridge).

Two synthetic profiles, each with its own database and Tailor home, each
served through `tailor_bridge.tailor_app` exactly as the launcher does:

* one profile is one Tailor candidate, by profile id, found again on reopen;
* an empty candidate id is refused before any route (the `//` upload bug);
* the posting handoff carries the canonical job id and reads the posting;
* Career Agent's application status is the truth, read and written through
  the bridge, and never crosses to the other profile;
* only CONFIRMED Career Profile statements reach Tailor, verbatim, with
  provenance, and a base resume is made from them and nothing else;
* a tailored resume attaches to the canonical posting.

Synthetic data only.
"""

from __future__ import annotations

import io
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_documents_review import answer, entry, place, review, upload
from tests.support import committed_config_dir
from tests.support_cv import load_cv

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.runtime.profiles import Profile
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig
from career_agent.web.tailor_bridge import tailor_app

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "evaluation" / "demo" / "demo_postings.yaml"
TAILOR = "http://127.0.0.1"
COMPANY, ROLE = "Fabrikam Cloud", "Senior Solutions Consultant"


def _profile(tmp_path: Path, pid: str, label: str) -> tuple[Profile, JobsApi, TestClient]:
    base = tmp_path / pid
    config = base / "config"
    shutil.copytree(committed_config_dir(), config, ignore=shutil.ignore_patterns("*.local.*"))
    shutil.copyfile(config / "search.worked-example.yaml", config / "search.local.yaml")
    db = base / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, label)
        search, _ = load_search_config(config)
        seed_demo(conn, search, source=DEMO)
    finally:
        conn.close()
    profile = Profile(
        id=pid,
        label=label,
        created_at="2026-09-26T00:00:00Z",
        db=str(db),
        config_dir=str(config),
        tailor_home=str(base / "tailor"),
    )
    api = JobsApi(ServerConfig(db_path=db, config_dir=config, port=0), quiet=True)
    client = TestClient(tailor_app(base / "tailor", profile, api), base_url=TAILOR)
    return profile, api, client


@pytest.fixture
def two(tmp_path: Path):  # noqa: ANN201
    return (
        _profile(tmp_path, "prof-01SYNTHETICAAAAAAAAAAAAAA", "Synthetic A"),
        _profile(tmp_path, "prof-01SYNTHETICBBBBBBBBBBBBBB", "Synthetic B"),
    )


def _jobs(api: JobsApi) -> list[str]:
    with connect(api.config.db_path) as conn:
        return [str(r[0]) for r in conn.execute("SELECT id FROM job ORDER BY id")]


def _status(api: JobsApi, job_id: str) -> str | None:
    with connect(api.config.db_path) as conn:
        row = conn.execute(
            "SELECT status FROM job_application WHERE job_id = ?", (job_id,)
        ).fetchone()
    return str(row[0]) if row else None


# ------------------------------------------------------------------ identity


def test_one_profile_is_one_tailor_candidate_found_again_by_id(two) -> None:
    (profile, _, client), (other, _, other_client) = two
    first = client.get("/api/workspace").json()
    assert first["mode"] == "profile"
    assert first["profile"] == {"id": profile.id, "label": profile.label}
    assert first["candidate_id"] == profile.id.lower()
    again = client.get("/api/workspace").json()
    assert again["candidate_id"] == first["candidate_id"], "reopening made a second candidate"
    candidates = client.get("/api/candidates").json()
    assert [c["id"] for c in candidates] == [first["candidate_id"]], "no duplicate candidates"
    assert other_client.get("/api/workspace").json()["candidate_id"] == other.id.lower()
    # Profile B's Tailor never lists profile A's candidate.
    assert [c["id"] for c in other_client.get("/api/candidates").json()] == [other.id.lower()]


def test_a_single_unclaimed_candidate_is_adopted_not_duplicated(tmp_path: Path) -> None:
    from resume_tailor.api.app import create_app

    profile, api, _ = _profile(tmp_path, "prof-01SYNTHETICCCCCCCCCCCCCCC", "Synthetic C")
    home = Path(profile.tailor_home)
    standalone = TestClient(create_app(home=home), base_url=TAILOR)
    made = standalone.post("/api/candidates", json={"name": "Earlier Tailor use"}).json()
    client = TestClient(tailor_app(home, profile, api), base_url=TAILOR)
    info = client.get("/api/workspace").json()
    assert info["candidate_id"] == made["id"], "the earlier candidate was not adopted"
    assert len(client.get("/api/candidates").json()) == 1


def test_an_empty_candidate_id_is_refused_before_any_route(two) -> None:
    (_, _, client), _ = two
    files = {"file": ("resume.md", b"# Synthetic\n\nA line.\n", "text/markdown")}
    for path in (
        "/api/candidates//resumes/upload",
        "/api/candidates/undefined/resumes/upload",
        "/api/candidates/null/resumes/upload",
    ):
        response = client.post(path, files=files, headers={"Origin": TAILOR})
        assert response.status_code == 400, path
        assert "No candidate is selected" in response.json()["detail"]


# ------------------------------------------------------------------ handoff


def test_the_posting_handoff_reads_the_canonical_job(two) -> None:
    (_, api, client), _ = two
    job_id = _jobs(api)[0]
    job = client.get(f"/api/career/jobs/{job_id}").json()
    assert job["job_id"] == job_id
    assert job["title"] and job["company"] and len(job["description"]) > 100
    assert job["status"] == "DISCOVERED" and job["status_label"] == "Found"
    assert client.get("/api/career/jobs/01ZZZZZZZZZZZZZZZZZZZZZZZZ").status_code == 404
    assert client.get("/api/career/jobs/not%20an%20id").status_code == 400


# --------------------------------------------------------- source of truth


def test_career_agent_status_is_the_truth_and_stays_in_its_profile(two) -> None:
    (_, api_a, client_a), (_, api_b, client_b) = two
    job_id = _jobs(api_a)[0]
    api_a.handle_api("PATCH", f"/api/jobs/{job_id}/status", {}, {"status": "SHORTLISTED"})

    listed = client_a.get("/api/career/applications").json()
    row = next(j for j in listed["jobs"] if j["job_id"] == job_id)
    assert row["status_label"] == "Interested"
    assert "TO_APPLY" not in {s["value"] for s in listed["statuses"]}

    # Changed in Tailor, recorded in Career Agent with its history.
    changed = client_a.patch(
        f"/api/career/applications/{job_id}", json={"status": "APPLIED"}, headers={"Origin": TAILOR}
    )
    assert changed.status_code == 200 and changed.json()["status"] == "APPLIED"
    assert _status(api_a, job_id) == "APPLIED"
    history = api_a.handle_api("GET", f"/api/jobs/{job_id}", {}, {})["history"]
    assert [e["to_status"] for e in history][-2:] == ["SHORTLISTED", "APPLIED"]

    # The other profile has the same public posting and none of this state.
    other_jobs = client_b.get("/api/career/applications").json()["jobs"]
    assert job_id not in {j["job_id"] for j in other_jobs}
    assert _status(api_b, job_id) is None

    bad = client_a.patch(
        f"/api/career/applications/{job_id}", json={"status": "MAYBE"}, headers={"Origin": TAILOR}
    )
    assert bad.status_code == 400


def test_a_switched_away_profile_refuses_the_bridge(two) -> None:
    (_, api, client), _ = two
    api.retired = True
    response = client.get("/api/workspace")
    assert response.status_code == 409
    assert "Reload" in response.json()["detail"]


# ------------------------------------------------------------------ evidence


def _career_profile(api: JobsApi) -> tuple[list[str], str]:
    """One experience from the synthetic CV: two lines confirmed, one left
    waiting. Returns (confirmed texts, the waiting text)."""
    import_id = upload(api, load_cv("markdown_complex.md"), "riley.md")
    job = entry(review(api, import_id), COMPANY, ROLE)
    place(api, import_id, job["key"], "new")
    first, second, waiting = job["items"][0], job["items"][1], job["items"][2]
    answer(api, import_id, first["key"], "CONFIRM")
    answer(api, import_id, second["key"], "CONFIRM")
    return [first["text"], second["text"]], waiting["text"]


def test_only_confirmed_statements_reach_tailor_verbatim_with_provenance(two) -> None:
    (_, api, client), _ = two
    confirmed, waiting = _career_profile(api)
    summary = client.get("/api/career/evidence").json()
    assert summary["details"] == 2
    imported = client.post(
        "/api/career/evidence/import", json={}, headers={"Origin": TAILOR}
    ).json()
    assert imported["details"] == 2
    cid = client.get("/api/workspace").json()["candidate_id"]
    items = client.get(f"/api/candidates/{cid}/experience?advanced=1").json()["items"]
    texts = {i["text"] for i in items}
    assert {" ".join(t.split()) for t in confirmed} <= texts, "a statement was rephrased"
    assert " ".join(waiting.split()) not in texts, "an unconfirmed suggestion crossed"
    assert {i["status"] for i in items} == {"Confirmed by you"}
    # Importing again replaces, never duplicates.
    client.post("/api/career/evidence/import", json={}, headers={"Origin": TAILOR})
    assert len(client.get(f"/api/candidates/{cid}/experience").json()["items"]) == 2


def test_a_base_resume_from_the_career_profile_and_nothing_else(two) -> None:
    (_, api, client), _ = two
    confirmed, waiting = _career_profile(api)
    made = client.post("/api/career/base-resume", json={}, headers={"Origin": TAILOR})
    assert made.status_code == 200, made.text
    cid = client.get("/api/workspace").json()["candidate_id"]
    resumes = client.get(f"/api/candidates/{cid}/resumes").json()
    assert [r["id"] for r in resumes] == ["career_agent_profile"] and resumes[0]["default"]
    body = client.get(f"/api/candidates/{cid}/resumes/career_agent_profile").json()
    bullets = [b["text"] for p in body["positions"] for b in p["bullets"]]
    assert sorted(bullets) == sorted(" ".join(t.split()) for t in confirmed)
    assert body["source_file"] == "career_agent"


def test_no_career_profile_means_a_clear_refusal(two) -> None:
    (_, _, client), _ = two
    response = client.post("/api/career/base-resume", json={}, headers={"Origin": TAILOR})
    assert response.status_code == 400
    assert "no confirmed experience" in response.json()["detail"]


# -------------------------------------------------------------- attachment


def test_a_tailored_resume_attaches_to_the_canonical_posting(two) -> None:
    (_, api, client), _ = two
    _career_profile(api)
    client.post("/api/career/base-resume", json={}, headers={"Origin": TAILOR})
    cid = client.get("/api/workspace").json()["candidate_id"]
    job_id = _jobs(api)[0]
    api.handle_api("PATCH", f"/api/jobs/{job_id}/status", {}, {"status": "SHORTLISTED"})
    job = client.get(f"/api/career/jobs/{job_id}").json()
    started = client.post(
        f"/api/candidates/{cid}/tailor",
        json={"jd_text": job["description"], "career_job_id": job_id},
        headers={"Origin": TAILOR},
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["application_id"]
    ends = time.monotonic() + 60
    while time.monotonic() < ends:
        state = client.get(f"/api/candidates/{cid}/applications/{run_id}").json()["status"]
        if state["status"] in {"done", "error"}:
            break
        time.sleep(0.2)
    assert state["status"] == "done", state
    row = next(
        j for j in client.get("/api/career/applications").json()["jobs"] if j["job_id"] == job_id
    )
    assert [t["id"] for t in row["tailored"]] == [run_id]
    listed = client.get(f"/api/candidates/{cid}/applications").json()
    assert listed[0]["career_job_id"] == job_id
    assert _status(api, job_id) == "SHORTLISTED", "tailoring never changes the status"


def test_tailoring_without_a_base_resume_says_what_to_do(two) -> None:
    (_, api, client), _ = two
    cid = client.get("/api/workspace").json()["candidate_id"]
    response = client.post(
        f"/api/candidates/{cid}/tailor",
        json={"jd_text": "A synthetic posting " * 10},
        headers={"Origin": TAILOR},
    )
    assert response.status_code == 400
    assert "no base resume yet" in response.json()["detail"]


# ------------------------------------------------------------------ uploads


def _docx(text: str) -> bytes:
    from docx import Document

    doc = Document()
    for line in text.splitlines():
        doc.add_paragraph(line)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _pdf(text: str) -> bytes:
    """A one-page PDF with real text, built by hand (no extra dependency)."""
    lines = text.splitlines()
    stream = (
        "BT /F1 11 Tf 72 740 Td 14 TL "
        + " ".join(f"({line.replace('(', '').replace(')', '')}) Tj T*" for line in lines)
        + " ET"
    )
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
        " /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body = b"%PDF-1.4\n"
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n{obj}\nendobj\n".encode("latin-1")
    xref = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode()
    body += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return body


SYNTHETIC_RESUME = (
    "Riley Synthetic\nIntegration Specialist\n\nSkills\nPython, SQL, REST APIs\n\n"
    "Experience\nIntegration Specialist, Example Systems, 2021 - 2024\n"
    "- Built a synthetic data pipeline for an invented team\n"
)


@pytest.mark.parametrize(
    ("name", "kind", "make"),
    [
        ("riley.pdf", "application/pdf", _pdf),
        (
            "riley.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _docx,
        ),
        ("riley.md", "text/markdown", lambda text: text.encode("utf-8")),
        ("riley.md", "application/octet-stream", lambda text: text.encode("utf-8")),
    ],
)
def test_pdf_docx_and_markdown_upload_to_the_active_profile(two, name, kind, make) -> None:
    (_, _, client), (_, _, other) = two
    cid = client.get("/api/workspace").json()["candidate_id"]
    response = client.post(
        f"/api/candidates/{cid}/resumes/upload",
        files={"file": (name, make(SYNTHETIC_RESUME), kind)},
        headers={"Origin": TAILOR},
    )
    assert response.status_code == 200, response.text
    assert response.json()["extracted"]["skills"] >= 1
    assert len(client.get(f"/api/candidates/{cid}/resumes").json()) == 1
    other_cid = other.get("/api/workspace").json()["candidate_id"]
    assert other.get(f"/api/candidates/{other_cid}/resumes").json() == [], "crossed profiles"


@pytest.mark.parametrize(
    ("name", "kind", "data", "said"),
    [
        ("riley.exe", "application/octet-stream", b"MZ", "PDF, Word (.docx) or Markdown"),
        ("riley.pdf", "application/pdf", b"not a pdf at all", "not really a .pdf"),
        ("riley.docx", "text/html", b"PK\x03\x04", "says it is text/html"),
        ("riley.md", "text/markdown", b"\xff\xfe\x00\x00binary", "not a text file"),
        ("riley.md", "text/markdown", b"", "is empty"),
        ("riley.docx", "application/octet-stream", b"PK\x03\x04broken", "could not be read"),
    ],
)
def test_a_bad_upload_is_refused_in_words(two, name, kind, data, said) -> None:
    (_, _, client), _ = two
    cid = client.get("/api/workspace").json()["candidate_id"]
    response = client.post(
        f"/api/candidates/{cid}/resumes/upload",
        files={"file": (name, data, kind)},
        headers={"Origin": TAILOR},
    )
    assert response.status_code == 400, response.text
    assert said in response.json()["detail"]
    assert client.get(f"/api/candidates/{cid}/resumes").json() == []


def test_standalone_tailor_has_no_career_routes(tmp_path: Path) -> None:
    from resume_tailor.api.app import create_app

    client = TestClient(create_app(home=tmp_path / "home"), base_url=TAILOR)
    assert client.get("/api/workspace").json() == {"mode": "standalone"}
    assert client.get("/api/career/applications").status_code == 404


def test_the_redirect_forwards_only_a_valid_posting_id_and_the_profile(tmp_path: Path) -> None:
    import http.client
    import threading
    import types

    from career_agent.web.server import build_server

    profile, api, _ = _profile(tmp_path, "prof-01SYNTHETICDDDDDDDDDDDDDD", "Synthetic D")
    api.profile_host = types.SimpleNamespace(active=profile)
    httpd = build_server(api)
    port = int(httpd.server_address[1])
    import dataclasses

    api.config = dataclasses.replace(api.config, port=port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:

        def location(path: str) -> str:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", path)
            response = conn.getresponse()
            assert response.status == 302
            conn.close()
            return str(response.getheader("Location"))

        job_id = _jobs(api)[0]
        assert location(f"/resume-tailor?job={job_id}") == (
            f"http://127.0.0.1:{port + 1}/?job={job_id}&profile={profile.id}"
        )
        # Anything that is not a posting id is dropped, never forwarded.
        assert location("/resume-tailor?job=<script>") == f"http://127.0.0.1:{port + 1}/"
        assert location("/resume-tailor") == f"http://127.0.0.1:{port + 1}/"
    finally:
        httpd.shutdown()
        httpd.server_close()
