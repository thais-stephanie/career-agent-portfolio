"""The preview routes: render a validated document, serve it as its own page.

Synthetic documents in a temporary profile database. The HTTP checks run a
real server, because the policy the preview page is served under is the point.
"""

from __future__ import annotations

import dataclasses
import http.client
import json
import socket
import threading
import types
from pathlib import Path
from typing import Any

import pytest
from tests.support import committed_config_dir
from tests.support_resume import confirm_cited, rich, sparse

from career_agent.resume_doc.store import ResumeStore
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, InlinePage, ServerConfig, build_server


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    db = tmp_path / "personal.db"
    conn = connect(db)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, "Synthetic")
        confirm_cited(conn, rich())
    ResumeStore(conn).create_document(rich())
    conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=committed_config_dir(), port=0), quiet=True)


def render(api: JobsApi, document: Any) -> dict:
    return api.handle_api("POST", "/api/resume/render", {}, {"document": document})


def test_a_render_is_served_once_as_its_own_page(api: JobsApi) -> None:
    doc = sparse().model_dump(mode="json")
    answer = render(api, doc)
    assert answer["page"] == {
        "size": "A4",
        "width_mm": 210.0,
        "height_mm": 297.0,
        "margin_mm": 16.0,
    }
    page = api.handle_api("GET", answer["url"], {}, {})
    assert isinstance(page, InlinePage) and b"Alex Exemplo" in page.body
    assert ResumeStore(connect(api.config.db_path)).list_documents()[0].id == rich().id, (
        "rendering saved nothing"
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"document": "<h1>raw html</h1>"},
        {"document": {"kind": "MASTER"}},
        {"document": {**sparse().model_dump(mode="json"), "schema_version": "9.9"}},
        {"document": sparse().model_dump(mode="json"), "html": "<script></script>"},
    ],
)
def test_only_a_valid_document_is_rendered(api: JobsApi, body: dict) -> None:
    with pytest.raises(ApiError) as refused:
        api.handle_api("POST", "/api/resume/render", {}, body)
    assert refused.value.status == 400


def test_an_unknown_or_other_profiles_render_is_not_served(api: JobsApi) -> None:
    with pytest.raises(ApiError) as missing:
        api.handle_api("GET", "/api/resume/preview/" + "a" * 24, {}, {})
    assert missing.value.status == 404
    api.profile_host = types.SimpleNamespace(active=types.SimpleNamespace(id="prof-A"))
    url = render(api, sparse().model_dump(mode="json"))["url"]
    api.profile_host = types.SimpleNamespace(active=types.SimpleNamespace(id="prof-B"))
    with pytest.raises(ApiError):
        api.handle_api("GET", url, {}, {})


def test_documents_are_listed_and_read(api: JobsApi) -> None:
    listed = api.handle_api("GET", "/api/resume/documents", {}, {})["master"]
    assert listed["id"] == rich().id and listed["kind"] == "MASTER"
    one = api.handle_api("GET", f"/api/resume/documents/{listed['id']}", {}, {})
    assert one["document"]["identity"]["full_name"] == "Morgan Example" and one["sha256"]


def test_the_preview_page_is_sealed(api: JobsApi) -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free = int(probe.getsockname()[1])
    probe.close()
    api.config = dataclasses.replace(api.config, port=free)
    httpd = build_server(api)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        body = json.dumps({"document": rich().model_dump(mode="json")})
        origin = f"http://127.0.0.1:{port}"
        headers = {"Content-Type": "application/json", "Origin": origin}
        conn.request("POST", "/api/resume/render", body, headers)
        url = json.loads(conn.getresponse().read())["url"]
        conn.request("GET", url)
        response = conn.getresponse()
        html = response.read()
        policy = response.getheader("Content-Security-Policy") or ""
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert response.status == 200 and b"Morgan Example" in html
    assert "default-src 'none'" in policy and "frame-ancestors 'self'" in policy
    assert "sandbox" in policy and "allow-scripts" not in policy
    assert response.getheader("Content-Type") == "text/html; charset=utf-8"
    assert "attachment" not in (response.getheader("Content-Disposition") or "")
