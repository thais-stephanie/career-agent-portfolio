"""The Resume helper is retired (Resume Workspace V2 PR 12), its resumes are not.

* the real launcher, in a temporary installation that looks like v0.2.0-beta.2
  with a Resume helper workspace: ONE server process, nothing on the next
  port, nothing moved until asked, a verified backup, an idempotent move, the
  old files byte for byte as they were, a clean quit and a clean restart;
* in process: a migrated export downloads again without the old server, the
  move is per profile, a partial move completes on retry without duplicates,
  "forget" never touches the old files, an application keeps its resume, and
  the old helper's address opens Resumes.

Synthetic data only (`tests/fixtures/legacy_resume_helper`); no real profile
is read, no model is called, nothing leaves the machine.
"""

from __future__ import annotations

import http.client
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

import pytest
from tests.support import career_profile, committed_config_dir
from tests.support_legacy import FACTS, LABEL, profile, workspace_copy

from career_agent.resume_doc.legacy import PROFILE_KEY, _manifest
from career_agent.resume_doc.store import ResumeStore, resume_row_counts
from career_agent.runtime.profiles import ensure_registry
from career_agent.storage.db import connect
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig, build_server

REPO = Path(__file__).resolve().parents[2]


def _free_pair() -> int:
    """A port whose neighbour is free too, so a second server COULD bind it."""
    for _ in range(50):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        if port < 65535 and _free(port) and _free(port + 1):
            return port
    raise RuntimeError("no free pair of ports")


