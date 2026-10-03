"""The Resume helper is a page of Career Agent, reached on Career Agent's origin.

Career Agent's own server and the Resume Tailor engine run here exactly as the
launcher runs them: the engine on the next port, following one synthetic local
profile through the bridge. The page never talks to that port; it asks
`/rt/api/...` on Career Agent's origin and the server forwards it.

* the profile is the profile: the workspace answers with the active profile,
  and a page that names another one is refused;
* a cross-origin page is refused before anything is forwarded, and an upload
  needs a page of this app;
* a resume made for a Career Agent job remembers that job (step 3 of Before
  you apply), and its Word file downloads through the same origin;
* a missing engine is a sentence for the reader, never a stack trace.

Synthetic data only.
"""

from __future__ import annotations

import dataclasses
import http.client
import json
import socket
import threading
import time
import types
from pathlib import Path

import pytest
import uvicorn
from tests.integration.test_tailor_bridge import SYNTHETIC_RESUME, _career_profile, _jobs, _profile

from career_agent.web.server import build_server
from career_agent.web.tailor_bridge import tailor_app


def _free_pair() -> int:
    """A port whose neighbour is free too (the engine takes port + 1)."""
    for _ in range(50):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
        probe.close()
        other = socket.socket()
        try:
            other.bind(("127.0.0.1", port + 1))
        except OSError:
            continue
        finally:
            other.close()
        return port
    raise RuntimeError("no free port pair")


@pytest.fixture
def app(tmp_path: Path):  # noqa: ANN201
    profile, api, _ = _profile(tmp_path, "prof-01SYNTHETICRHRHRHRHRHRHRH", "Synthetic RH")
    port = _free_pair()
    api.profile_host = types.SimpleNamespace(active=profile)
    api.config = dataclasses.replace(api.config, port=port)
    engine = uvicorn.Server(
        uvicorn.Config(
            tailor_app(tmp_path / "rh-tailor", profile, api),
            host="127.0.0.1",
            port=port + 1,
            log_level="warning",
        )
    )
    engine_thread = threading.Thread(target=engine.run, daemon=True)
    engine_thread.start()
    httpd = build_server(api)
    # build_server binds the configured port.
    assert int(httpd.server_address[1]) == port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ends = time.monotonic() + 20
    while not engine.started and time.monotonic() < ends:
        time.sleep(0.05)
    try:
        yield types.SimpleNamespace(port=port, profile=profile, api=api)
    finally:
        httpd.shutdown()
        httpd.server_close()
        engine.should_exit = True
        engine_thread.join(timeout=10)


def _ask(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    found = {k.lower(): v for k, v in response.getheaders()}
    conn.close()
    return response.status, found, data


def _json(app, method: str, path: str, body: dict | None = None, **extra: str) -> tuple[int, dict]:
    headers = {"X-Local-Profile": app.profile.id, **extra}
    raw = None
    if method != "GET":
        headers["Content-Type"] = "application/json"
        raw = json.dumps(body or {}).encode()
    status, _, data = _ask(app.port, method, f"/rt/api{path}", body=raw, headers=headers)
    return status, (json.loads(data) if data else {})


def test_the_helper_answers_on_career_agents_origin_for_the_active_profile(app) -> None:
    status, workspace = _json(app, "GET", "/workspace")
    assert status == 200, workspace
    assert workspace["mode"] == "profile"
    assert workspace["profile"]["id"] == app.profile.id
    assert workspace["candidate_id"] == app.profile.id.lower()
    # A page drawn for another profile is refused by the engine, through us.
    status, refused = _json(app, "GET", "/career/applications", **{"X-Local-Profile": "prof-other"})
    assert status == 409, refused
    assert refused["detail"]["code"] == "stale_profile"


def test_another_origin_is_refused_before_anything_is_forwarded(app) -> None:
    status, _, data = _ask(
        app.port,
        "GET",
        "/rt/api/workspace",
        headers={"Origin": "http://evil.example", "X-Local-Profile": app.profile.id},
    )
    assert status == 403, data
    status, _, data = _ask(
        app.port,
        "POST",
        "/rt/api/workspace",
        body=b"a=1",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == 415, data


def _multipart(name: str, text: str) -> tuple[bytes, str]:
    boundary = "rhsyntheticboundary"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: text/markdown\r\n\r\n{text}\r\n--{boundary}--\r\n"
    ).encode()
    return body, f"multipart/form-data; boundary={boundary}"


def test_an_upload_needs_a_page_of_this_app(app) -> None:
    cid = _json(app, "GET", "/workspace")[1]["candidate_id"]  # the page's first call
    body, kind = _multipart("riley.md", SYNTHETIC_RESUME)
    path = f"/rt/api/candidates/{cid}/resumes/upload"
    status, _, data = _ask(
        app.port,
        "POST",
        path,
        body=body,
        headers={"Content-Type": kind, "X-Local-Profile": app.profile.id},
    )
    assert status == 403, data
    origin = f"http://127.0.0.1:{app.port}"
    status, _, data = _ask(
        app.port,
        "POST",
        path,
        body=body,
        headers={"Content-Type": kind, "X-Local-Profile": app.profile.id, "Origin": origin},
    )
    assert status == 200, data
    assert json.loads(data)["extracted"]["skills"] >= 1


def test_a_resume_made_for_a_job_remembers_it_and_downloads_here(app) -> None:
    cid = _json(app, "GET", "/workspace")[1]["candidate_id"]  # the page's first call
    # The page's own sequence: the confirmed Career Profile is copied into the
    # engine, and a starting resume is made from it.
    _career_profile(app.api)
    assert _json(app, "POST", "/career/evidence/import")[0] == 200
    assert _json(app, "POST", "/career/base-resume")[0] == 200
    job_id = _jobs(app.api)[0]
    status, job = _json(app, "GET", f"/career/jobs/{job_id}")
    assert status == 200, job
    status, started = _json(
        app,
        "POST",
        f"/candidates/{cid}/tailor",
        {
            "jd_text": job["description"],
            "career_job_id": job_id,
            "options": {"evidence_only_claims": True, "max_two_pages": True, "use_llm": False},
        },
    )
    assert status == 200, started
    run_id = started["application_id"]
    ends = time.monotonic() + 90
    state: dict = {}
    while time.monotonic() < ends:
        state = _json(app, "GET", f"/candidates/{cid}/applications/{run_id}")[1]["status"]
        if state["status"] in {"done", "error"}:
            break
        time.sleep(0.3)
    assert state["status"] == "done", state
    _, listed = _json(app, "GET", f"/candidates/{cid}/applications")
    assert [r["career_job_id"] for r in listed] == [job_id]
    status, headers, data = _ask(
        app.port,
        "GET",
        f"/rt/api/candidates/{cid}/applications/{run_id}/export/docx",
        headers={"X-Local-Profile": app.profile.id},
    )
    assert status == 200
    assert "attachment" in headers["content-disposition"]
    assert data[:2] == b"PK", "a Word file is a zip"


def test_a_missing_engine_is_a_sentence_for_the_reader(tmp_path: Path) -> None:
    profile, api, _ = _profile(tmp_path, "prof-01SYNTHETICNOENGINEAAAAA", "Synthetic N")
    port = _free_pair()
    api.config = dataclasses.replace(api.config, port=port)
    httpd = build_server(api)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, _, data = _ask(port, "GET", "/rt/api/workspace")
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert status == 503
    said = json.loads(data)
    assert said.get("for_reader") is True
    assert "Resume helper" in said["error"]
