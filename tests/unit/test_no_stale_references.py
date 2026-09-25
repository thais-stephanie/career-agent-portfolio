"""Current documentation may not name a configuration file that does not exist.

A small gate for a class of drift that is otherwise invisible. The
configuration header told every reader to open `config/search.example.yaml` for
months after it was renamed, and nothing failed -- a comment naming a missing
file is not a syntax error, it is just wrong. A person following a broken
pointer concludes the product is confusing; an agent following one invents
whatever it expected to find there.

Three things keep this gate honest rather than annoying, and each was added
because the first version got it wrong:

**Only `config/*.yaml`.** `config/consistency.py` is how this repository writes
a reference to `src/career_agent/config/consistency.py`, and treating that
shorthand as a missing file made the gate fire on eight correct sentences.

**Only documents that describe the product TODAY.** A planning document from
milestone 0 naming `config/ranking.yaml` is a record of what was intended, and
rewriting it would be falsifying history rather than fixing drift.

**An explicit list of names kept alive to say they are gone.** The correction
for a renamed file is a sentence that names it, so a gate forbidding the name
outright would forbid the fix.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Documents that RECORD a past state. Their claims were true when written.
HISTORICAL = (
    "docs/architecture/",
    "docs/decisions/",
    "docs/checkpoints/",
    "docs/research/",
    "docs/product/source-evidence",
    "docs/product/v1-measurements",
    # Dated 2026-08-25, "before writing a single line of M0", and it says so
    # in a banner at the top. It names files that were planned and never
    # built; rewriting it would falsify a record of what was intended.
    "docs/setup/personal-alpha-environment",
)

#: Files a reader is told to CREATE, and which are gitignored, so a clean
#: clone legitimately does not have them.
CREATED_BY_THE_USER = frozenset(
    {
        "config/search.local.yaml",
        "config/profile.local.yaml",
        "config/career_facts.local.yaml",
        # AI & Semantic Matching settings: written by Career Agent on first save.
        "config/semantic.local.yaml",
    }
)

#: Renamed, and named on purpose so a reader who remembers the old name finds
#: out where it went. The gate exists to stop somebody being SENT there; it
#: must not stop somebody being told it moved.
RETIRED = {"config/search.example.yaml": "config/search.worked-example.yaml"}

REFERENCE = re.compile(r"\b(config/[A-Za-z0-9_.-]+\.ya?ml)\b")


def _current_documents() -> list[Path]:
    found = [
        *sorted(REPO_ROOT.glob("*.md")),
        *sorted((REPO_ROOT / "docs").rglob("*.md")),
        *sorted((REPO_ROOT / "config").glob("*.yaml")),
        *sorted((REPO_ROOT / "src").rglob("*.py")),
    ]
    keep = []
    for path in found:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(part in path.parts for part in ("__pycache__", ".venv")):
            continue
        if rel.startswith(HISTORICAL):
            continue
        keep.append(path)
    return keep


def test_no_current_document_names_a_configuration_file_that_is_missing() -> None:
    missing: dict[str, set[str]] = {}
    for path in _current_documents():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # pragma: no cover - defensive
            continue
        for reference in set(REFERENCE.findall(text)):
            if reference in CREATED_BY_THE_USER or reference in RETIRED:
                continue
            if not (REPO_ROOT / reference).exists():
                missing.setdefault(reference, set()).add(path.relative_to(REPO_ROOT).as_posix())

    assert not missing, "current documents point at configuration that does not exist:\n" + (
        "\n".join(
            f"  {name} -- named in {', '.join(sorted(where)[:4])}"
            for name, where in sorted(missing.items())
        )
    )


def test_a_retired_name_is_only_ever_mentioned_beside_its_replacement() -> None:
    """The rename may be explained. It may not be an instruction.

    `search.example.yaml` became `search.worked-example.yaml` because "example"
    read as "the default" for a file that is somebody's real job search, and a
    new user starting from it inherited a stranger's preferences. Every
    surviving mention has to sit near the new name, which is what makes it an
    explanation rather than a broken pointer.
    """
    offenders: list[str] = []
    for path in _current_documents():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):  # pragma: no cover - defensive
            continue
        for number, line in enumerate(lines, 1):
            for retired, replacement in RETIRED.items():
                # The retired name, not counting occurrences that are really
                # the replacement with the retired name inside it.
                if retired not in line.replace(replacement, ""):
                    continue
                # A window, because the explanation is usually the next
                # sentence rather than the same one.
                window = "\n".join(lines[max(0, number - 4) : number + 4])
                if replacement in window.replace(retired, ""):
                    continue
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{number}")

    assert not offenders, (
        "a retired configuration name is used without saying what replaced it: "
        + ", ".join(offenders)
    )