def _free(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


class Client:
    def __init__(self, port: int) -> None:
        self.port = port

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        headers = {"Host": f"127.0.0.1:{self.port}"}
        payload = None
        if method != "GET":
            headers |= {
                "Origin": f"http://127.0.0.1:{self.port}",
                "Content-Type": "application/json",
            }
            payload = json.dumps(body if body is not None else {})
        try:
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
        finally:
            conn.close()
        kind = response.getheader("Content-Type") or ""
        return response.status, (json.loads(raw) if "json" in kind and raw else None), raw

    def json(self, method: str, path: str, body: Any = None) -> Any:
        status, data, raw = self.call(method, path, body)
        assert status == 200, (method, path, status, raw[:300])
        return data


# -------------------------------------------------------- a Beta 2 install


@pytest.fixture
def install(tmp_path: Path) -> dict[str, Any]:
    """A temporary installation as v0.2.0-beta.2 left it: the first profile
    adopted where it was, its Resume helper workspace in its Tailor home,
    stamped by the old bridge with the profile's id."""
    root = tmp_path / "Career-Agent"
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(REPO / "scripts" / "launch.py", root / "scripts" / "launch.py")
    shutil.copytree(committed_config_dir(), root / "config")
    # data/personal.db, with the statements the old workspace cites confirmed.
    profile(root, "data").close()
    active = ensure_registry(root).current
    home = root / active.tailor_home / "candidates"
    workspace = workspace_copy(tmp_path / "frozen")
    target = home / active.id.lower()
    shutil.copytree(workspace, target)
    meta = json.loads((target / "candidate.json").read_text("utf-8"))
    meta[PROFILE_KEY] = active.id
    (target / "candidate.json").write_text(json.dumps(meta), encoding="utf-8")
    return {"root": root, "workspace": target, "profile": active}


def _launch(root: Path, port: int) -> subprocess.Popen[str]:
    process = subprocess.Popen(  # noqa: S603 -- this repository's own launcher
        [sys.executable, str(root / "scripts" / "launch.py"), "--no-open", "--port", str(port)],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"the launcher ended early: {process.stdout.read()}")  # type: ignore[union-attr]
        if _listening(port):
            try:
                if Client(port).call("GET", "/api/app")[0] == 200:
                    return process
            except OSError:
                pass
        time.sleep(0.3)
    process.kill()
    raise AssertionError("the launcher did not answer in time")


def _quit(process: subprocess.Popen[str], port: int) -> str:
    assert Client(port).json("POST", "/api/app/quit")["stopping"] is True
    try:
        out, _ = process.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        raise
    assert process.returncode == 0, out
    assert not _listening(port), "the server still answers after Quit"
    return out


def _kill_tree(process: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603, S607
            ["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False
        )
    else:
        process.kill()
    process.communicate(timeout=30)


def _tree(pid: int) -> tuple[list[str], set[int]]:
    """Windows: the command lines of every process under `pid` (itself
    included) and the TCP ports they listen on."""
    script = (
        "$all = Get-CimInstance Win32_Process"
        " | Select-Object ProcessId,ParentProcessId,CommandLine;"
        "$listen = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue"
        " | Select-Object OwningProcess,LocalPort;"
        "@{p=$all; l=$listen} | ConvertTo-Json -Depth 3 -Compress"
    )
    raw = subprocess.run(  # noqa: S603, S607
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout
    data = json.loads(raw)
    procs = data["p"] or []
    pids, grew = {pid}, True
    while grew:
        more = {p["ProcessId"] for p in procs if p["ParentProcessId"] in pids} - pids
        grew = bool(more)
        pids |= more
    lines = [str(p.get("CommandLine") or "") for p in procs if p["ProcessId"] in pids]
    listen = data["l"] or []
    listen = listen if isinstance(listen, list) else [listen]
    return lines, {int(c["LocalPort"]) for c in listen if c["OwningProcess"] in pids}


def test_a_beta2_installation_upgrades_with_one_server_and_its_old_resumes_move_once(
    install: dict[str, Any],
) -> None:
    root, workspace = install["root"], install["workspace"]
    before = _manifest(workspace)
    port = _free_pair()
    process = _launch(root, port)
    try:
        # ONE runtime: nothing answers on the port the companion used, and on
        # Windows no process under the launcher is the old engine or listens
        # anywhere but on Career Agent's own port.
        assert not _listening(port + 1), "something answers on the former companion port"
        if sys.platform == "win32":
            lines, ports = _tree(process.pid)
            joined = "\n".join(lines).casefold()
            for name in ("resume_tailor", "resume-tailor", "uvicorn"):
                assert name not in joined, name
            assert ports == {port}, ports
        client = Client(port)
        found = client.json("GET", "/api/resume/legacy")
        assert found["state"] == "FOUND" and found["job_versions"] == 3
        # Looking moved nothing, and the old helper's routes are gone.
        assert client.json("GET", "/api/resume/documents") == {
            "master": None,
            "others": [],
            "jobs": [],
        }
        assert client.call("GET", "/rt/api/workspace")[0] == 404
        moved = client.json("POST", "/api/resume/legacy/migrate")
        assert (moved["masters"], moved["job_versions"], moved["exports"]) == (1, 3, 1)
        assert moved["failed"] == 2 and {f["kind"] for f in moved["failures"]} == {"run", "export"}
        backups = list((root / "data" / "resume_helper_backups").glob("*.zip"))
        assert len(backups) == 1
        again = client.json("POST", "/api/resume/legacy/migrate")
        assert again["masters"] == again["job_versions"] == 0 and again["already"] >= 4
        assert _manifest(workspace) == before, "an old file was changed"
        library = client.json("GET", "/api/resume/documents")
        assert library["master"] is not None and len(library["jobs"]) == 2
        # A moved version opens, edits and exports as any other.
        version = library["jobs"][0]["versions"][0]["id"]
        doc = client.json("GET", f"/api/resume/documents/{version}")
        saved = client.json(
            "PATCH",
            f"/api/resume/documents/{version}/working",
            {"document": {**doc["document"], "title": "Edited after the move"},
             "expected_sha256": doc["sha256"]},
        )  # fmt: skip
        made = client.json(
            "POST",
            f"/api/resume/documents/{version}/exports",
            {"format": "JSON", "expected_sha256": saved["sha256"]},
        )
        assert made["format"] == "JSON"
        # The old helper's own download comes back from its folder.
        olds = [
            e
            for d in library["jobs"]
            for v in d["versions"]
            for e in client.json("GET", f"/api/resume/documents/{v['id']}/exports")
            if e["engine"] == "resume_tailor_legacy"
        ]
        assert len(olds) == 1 and olds[0]["available"] is True
        status, _, body = client.call("GET", f"/api/resume/exports/{olds[0]['id']}/file")
        assert status == 200 and body.startswith(b"PK synthetic docx")
        out = _quit(process, port)
    except BaseException:
        if process.poll() is None:
            _kill_tree(process)
        raise
    # The console names no resume content.
    for private in ("Riley", "riley@example.invalid", "Northwind", FACTS["claims"][0]["text"]):
        assert private not in out, private
    # A restart: the same profile, everything where it was, nothing to move.
    process = _launch(root, port)
    try:
        client = Client(port)
        after = client.json("GET", "/api/resume/legacy")
        assert after["state"] == "MOVED" and after["remaining"] == 0
        assert client.json("GET", "/api/resume/documents")["master"] is not None
        _quit(process, port)
    except BaseException:
        if process.poll() is None:
            _kill_tree(process)
        raise
    assert _manifest(workspace) == before


# ---------------------------------------------------------- in process


def _call(api: JobsApi, method: str, path: str, body: Any = None) -> Any:
    return api.handle_api(method, f"/api/resume{path}", {}, body or {})


def _with_workspace(tmp_path: Path, pid: str, label: str, *, legacy: bool) -> tuple[Any, JobsApi]:
    """A profile served as the launcher serves it, with (or without) a
    Resume helper workspace in its own Tailor home."""
    found, api = career_profile(tmp_path, pid, label)
    if legacy:
        workspace_copy(tmp_path / f"{pid}-frozen")
        shutil.copytree(
            tmp_path / f"{pid}-frozen" / "candidates",
            Path(found.tailor_home) / "candidates",
        )
        (Path(found.tailor_home) / "candidates" / FACTS["profile_id"].lower()).rename(
            Path(found.tailor_home) / "candidates" / pid.lower()
        )
    api.profile_host = types.SimpleNamespace(root=tmp_path, active=found)  # type: ignore[attr-defined]
    return found, api


def test_each_profile_sees_and_moves_only_its_own_old_resumes(tmp_path: Path) -> None:
    a, api_a = _with_workspace(tmp_path, "prof-01SYNTHETICRETIREAAAAAAAA", LABEL, legacy=True)
    _, api_b = _with_workspace(tmp_path, "prof-01SYNTHETICRETIREBBBBBBBB", "Other", legacy=False)
    assert _call(api_b, "GET", "/legacy") == {"state": "NONE"}
    with pytest.raises(ApiError) as nothing:
        _call(api_b, "POST", "/legacy/migrate")
    assert nothing.value.status == 404
    assert _call(api_a, "GET", "/legacy")["state"] == "FOUND"
    _call(api_a, "POST", "/legacy/migrate")
    with connect(api_b.config.db_path) as conn:
        assert set(resume_row_counts(conn).values()) == {0}, "B received A's resumes"
    assert not list(api_b.config.db_path.parent.glob("resume_helper_backups/*.zip"))
    assert list(api_a.config.db_path.parent.glob("resume_helper_backups/*.zip"))
    assert _call(api_b, "GET", "/legacy") == {"state": "NONE"}
    # A's moved export is served to A only: B has no such row.
    with connect(api_a.config.db_path) as conn:
        (export_id,) = conn.execute(
            "SELECT id FROM resume_export WHERE engine = 'resume_tailor_legacy'"
        ).fetchone()
    with pytest.raises(ApiError) as other:
        _call(api_b, "GET", f"/exports/{export_id}/file")
    assert other.value.status == 404
    assert a.id.lower() in str(Path(a.tailor_home) / "candidates" / a.id.lower())


def test_a_moved_download_is_served_only_while_it_is_the_recorded_file(tmp_path: Path) -> None:
    found, api = _with_workspace(tmp_path, "prof-01SYNTHETICDOWNLOADAAAAAA", LABEL, legacy=True)
    _call(api, "POST", "/legacy/migrate")
    with connect(api.config.db_path) as conn:
        row = conn.execute(
            "SELECT id, file_path FROM resume_export WHERE engine = 'resume_tailor_legacy'"
        ).fetchone()
    export_id, path = row[0], Path(row[1])
    assert _call(api, "GET", f"/exports/{export_id}/file").body.startswith(b"PK synthetic")
    original = path.read_bytes()
    path.write_bytes(original + b" changed")
    with pytest.raises(ApiError) as changed:
        _call(api, "GET", f"/exports/{export_id}/file")
    assert changed.value.status == 404, "a file that is no longer the recorded one is not served"
    path.write_bytes(original)
    # A recorded path outside the old helper's own exports folder is never read.
    with connect(api.config.db_path) as conn:
        outside = tmp_path / "outside.docx"
        outside.write_bytes(original)
        conn.execute(
            "UPDATE resume_export SET file_path = ? WHERE id = ?", (str(outside), export_id)
        )
        conn.commit()
    with pytest.raises(ApiError) as escaped:
        _call(api, "GET", f"/exports/{export_id}/file")
    assert escaped.value.status == 404
    assert found is not None


def test_a_partial_move_completes_on_retry_without_duplicating_anything(tmp_path: Path) -> None:
    found, api = _with_workspace(tmp_path, "prof-01SYNTHETICPARTIALAAAAAAA", LABEL, legacy=True)
    root = Path(found.tailor_home) / "candidates" / found.id.lower()
    run = root / "applications" / "20260102T000000-bbbbbb" / "run.json"
    original = run.read_bytes()
    data = json.loads(original)
    data["generated_resume"]["unknown_future_field"] = True
    run.write_text(json.dumps(data), encoding="utf-8")
    first = _call(api, "POST", "/legacy/migrate")
    # The unreadable run, the corrupt one and the file both job versions name
    # stay: a run that could not move never makes the other its file's owner.
    assert first["job_versions"] == 2 and first["failed"] == 3
    assert {"kind": "run", "name": "20260102T000000-bbbbbb"} in first["failures"]
    left = _call(api, "GET", "/legacy")
    assert left["state"] == "MOVED" and left["remaining"] == 1
    # The moved resumes are usable while one is left behind.
    library = _call(api, "GET", "/documents")
    assert library["master"] is not None
    run.write_bytes(original)
    before = _manifest(root)
    second = _call(api, "POST", "/legacy/migrate")
    assert second["job_versions"] == 1 and second["masters"] == 0
    assert _call(api, "GET", "/legacy")["remaining"] == 0
    with connect(api.config.db_path) as conn:
        counts = resume_row_counts(conn)
    third = _call(api, "POST", "/legacy/migrate")
    assert third["job_versions"] == 0
    with connect(api.config.db_path) as conn:
        assert resume_row_counts(conn) == counts, "a retry duplicated something"
    assert _manifest(root) == before


def test_forgetting_everything_never_touches_the_old_helpers_files(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from career_agent.cli import app

    found, api = _with_workspace(tmp_path, "prof-01SYNTHETICFORGETOLDAAAAA", LABEL, legacy=True)
    root = Path(found.tailor_home) / "candidates" / found.id.lower()
    _call(api, "POST", "/legacy/migrate")
    before = _manifest(root)
    result = CliRunner().invoke(
        app,
        ["forget", "everything", "--yes", "--db", str(api.config.db_path),
         "--config-dir", str(api.config.config_dir)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert _manifest(root) == before, "forget removed or changed an old helper file"
    with connect(api.config.db_path) as conn:
        assert set(resume_row_counts(conn).values()) == {0}
    # The files are still there to move again.
    assert _call(api, "GET", "/legacy")["state"] == "FOUND"


def test_an_application_keeps_the_moved_resume_it_used(tmp_path: Path) -> None:
    _, api = _with_workspace(tmp_path, "prof-01SYNTHETICUSEDAAAAAAAAAA", LABEL, legacy=True)
    _call(api, "POST", "/legacy/migrate")
    with connect(api.config.db_path) as conn:
        job = conn.execute("SELECT id FROM job ORDER BY id LIMIT 1").fetchone()[0]
        version = next(d for d in ResumeStore(conn).list_documents() if d.kind == "TAILORED")
    _call(api, "POST", f"/jobs/{job}/used", {"document_id": version.id})
    used = _call(api, "GET", f"/jobs/{job}")["used"]
    assert used["document_id"] == version.id
    _call(api, "POST", "/legacy/migrate")  # a retry changes nothing it points at
    assert _call(api, "GET", f"/jobs/{job}")["used"] == used


def test_the_old_helpers_address_opens_resumes_here(tmp_path: Path) -> None:
    api = JobsApi(
        ServerConfig(db_path=_db(tmp_path), config_dir=committed_config_dir(), port=0),
        quiet=True,
    )
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

        assert location("/resume-tailor?job=abc-123") == "/?resume_job=abc-123#resume"
        assert location("/resume-tailor?job=<script>") == "/#resume"
        assert location("/resume-tailor") == "/#resume"
        # The old engine's API is simply gone: no forwarding, no second server.
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/rt/api/workspace")
        assert conn.getresponse().status == 404
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _db(tmp_path: Path) -> Path:
    conn = profile(tmp_path, "redirect")
    path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    return path
