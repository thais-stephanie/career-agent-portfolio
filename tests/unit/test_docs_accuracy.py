"""Every command the USER-FACING documentation prints has to exist.

The design documents are exempt and say so at their own link: `milestone-0.md`
describes stages that were planned rather than built, and names
`career-agent evaluate`, `rank`, `digest`, `reproject`, `stats` and `eval`,
none of which were written. That is a record of a design, and rewriting it
would falsify what was designed.

What must never happen is the README, the troubleshooting guide or the
contribution guide telling somebody to type something that does not work.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The documents a person actually follows.
USER_FACING = (
    "README.md", "README.pt-BR.md", "README.es.md", "FIRST_RUN.md",
    "CONTRIBUTING.md", "docs/PRIVACY.md", "THIRD_PARTY_NOTICES.md",
)


def _cli_help() -> str:
    """`--help`, decoded defensively.

    Typer draws a box with line-drawing characters, and the default Windows
    console encoding cannot decode them. That is a property of the terminal
    rather than of the output, so the bytes are decoded here explicitly.
    """
    result = subprocess.run(
        ["uv", "run", "career-agent", "--help"],
        capture_output=True,
        cwd=REPO_ROOT,
        check=False,
    )
    return result.stdout.decode("utf-8", errors="replace")


def test_every_command_the_user_facing_docs_print_actually_exists() -> None:
    help_text = _cli_help()
    assert "career-agent" in help_text, "the CLI did not answer --help at all"

    missing: list[str] = []
    for name in USER_FACING:
        path = REPO_ROOT / name
        if not path.exists():
            missing.append(f"{name}: the document itself is missing")
            continue
        for command in sorted(
            set(re.findall(r"career-agent ([a-z][a-z-]+)", path.read_text(encoding="utf-8")))
        ):
            if command == "help":
                continue
            if not re.search(rf"\b{re.escape(command)}\b", help_text):
                missing.append(f"{name} tells somebody to run `career-agent {command}`")
    assert not missing, "\n".join(missing)


def test_every_relative_link_in_the_user_facing_docs_resolves() -> None:
    """A broken link in a README is a small thing that reads as neglect."""
    broken: list[str] = []
    for name in USER_FACING:
        path = REPO_ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            resolved = (path.parent / target.split("#")[0]).resolve()
            if not resolved.exists():
                broken.append(f"{name} -> {target}")
    assert not broken, "\n".join(broken)
