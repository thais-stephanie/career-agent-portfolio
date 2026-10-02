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
  /api/app/quit, as Ctrl+C does in the manual launcher; while a collection or
  recalculation is still running it lets it finish first. Without Edge the
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
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
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
ERROR_ALREADY_EXISTS = 183
INFINITE = 0xFFFFFFFF
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
KERNEL32.OpenProcess.restype = ctypes.c_void_p
KERNEL32.CreateMutexW.restype = ctypes.c_void_p
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
OTHER_MODE = (
    "Career Agent is already open in another mode, for example the demo. Close that "
    "window first, then open Career Agent again."
)
FAILED = (
    "Career Agent could not start. Nothing you saved was changed. Try again. If it "
    "happens again, double-click Start-Career-Agent.cmd in the Career Agent folder "
    "to see what went wrong."
)
SLOW = (
    "Career Agent is taking longer than usual to start, for example after an update. "
    "Its window opens by itself when it is ready."
)
INCOMPLETE = (
    "Career Agent's setup is not finished. Double-click Start-Career-Agent.cmd in "
    "the Career Agent folder to finish it."
)


def message(text: str, icon: int = 0x10) -> None:
    ctypes.windll.user32.MessageBoxW(None, text + "\n\n" + HELP, "Career Agent", icon)


def probe() -> dict[str, Any] | str | None:
    """This installation's /api/app answer; None if nothing listens; "busy" if
    something listens and does not answer in time; "other" for anything else."""
    from career_agent import install_id

    try:
        with OPENER.open(URL + "api/app", timeout=3) as response:
            data = json.loads(response.read())
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            return None
        return "busy" if isinstance(exc.reason, TimeoutError) else "other"
    except ConnectionRefusedError:
        return None
    except TimeoutError:
        return "busy"
    except (OSError, ValueError):
        return "other"
    ours = isinstance(data, dict) and data.get("app") == "career-agent"
    return data if ours and data.get("install") == install_id(ROOT) else "other"


def quit_server() -> bool:
    """Ask the servers to stop. False while they are still finding jobs or
    recalculating (the route answers 409), so that work is never cut short."""
    request = urllib.request.Request(
        URL + "api/app/quit",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        OPENER.open(request, timeout=10).close()
    except urllib.error.HTTPError as exc:
        return exc.code != 409
    except OSError:
        pass  # already stopping, or gone
    return True


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


def watch_window(tries: int = 20) -> int | None:
    """A handle on the Edge window process, held so its id cannot be reused under us."""
    for attempt in range(tries):
        pid = window_process()
        if pid:
            handle = KERNEL32.OpenProcess(SYNCHRONIZE, False, pid)
            # Checked again WITH the handle held: the id still names that process.
            if handle and window_process() == pid:
                return int(handle)
            if handle:
                KERNEL32.CloseHandle(ctypes.c_void_p(handle))
        if attempt + 1 < tries:
            time.sleep(0.5)
    return None


def wait_for_any(handles: list[int], milliseconds: int = INFINITE) -> None:
    array = (ctypes.c_void_p * len(handles))(*handles)
    KERNEL32.WaitForMultipleObjects(len(handles), array, False, milliseconds)


def show(found: dict[str, Any] | str | None) -> int:
    """Open a window on a server that is already there, or say why not."""
    if isinstance(found, dict) and found.get("mode") == "PERSONAL":
        open_window()
        return 0
    if isinstance(found, dict):
        message(OTHER_MODE)
    else:
        message(PORT_BUSY if found else FAILED)
    return 1


def run() -> int:
    try:
        from career_agent import install_id
    except ImportError:
        message(INCOMPLETE)
        return 1
    # One launcher per installation starts the servers. A second click while
    # the first is starting them (or watching their window) waits for them.
    name = "Local\\CareerAgentDesktop-" + install_id(ROOT)
    mutex = KERNEL32.CreateMutexW(None, False, name)
    if mutex and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        deadline = time.monotonic() + READY_SECONDS * 2
        found = probe()
        while not isinstance(found, dict) and found != "other" and time.monotonic() < deadline:
            time.sleep(1)
            found = probe()
        return show(found)
    deadline = time.monotonic() + 30
    found = probe()
    while found == "busy" and time.monotonic() < deadline:
        time.sleep(1)
        found = probe()
    if found is not None:
        return show(found)

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
    started, warned = time.monotonic(), False
    while not isinstance(probe(), dict):
        if child.poll() is not None:
            text = LOG.read_text(encoding="utf-8", errors="replace")
            message(PORT_BUSY if "already in use" in text else FAILED)
            return 1
        if not warned and time.monotonic() - started > READY_SECONDS:
            # A first start after an update can migrate a large database.
            # Never stopped for being slow: said once, and waited for.
            warned = True
            threading.Thread(target=message, args=(SLOW, 0x40), daemon=True).start()
        time.sleep(0.5)

    server = int(child._handle)  # type: ignore[attr-defined]
    window = None
    if open_window():
        window = watch_window()
        if window is None and open_window():
            # Measured once in testing: Edge did not come up on the first ask.
            window = watch_window()
        if window is None:
            # Never leave the person without a window. This one cannot be
            # watched, so the servers stop through "Quit Career Agent".
            webbrowser.open(URL)
    closed = False  # every window this process watched has closed
    while child.poll() is None:
        if window is not None:
            wait_for_any([server, window])
            window, closed = None, True
        elif not closed:
            wait_for_any([server])  # no window to watch: until "Quit Career Agent"
            continue
        if child.poll() is not None:
            break
        window = watch_window(tries=1)  # a window opened again meanwhile
        if window is not None:
            continue
        if quit_server():
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.terminate()  # our own child, by its own handle
                child.wait(timeout=10)
            break
        # Still finding jobs or recalculating: let it finish, then ask again.
        wait_for_any([server], 30_000)
    return 0


def main() -> int:
    try:
        return run()
    except Exception:
        with contextlib.suppress(OSError):
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8") as log:
                log.write(traceback.format_exc())
        message(FAILED)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
