"""The frontend gate, run from pytest so it cannot quietly stop being run.

`scripts/frontend_check.mjs` is the frontend's equivalent of ruff and mypy:
parse, resolve, safety, hygiene. A check that lives only in a developer's shell
history is a check that stops happening, so it runs here too, and these tests
also assert that the gate would actually *catch* something, because a linter
that passes everything is indistinguishable from no linter at all.

Skipped, not failed, when node is absent. The Python side of this project has
no Node dependency and must stay installable without one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts" / "frontend_check.mjs"
STATIC = REPO_ROOT / "src" / "career_agent" / "web" / "static"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed on this machine")


def run_checker(checker: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a specific copy of the checker.

    The path MATTERS: the script resolves the static directory from its own
    `import.meta.url`, not from the working directory, so pointing at the real
    checker would scan the real tree no matter where it was invoked. The
    sandbox tests below depend on that distinction.
    """
    assert NODE is not None
    target = checker or CHECKER
    return subprocess.run(  # noqa: S603  -- fixed argv, no shell
        [NODE, str(target)],
        cwd=str(target.parent.parent),
        capture_output=True,
        text=True,
        timeout=180,
    )


def _sandbox(tmp_path: Path) -> Path:
    """A throwaway copy of the frontend, so a probe never touches the real one."""
    sandbox = tmp_path / "repo"
    (sandbox / "scripts").mkdir(parents=True)
    shutil.copy2(CHECKER, sandbox / "scripts" / "frontend_check.mjs")
    shutil.copytree(STATIC, sandbox / "src" / "career_agent" / "web" / "static")
    return sandbox


# =========================================================================
# the gate itself
# =========================================================================


@needs_node
def test_the_frontend_gate_passes() -> None:
    result = run_checker()
    assert result.returncode == 0, "the frontend gate failed:\n" + result.stdout + result.stderr
    assert "clean" in result.stdout


@needs_node
def test_the_gate_reports_what_it_checked() -> None:
    """A gate that passes silently is one nobody notices has broken."""
    result = run_checker()
    for pass_name in ("parse", "resolve", "safety", "hygiene"):
        assert pass_name in result.stdout, f"the {pass_name} pass is not reported"


@needs_node
def test_the_gate_actually_catches_an_unsafe_sink(tmp_path: Path) -> None:
    """Prove the safety pass is live by giving it something it must reject.

    Written against a COPY of the tree, so the real modules are never touched.
    Without this, a regex that silently stopped matching would look exactly
    like a clean codebase.
    """
    sandbox = _sandbox(tmp_path)
    offender = sandbox / "src" / "career_agent" / "web" / "static" / "js" / "_probe.js"
    offender.write_text(
        "export function bad(node, jobText) {\n  node.innerHTML = jobText;\n}\n",
        encoding="utf-8",
    )
    result = run_checker(sandbox / "scripts" / "frontend_check.mjs")
    assert result.returncode == 1
    assert "safety/sink" in result.stderr


@needs_node
def test_the_gate_actually_catches_a_broken_import(tmp_path: Path) -> None:
    """The resolve pass is this project's stand-in for a build.

    There is no bundler, so a renamed module is a blank page and a 404 rather
    than a compile error. This is the check that turns it back into one.
    """
    sandbox = _sandbox(tmp_path)
    probe = sandbox / "src" / "career_agent" / "web" / "static" / "js" / "_probe.js"
    probe.write_text(
        "import { nothing } from './does-not-exist.js';\nexport default nothing;\n",
        encoding="utf-8",
    )
    result = run_checker(sandbox / "scripts" / "frontend_check.mjs")
    assert result.returncode == 1
    assert "import/missing" in result.stderr


# =========================================================================
# things the gate cannot see, asserted here instead
# =========================================================================


def test_every_module_the_page_loads_exists() -> None:
    """`index.html` names an entry point; it must be on disk.

    The gate walks the module graph from the files it finds. This walks it from
    the HTML, which is where the browser actually starts.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'type="module"' in html, "the page must load ES modules"
    for name in ("main.js",):
        assert name in html, f"{name} is not referenced by index.html"
        assert (STATIC / "js" / name).is_file()


def test_the_page_ships_its_own_stylesheet() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "app.css" in html
    assert (STATIC / "app.css").is_file()


def test_no_frontend_dependency_manifest_exists() -> None:
    """There is no bundler, and that must stay a fact rather than a memory.

    A stray `package.json` under the web tree would mean someone reached for
    npm; the architecture note says why that decision needs to be deliberate
    rather than incidental.
    """
    for stray in ("package.json", "package-lock.json", "node_modules", "tsconfig.json"):
        assert not (STATIC / stray).exists(), f"{stray} appeared under static/"
        assert not (STATIC.parent / stray).exists(), f"{stray} appeared under web/"


def test_the_mock_harness_cannot_be_switched_on_from_a_url() -> None:
    """The production page must not carry a mode that replaces its data.

    `?mock=1` used to do exactly that. It failed loudly because the fixture
    sits outside `static/`, but a page serving a person's job search should not
    have a query string that swaps in invented postings.
    """
    api = (STATIC / "js" / "api.js").read_text(encoding="utf-8")
    assert "window.__CAREER_AGENT_MOCK__" in api
    assert "params.get('mock')" not in api
    assert "searchParams" not in api.split("export const MOCK")[0].split("\n")[-3:][0]


def test_the_mock_fixture_is_not_served_to_the_browser() -> None:
    """It lives outside `static/`, so the server cannot hand it out."""
    fixture = STATIC.parent / "mock" / "api-fixture.json"
    assert fixture.is_file(), "the dev harness needs its fixture"
    assert STATIC not in fixture.parents, "the fixture must not be inside static/"
    json.loads(fixture.read_text(encoding="utf-8"))  # and it must be valid JSON
