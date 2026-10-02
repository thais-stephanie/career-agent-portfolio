"""Open Career Agent from the Windows shortcut, with no console window.

The shortcut that Start-Career-Agent.ps1 makes runs this file with the
installation's own `pythonw.exe`. Standard library only.

* Career Agent from THIS installation already answers on port 8765: open a
  window on it and leave. Nothing else is started.
* Something else answers there: say so in a Windows message. Nothing is
  opened and nothing is stopped.
* Nothing answers: start `scripts/launch.py` hidden, wait until it answers as
  this installation, then open the window. This process then waits for the
  last Career Agent window to close (Microsoft Edge in app mode, with its own
  browser data in data/app-window) and stops both local servers through
  /api/app/quit, as Ctrl+C does in the manual launcher. Without Edge the
  default browser opens a tab, and "Quit Career Agent" in the app stops it.

Everything the servers print goes to data/logs/desktop.log. No AI provider is
called and nothing is collected: this is the same start as the manual launcher.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import winreg
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"
LOG = ROOT / "data" / "logs" / "desktop.log"
WINDOW_DATA = ROOT / "data" / "app-window"
READY_SECONDS = 90
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
SYNCHRONIZE = 0x00100000
#: Never through a proxy: the address is this computer.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

HELP = (
    "More help: FIRST_RUN.md in the Career Agent folder. Details of this start "
    "are in data/logs/desktop.log there."
)
PORT_BUSY = (
    f"Career Agent could not start because the local address it uses (port {PORT} "
    f"or {PORT + 1}) is in use by another program. Close the other program and try "
    "again. Nothing was changed."
)
DEMO_OPEN = (
    "The Career Agent demo is open. Close the demo window first, then open Career Agent again."
)
FAILED = (
    "Career Agent could not start. Nothing you saved was changed. Try again. If it "
    "happens again, double-click Start-Career-Agent.cmd in the Career Agent folder "
    "to see what went wrong."
)
SLOW = (
    "Career Agent took too long to start and was stopped. Nothing you saved was changed. Try again."
)
INCOMPLETE = (
    "Career Agent's setup is not finished. Double-click Start-Career-Agent.cmd in "
    "the Career Agent folder to finish it."
)


def message(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, f"{text}\n\n{HELP}", "Career Agent", 0x10)


def probe() -> dict[str, Any] | str | None:
    """This installation's /api/app answer, None if nothing listens, "other" otherwise."""
    from career_agent import install_id

    try:
        with OPENER.open(URL + "api/app", timeout=3) as response:
            data = json.loads(response.read())
    except urllib.error.URLError as exc:
        return None if isinstance(exc.reason, ConnectionRefusedError) else "other"
    except ConnectionRefusedError:
        return None
    except (OSError, ValueError):
        return "other"
    ours = isinstance(data, dict) and data.get("app") == "career-agent"
    return data if ours and data.get("install") == install_id(ROOT) else "other"


def quit_server() -> None:
    request = urllib.request.Request(
        URL + "api/app/quit",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    # Already stopping, or gone: the caller waits for the process either way.
    with contextlib.suppress(OSError):
        OPENER.open(request, timeout=10).close()


def edge() -> str | None:
    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key) as handle:
                path = winreg.QueryValue(handle, None)
        except OSError:
            continue
        if path and Path(path).is_file():
            return path
    for base in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        folder = os.environ.get(base)
        candidate = Path(folder or ".", "Microsoft", "Edge", "Application", "msedge.exe")
        if folder and candidate.is_file():
            return str(candidate)
    return None


def open_window() -> bool:
    """Open Career Agent. True when it is an Edge app window this process can watch."""
    exe = edge()
    if exe:
        try:
            subprocess.Popen(
                [
                    exe,
                    f"--app={URL}",
                    f"--user-data-dir={WINDOW_DATA}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-sync",
                ],
                creationflags=NO_WINDOW,
                close_fds=True,
            )
            return True
        except OSError:
            pass
    import webbrowser

    webbrowser.open(URL)
    return False


def window_process() -> int | None:
    """The Edge browser process that owns data/app-window, by its command line."""
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object { "
        "$_.CommandLine -and $_.CommandLine -notmatch '--type=' -and "
        "$_.CommandLine.Contains($env:CAREER_AGENT_WINDOW_DATA) } | "
        "Select-Object -First 1 -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=NO_WINDOW,
            env={**os.environ, "CAREER_AGENT_WINDOW_DATA": f"--user-data-dir={WINDOW_DATA}"},
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return int(out) if out.isdigit() else None


def watch_window() -> int | None:
    """A handle on the Edge window process, held so its id cannot be reused under us."""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    for _ in range(20):
        pid = window_process()
        if pid:
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            # Checked again WITH the handle held: the id still names that process.
            if handle and window_process() == pid:
                return int(handle)
            if handle:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
        time.sleep(0.5)
    return None


def wait_for_any(handles: list[int]) -> None:
    array = (ctypes.c_void_p * len(handles))(*handles)
    ctypes.windll.kernel32.WaitForMultipleObjects(len(handles), array, False, 0xFFFFFFFF)


def run() -> int:
    try:
        import career_agent  # noqa: F401  (a finished setup can import the app)
    except ImportError:
        message(INCOMPLETE)
        return 1
    found = probe()
    if found == "other":
        message(PORT_BUSY)
        return 1
    if isinstance(found, dict):
        if found.get("mode") != "PERSONAL":
            message(DEMO_OPEN)
            return 1
        open_window()
        return 0

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w", encoding="utf-8") as log:
        child = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "launch.py"), "--no-open", "--port", str(PORT)],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=NO_WINDOW,
        )
    deadline = time.monotonic() + READY_SECONDS
    while not isinstance(probe(), dict):
        if child.poll() is not None:
            if isinstance(probe(), dict):  # a second click started it first
                open_window()
                return 0
            text = LOG.read_text(encoding="utf-8", errors="replace")
            message(PORT_BUSY if "already in use" in text else FAILED)
            return 1
        if time.monotonic() > deadline:
            child.terminate()
            message(SLOW)
            return 1
        time.sleep(0.5)

    handles = [int(child._handle)]  # type: ignore[attr-defined]
    if open_window():
        window = watch_window()
        if window is not None:
            handles.append(window)
    # Until the last Career Agent window closes, or the servers stop on their
    # own ("Quit Career Agent").
    wait_for_any(handles)
    if child.poll() is None:
        quit_server()
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.terminate()  # our own child, by its own handle
            child.wait(timeout=10)
    return 0


def main() -> int:
    try:
        return run()
    except Exception:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as log:
            log.write(traceback.format_exc())
        message(FAILED)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
