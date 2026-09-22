"""One URL, one process. A stale server may never impersonate a new one.

WHAT HAPPENED
-------------
2026-09-08. The owner started Career Agent and read this:

    database      : data\\m1d2\\career.db (schema 25)
    configuration : config\\search.local.yaml (v4)
    jobs          : 19485
    scored        : 19469

Then the browser answered 404 on routes that exist, 500 on routes that work,
and a Jobs list saying nothing was scored. Every line of that banner was true.
It was true about a process the browser was not talking to: a Career Agent
started the previous day still owned port 8765.

`SO_REUSEADDR` is why, and it is worth stating precisely because the name
suggests the opposite of what it does here. On POSIX it permits reusing an
address left in `TIME_WAIT` by a socket that has already closed. On Windows it
permits binding an address **another process is actively serving**.
`socketserver.TCPServer` sets it by default, so the product inherited the
dangerous meaning without ever choosing it.

Measured before the fix, on this machine: two `ThreadingHTTPServer` instances
bound the same loopback port, the second raised nothing whatsoever, and every
request afterwards was answered by the FIRST.

THE CONTRACT
------------
Bind first, announce second. If the address cannot be served exclusively, the
start fails loudly and prints no banner. And a running server says which
process it is, so the terminal and the browser can be compared by anyone.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

from career_agent.runtime.fingerprint import STARTED_AT, runtime_fingerprint
from career_agent.storage.db import connect, migrate
from career_agent.web.api import JobsApi
from career_agent.web.server import ExclusiveHTTPServer, PortInUse, ServerConfig, build_server

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A database and a configuration of its very own.

    Never the owner's. This file starts and stops servers, and a test that
    reached her database or her `search.local.yaml` to do it would be exactly
    the class of accident the rest of this session is closing.
    """
    import shutil

    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(ROOT / "config" / "search.worked-example.yaml", config)
    shutil.copy(config / "search.worked-example.yaml", config / "search.local.yaml")
    shutil.copy(ROOT / "config" / "places.yaml", config)
    conn = connect(tmp_path / "career.db")
    migrate(conn)
    # Stamped, because `serve_command` refuses a database that does not say
    # what it is -- which is the guard that stops the demo corpus being served
    # to somebody who asked for their own.
    from career_agent.runtime.mode import RuntimeMode, stamp_identity

    stamp_identity(conn, RuntimeMode.PERSONAL, label="lifecycle test")
    conn.close()
    return tmp_path


def api_for(workspace: Path, port: int) -> JobsApi:
    return JobsApi(
        ServerConfig(db_path=workspace / "career.db", config_dir=workspace / "config", port=port),
        quiet=True,
    )


@pytest.fixture
def running(workspace: Path) -> Iterator[tuple[int, ExclusiveHTTPServer]]:
    port = free_port()
    httpd = build_server(api_for(workspace, port))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port, httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


# =========================================================================
# 1. THE PORT BELONGS TO ONE PROCESS
# =========================================================================


def test_the_first_bind_succeeds_and_answers(running) -> None:
    port, _ = running

    with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=30) as response:
        assert response.status == 200


def test_a_second_server_on_the_same_port_is_refused(running, workspace: Path) -> None:
    """**The defect, as an assertion.**

    Before `ExclusiveHTTPServer` this raised nothing on Windows and the second
    server silently became a ghost: bound, apparently healthy, answering
    nothing, while the first kept serving every request.
    """
    port, _ = running

    with pytest.raises(PortInUse) as refused:
        build_server(api_for(workspace, port))

    assert refused.value.port == port
    assert str(port) in str(refused.value)


def test_the_refusal_says_which_address_it_is_about(running, workspace: Path) -> None:
    """A message a person can act on names the thing they must go and find."""
    port, _ = running

    with pytest.raises(PortInUse) as refused:
        build_server(api_for(workspace, port))

    message = str(refused.value)
    assert "127.0.0.1" in message
    assert "already being served" in message


def test_the_platform_flag_is_the_right_one_for_the_platform() -> None:
    """`SO_REUSEADDR` and `SO_EXCLUSIVEADDRUSE` are mutually exclusive answers.

    Windows has the exclusive flag and must not also ask to share. POSIX has
    no such flag and genuinely wants `SO_REUSEADDR`, so that restarting after
    Ctrl+C is not blocked by `TIME_WAIT`.
    """
    windows = hasattr(socket, "SO_EXCLUSIVEADDRUSE")

    assert ExclusiveHTTPServer.allow_reuse_address is not windows


# =========================================================================
# 2. SHUTTING DOWN GIVES THE PORT BACK
# =========================================================================


