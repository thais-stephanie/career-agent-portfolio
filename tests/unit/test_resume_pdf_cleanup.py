"""PR 5A: what a PDF export leaves in the temp folder, and which browser it stops.

The resume's own text (`resume.html`, `resume.pdf`) holds a name, an email
and a phone number, so it must leave the temp folder even when the browser
still holds its profile. A print that times out stops THIS export's browser
and nothing else. Profiles an earlier export could not remove are swept once
they are old. All content here is synthetic.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter

from career_agent.resume_doc import export as exporter

HTML = "<!doctype html><title>Synthetic</title><p>Morgan Synthetic, morgan@example.test</p>"
windows_only = pytest.mark.skipif(os.name != "nt", reason="the stop path runs on Windows only")


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The system temp folder, here; a fake browser that prints a real PDF."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(exporter, "find_browser", lambda: ("fake-browser", "msedge"))
    monkeypatch.setattr(exporter.time, "sleep", lambda _s: None)
    return tmp_path


def _printing(args: list[str], **_kw: Any) -> None:
    out = next(a for a in args if a.startswith("--print-to-pdf="))
    Path(out.split("=", 1)[1]).write_bytes(_pdf())


def _profiles(temp: Path) -> list[Path]:
    return list(temp.glob(f"{exporter.TEMP_PREFIX}*"))


def test_the_resume_leaves_temp_even_when_the_profile_stays_locked(
    temp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exporter.subprocess, "Popen", _printing)

    def locked(_path: Any) -> None:
        raise PermissionError("the browser still holds its profile")

    monkeypatch.setattr(exporter.shutil, "rmtree", locked)
    stopped: list[Path] = []
    monkeypatch.setattr(exporter, "_stop_browser", stopped.append)
    data, _engine = exporter.print_pdf(HTML)
    assert data.startswith(b"%PDF")
    [left] = _profiles(temp)  # genuinely locked: the folder may stay...
    assert not (left / "resume.html").exists(), "the resume's text stayed in temp"
    assert not (left / "resume.pdf").exists(), "the resume's PDF stayed in temp"
    assert stopped == [left], "a browser still holding the folder is stopped, then retried"


def test_a_print_that_fails_any_other_way_stops_its_browser(
    temp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_a: Any, **_k: Any) -> None:
        raise OSError("the browser could not start")

    monkeypatch.setattr(exporter.subprocess, "Popen", broken)
    stopped: list[Path] = []
    monkeypatch.setattr(exporter, "_stop_browser", stopped.append)
    with pytest.raises(OSError):
        exporter.print_pdf(HTML)
    assert len(stopped) == 1 and _profiles(temp) == []


def test_a_timed_out_print_stops_its_browser_and_leaves_no_resume(
    temp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exporter.subprocess, "Popen", lambda *a, **k: None)  # never prints
    stopped: list[Path] = []
    monkeypatch.setattr(exporter, "_stop_browser", stopped.append)
    with pytest.raises(exporter.ExportFailed, match="PDF_TIMEOUT"):
        exporter.print_pdf(HTML, timeout=0.01)
    assert len(stopped) == 1 and stopped[0].name.startswith(exporter.TEMP_PREFIX)
    assert stopped[0].parent == temp, "the stop names THIS export's temp folder"
    assert _profiles(temp) == []


@windows_only
def test_the_stop_names_one_folder_exactly_and_never_a_process_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    asked: list[list[str]] = []
    monkeypatch.setattr(exporter.subprocess, "run", lambda args, **_k: asked.append(args))
    folder = tmp_path / f"{exporter.TEMP_PREFIX}o'k[1]"
    exporter._stop_browser(folder)
    [command] = [args[-1] for args in asked]
    assert f".Contains('{str(folder).replace(chr(39), chr(39) * 2)}{os.sep}')" in command
    assert "$_.ProcessId -ne $PID" in command, "PowerShell must not stop itself first"
    assert "Stop-Process -Id" in command
    for broad in ("-Name", "taskkill", "msedge", "chrome", "-like"):
        assert broad not in command


def _sleeper(folder: Path) -> subprocess.Popen[bytes]:
    """A synthetic process whose command line names `folder` as a browser's would."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)", f"--user-data-dir={folder}"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


@windows_only
def test_the_stop_ends_only_the_process_using_this_profile(tmp_path: Path) -> None:
    mine = tmp_path / f"{exporter.TEMP_PREFIX}mine1234"
    # Another export whose folder name STARTS with this one's must survive.
    other = tmp_path / f"{exporter.TEMP_PREFIX}mine12345"
    target, bystander = _sleeper(mine / "profile"), _sleeper(other / "profile")
    try:
        exporter._stop_browser(mine)
        target.wait(timeout=30)
        assert bystander.poll() is None, "another process was stopped"
    finally:
        for proc in (target, bystander):
            proc.kill()
            proc.wait()


def test_the_sweep_removes_only_old_print_profiles(temp: Path) -> None:
    old = temp / f"{exporter.TEMP_PREFIX}old"
    recent = temp / f"{exporter.TEMP_PREFIX}recent"
    unrelated = temp / "someone-elses-old-folder"
    for folder in (old, recent, unrelated):
        (folder / "profile").mkdir(parents=True)
    hours_ago = time.time() - 2 * 3600
    for folder in (old, unrelated):
        os.utime(folder, (hours_ago, hours_ago))
    exporter._sweep_old_profiles()
    assert not old.exists()
    assert recent.exists(), "a live export's folder is recent and stays"
    assert unrelated.exists(), "only this program's print folders are swept"


def test_a_successful_print_leaves_nothing_in_temp(
    temp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exporter.subprocess, "Popen", _printing)
    exporter.print_pdf(HTML)
    assert _profiles(temp) == []


@pytest.mark.skipif(exporter.find_browser() is None, reason="no Edge or Chrome: NOT verified")
def test_a_real_print_leaves_no_resume_no_profile_and_no_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    data, _engine = exporter.print_pdf(HTML)
    assert data.startswith(b"%PDF")
    assert _profiles(tmp_path) == []
    if os.name == "nt":
        found = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and"
                f" $_.CommandLine -and $_.CommandLine.Contains('{tmp_path}') }}).Count",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert found.stdout.strip() in ("", "0"), "a headless browser outlived its export"
