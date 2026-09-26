"""Resume Tailor uploads (sources and base resumes) and coded errors.

One validator for both upload paths; content decides identity, names stay
clean; every error a page receives carries a code. Synthetic data only.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_tailor_bridge import SYNTHETIC_RESUME, _docx, _pdf

TAILOR = "http://127.0.0.1"
HEAD = {"Origin": TAILOR}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    from resume_tailor.api.app import create_app

    return TestClient(create_app(home=tmp_path / "home"), base_url=TAILOR)


@pytest.fixture
def cid(client: TestClient) -> str:
    return client.post("/api/candidates", json={"name": "Synthetic Person"}, headers=HEAD).json()[
        "id"
    ]


def _upload(client: TestClient, url: str, name: str, data: bytes, kind: str):  # noqa: ANN202
    return client.post(url, files={"file": (name, data, kind)}, headers=HEAD)


MD = SYNTHETIC_RESUME.encode("utf-8")
OTHER_MD = (SYNTHETIC_RESUME + "- Wrote an invented migration guide\n").encode("utf-8")


# ------------------------------------------------------------------ sources


@pytest.mark.parametrize(
    ("name", "kind", "make"),
    [
        ("notes.pdf", "application/pdf", _pdf),
        (
            "notes.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _docx,
        ),
        ("notes.md", "text/markdown", lambda text: text.encode("utf-8")),
    ],
)
def test_sources_read_pdf_docx_and_markdown(client, cid, name, kind, make) -> None:
    response = _upload(client, f"/api/candidates/{cid}/sources", name, make(SYNTHETIC_RESUME), kind)
    assert response.status_code == 200, response.text
    assert response.json()["already"] is False
    assert [s["name"] for s in client.get(f"/api/candidates/{cid}/sources").json()] == [name]


@pytest.mark.parametrize(
    ("name", "kind", "data", "code"),
    [
        ("fake.pdf", "application/pdf", b"plain text pretending", "not_really"),
        ("fake.docx", "application/octet-stream", b"not a zip file", "not_really"),
        ("tool.exe", "application/octet-stream", b"MZ", "unsupported_format"),
        ("empty.md", "text/markdown", b"", "empty_file"),
        ("binary.md", "text/markdown", b"\x00\x01\x02", "not_text"),
        ("broken.docx", "application/octet-stream", b"PK\x03\x04broken", "unreadable"),
    ],
)
def test_sources_refuse_what_they_cannot_read_server_side(client, cid, name, kind, data, code):
    response = _upload(client, f"/api/candidates/{cid}/sources", name, data, kind)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == code
    assert client.get(f"/api/candidates/{cid}/sources").json() == []


def test_a_source_over_the_size_limit_is_refused(client, cid, monkeypatch) -> None:
    from resume_tailor.importing import upload_check

    monkeypatch.setattr(upload_check, "MAX_DOCUMENT_BYTES", 10)
    response = _upload(client, f"/api/candidates/{cid}/sources", "big.md", MD, "text/markdown")
    assert response.json()["detail"]["code"] == "too_large"


def test_the_same_source_twice_is_already_uploaded(client, cid) -> None:
    first = _upload(client, f"/api/candidates/{cid}/sources", "notes.md", MD, "text/markdown")
    again = _upload(client, f"/api/candidates/{cid}/sources", "copy.md", MD, "text/markdown")
    assert again.status_code == 200 and again.json()["already"] is True
    assert again.json()["id"] == first.json()["id"]
    assert len(client.get(f"/api/candidates/{cid}/sources").json()) == 1


def test_a_removed_source_never_lends_its_id_to_the_next(client, cid) -> None:
    a = _upload(client, f"/api/candidates/{cid}/sources", "a.md", MD, "text/markdown").json()
    b = _upload(client, f"/api/candidates/{cid}/sources", "b.md", OTHER_MD, "text/markdown").json()
    client.delete(f"/api/candidates/{cid}/sources/{a['id']}", headers=HEAD)
    c = _upload(
        client, f"/api/candidates/{cid}/sources", "c.md", MD + b"- more\n", "text/markdown"
    ).json()
    assert len({a["id"], b["id"], c["id"]}) == 3
    ids = [s["id"] for s in client.get(f"/api/candidates/{cid}/sources").json()]
    assert b["id"] in ids and c["id"] in ids


# ------------------------------------------------------------------ resumes


def test_the_same_resume_twice_is_one_resume(client, cid) -> None:
    url = f"/api/candidates/{cid}/resumes/upload"
    first = _upload(client, url, "Resume.md", MD, "text/markdown").json()
    again = _upload(client, url, "Resume.md", MD, "text/markdown").json()
    assert again["already"] is True and again["id"] == first["id"]
    assert again["name"] == "Resume.md"
    assert len(client.get(f"/api/candidates/{cid}/resumes").json()) == 1


def test_same_name_different_content_keeps_both_under_clean_names(client, cid) -> None:
    url = f"/api/candidates/{cid}/resumes/upload"
    one = _upload(client, url, "Resume.md", MD, "text/markdown").json()
    two = _upload(client, url, "Resume.md", OTHER_MD, "text/markdown").json()
    three = _upload(client, url, "Resume.md", OTHER_MD + b"- again\n", "text/markdown").json()
    assert [one["name"], two["name"], three["name"]] == [
        "Resume.md",
        "Resume.md (2)",
        "Resume.md (3)",
    ]
    listed = client.get(f"/api/candidates/{cid}/resumes").json()
    assert sorted(r["name"] for r in listed) == ["Resume.md", "Resume.md (2)", "Resume.md (3)"]
    # The internal id is never the name a person reads.
    assert all(r["id"] != r["name"] for r in listed)


def test_resumes_stored_with_old_suffix_ids_still_open(client, cid, tmp_path) -> None:
    from resume_tailor.core.models import BaseResume

    folder = tmp_path / "home" / "candidates" / cid / "base_resumes"
    for rid in ("resume_3", "resume_4"):
        (folder / f"{rid}.json").write_text(
            BaseResume(id=rid, name="Earlier upload", headline="").model_dump_json(),
            encoding="utf-8",
        )
    listed = {r["id"] for r in client.get(f"/api/candidates/{cid}/resumes").json()}
    assert {"resume_3", "resume_4"} <= listed
    assert client.get(f"/api/candidates/{cid}/resumes/resume_4").status_code == 200
    new = _upload(
        client, f"/api/candidates/{cid}/resumes/upload", "Earlier upload", MD, "text/markdown"
    )
    assert new.json()["detail"]["code"] == "unsupported_format"


# ------------------------------------------------------------------ errors


def test_every_error_carries_a_code(client, cid) -> None:
    missing = client.get("/api/candidates/nobody-here/resumes")
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "not_found"
    unknown_resume = client.get(f"/api/candidates/{cid}/resumes/nope")
    assert unknown_resume.json()["detail"]["code"] == "not_found"
    invalid = client.post(f"/api/candidates/{cid}/tailor", json={}, headers=HEAD)
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_request"
    assert "loc" not in str(invalid.json()), "validation internals stay in the log"
    no_base = client.post(
        f"/api/candidates/{cid}/tailor", json={"jd_text": "A synthetic posting " * 10}, headers=HEAD
    )
    assert no_base.json()["detail"]["code"] == "no_base_resume"


def test_an_unexpected_failure_is_generic_and_leaks_nothing(tmp_path, monkeypatch) -> None:
    from resume_tailor.api.app import create_app
    from resume_tailor.workspace import store as store_module

    def explode(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        raise RuntimeError(r"secret failure at C:\private\path\file.json")

    client = TestClient(
        create_app(home=tmp_path / "home"), base_url=TAILOR, raise_server_exceptions=False
    )
    monkeypatch.setattr(store_module.WorkspaceStore, "list_candidates", explode)
    response = client.get("/api/candidates")
    assert response.status_code == 500
    body = response.json()
    assert body["detail"]["code"] == "internal"
    assert "private" not in str(body) and "Traceback" not in str(body)


def test_the_shared_check_is_the_only_one() -> None:
    """One validator: the routes use `upload_check`, never a copy of it."""
    from resume_tailor.api import material

    source = Path(material.__file__).read_text(encoding="utf-8")
    assert "check_document(" in source
    assert "RESUME_TYPES" not in source and "def check_resume_upload" not in source
    assert io is not None