def test_shutdown_releases_the_port_for_an_immediate_restart(workspace: Path) -> None:
    """Ctrl+C must not cost a wait, or the next start looks like the bug above.

    This is the reason the exclusive flag cannot simply be "never reuse": a
    restart that failed for thirty seconds after every stop would train the
    person to run two copies, which is where this whole story began.
    """
    port = free_port()
    first = build_server(api_for(workspace, port))
    thread = threading.Thread(target=first.serve_forever, daemon=True)
    thread.start()
    with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=30) as response:
        assert response.status == 200
    first.shutdown()
    first.server_close()
    thread.join(timeout=10)

    second = build_server(api_for(workspace, port))
    try:
        again = threading.Thread(target=second.serve_forever, daemon=True)
        again.start()
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=30) as response:
            assert response.status == 200
    finally:
        second.shutdown()
        second.server_close()
        again.join(timeout=10)


def test_a_closed_server_stops_answering(workspace: Path) -> None:
    """Otherwise "I stopped it" and "it stopped" are different facts."""
    port = free_port()
    httpd = build_server(api_for(workspace, port))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=30) as response:
        assert response.status == 200

    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=10)
    time.sleep(0.2)

    with pytest.raises((URLError, HTTPError, OSError)):
        urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5)


# =========================================================================
# 3. THE RUNTIME SAYS WHICH RUNTIME IT IS
# =========================================================================


def test_health_reports_the_process_answering_it(running) -> None:
    """The browser's answer must be comparable with the terminal's."""
    import json
    import os

    port, _ = running
    with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=30) as response:
        payload = json.loads(response.read())

    process = payload["process"]
    assert process["pid"] == os.getpid()
    assert process["started_at"] == STARTED_AT
    assert process == runtime_fingerprint()


def test_the_fingerprint_carries_no_path_and_no_preference() -> None:
    """It is rendered on a page that gets screenshotted for a portfolio.

    An absolute path on Windows carries the account holder's own name, and
    `docs/PRIVACY.md` is a promise about exactly this.
    """
    fingerprint = runtime_fingerprint()

    assert set(fingerprint) == {"pid", "started_at", "revision"}
    rendered = " ".join(str(value) for value in fingerprint.values())
    for forbidden in ("/", "\\", "Users", "AI-Workspace", "search.local"):
        assert forbidden not in rendered


def test_two_workspaces_are_not_mistaken_for_one_another(workspace: Path, tmp_path: Path) -> None:
    """Two servers, two databases, two ports, and each says which it opened.

    The stale-process failure was survivable only because the two runtimes
    looked identical from the browser. They must not.
    """
    import json
    import shutil

    other = tmp_path / "second"
    other.mkdir()
    (other / "config").mkdir()
    shutil.copy(
        ROOT / "config" / "search.worked-example.yaml", other / "config" / "search.local.yaml"
    )
    shutil.copy(ROOT / "config" / "places.yaml", other / "config")
    conn = connect(other / "different.db")
    migrate(conn)
    conn.close()

    servers = []
    try:
        for root, name in ((workspace, "career.db"), (other, "different.db")):
            port = free_port()
            app = JobsApi(
                ServerConfig(db_path=root / name, config_dir=root / "config", port=port),
                quiet=True,
            )
            httpd = build_server(app)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            servers.append((port, httpd))

        names = []
        for port, _ in servers:
            with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=30) as response:
                names.append(json.loads(response.read())["db_name"])
    finally:
        for _, httpd in servers:
            httpd.shutdown()
            httpd.server_close()

    assert names == ["career.db", "different.db"]


# =========================================================================
# 4. A FAILED START ANNOUNCES NOTHING
# =========================================================================


def test_a_refused_start_prints_no_address(running, workspace: Path, capsys) -> None:
    """**The line that cost the morning.**

    `career-agent start` used to print its preamble -- database, counts, and
    `address: http://127.0.0.1:8765` -- BEFORE the socket was bound. A second
    copy therefore announced a URL it did not own, and the older process from
    the previous day answered everything sent to it.

    An address is a claim about a socket. It may not be printed by a process
    that has not got one.
    """
    import typer

    from career_agent.cli_local import serve_command

    port, _ = running
    with pytest.raises(typer.Exit) as exit_code:
        serve_command(
            db=workspace / "career.db",
            config_dir=workspace / "config",
            port=port,
            open_browser=False,
        )

    assert exit_code.value.exit_code == 2
    printed = capsys.readouterr()
    combined = printed.out + printed.err
    assert "already being served" in combined
    assert f"http://127.0.0.1:{port}/" not in combined, "it announced a URL it does not own"


def test_the_preamble_of_start_carries_no_url() -> None:
    """The other half, asserted structurally.

    `start` reads the database and prints what it found before it ever asks
    for a socket. Whatever else that block says, it may not say where to go.
    """
    import inspect

    from career_agent import cli_local

    source = inspect.getsource(cli_local.start_command)
    preamble = source[: source.index("serve_command(")]
    # Comments are prose about the defect and say the URL out loud. What is
    # asserted is what the command PRINTS.
    code = " ".join(line for line in preamble.splitlines() if not line.strip().startswith("#"))

    assert "http://127.0.0.1" not in code, "start announces an address before binding"
