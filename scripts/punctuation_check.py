"""The punctuation gate: no long dashes in project-authored text.

WHAT THIS ENFORCES
------------------
Project-authored prose uses ASCII punctuation. The Unicode em dash (U+2014)
and en dash (U+2013) are banned from every string this project writes for a
person to read: interface copy, CSS generated content, Python and JavaScript
strings, documentation, README files, reports, ADRs, comments, commit
messages and future release material.

Use periods, commas, colons, parentheses or semicolons instead. ASCII hyphens
stay allowed everywhere they are technically required: CSS property names, CLI
flags, code syntax, filenames, identifiers and established standards.

WHY IT IS A GATE AND NOT A STYLE NOTE
-------------------------------------
A convention that lives in a document is a convention that decays. Every file
in this repository already had to be swept once; the gate is what stops the
next sweep from being necessary. It runs under pytest for the same reason the
frontend gate does, so it cannot quietly stop being run.

WHY THERE IS AN EXCLUSION LIST, AND WHY EVERY ENTRY CARRIES A REASON
--------------------------------------------------------------------
Some files in this repository are NOT project-authored text, and rewriting a
character inside one of them would be a defect rather than a cleanup:

* an employer wrote it, and evidence is verified as a contiguous substring of
  exactly those bytes (ADR-0002);
* a digest is pinned to exactly those bytes (`llm/prompts.py::PROMPT_DIGESTS`);
* it is a vendor's archived payload, kept verbatim as provenance.

The Speedrun feed makes the first case concrete rather than theoretical: real
posting descriptions retrieved from it contain U+2014, because employers write
with em dashes. Normalising employer text to satisfy our own house style would
silently break the substring verification that the evidence contract rests on.

So the exclusions are DECLARED, each with a reason, and a test asserts that
every declared path still exists. A stale exclusion is a hole nobody can see,
which is the failure mode this list is shaped to avoid.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The two characters. Named so error output can say which one was found.
#:
#: Written as escapes rather than literally so that this gate does not trip over
#: its own definition, and so that no editor or copy-paste can silently swap one
#: for a look-alike. The escapes ARE the two characters.
EM_DASH = "\u2014"
EN_DASH = "\u2013"
LONG_DASHES: dict[str, str] = {
    EM_DASH: "U+2014 EM DASH",
    EN_DASH: "U+2013 EN DASH",
}

#: The line-level opt-out, for a long dash that is DATA rather than prose.
#:
#: `domain/verify.py` is the case that forced this to exist. Its normalisation
#: table maps U+2014 and U+2013 to an ASCII hyphen so that NORMALISED evidence
#: matching works on employer text, and employers write with em dashes. Deleting
#: those two characters would not tidy the file; it would break evidence
#: verification for every posting that contains one.
#:
#: A file-level exclusion would have hidden the whole file. This hides one line,
#: and `require_reason` makes the author say why, on that line, where the next
#: reader will see it.
ALLOW_MARKER = "punctuation-check: allow"


@dataclass(frozen=True)
class Exclusion:
    """One path prefix that is not project-authored text, and why.

    `prefix` is matched against the repository-relative path with forward
    slashes, so it addresses either one file or a whole tree.
    """

    prefix: str
    reason: str

    def covers(self, relative_path: str) -> bool:
        return relative_path == self.prefix or relative_path.startswith(self.prefix + "/")


#: Every file this gate deliberately does not read, with the reason it does not.
#:
#: Adding an entry here is a decision about provenance, never a way to make the
#: gate quiet. If the text is ours, fix the text.
EXCLUSIONS: tuple[Exclusion, ...] = (
    Exclusion(
        "companion/resume-tailor",
        "Vendored Apache-2.0 component; original input grammar, resume typography "
        "and punctuation are preserved.",
    ),
    Exclusion(
        "src/career_agent/llm/prompts",
        "Prompt versions are byte-frozen. `PROMPT_DIGESTS` verifies each file on "
        "every load, and a prompt used for a live call is immutable: answers "
        "already stored under that version were given to those exact bytes.",
    ),
    Exclusion(
        "tests/fixtures/providers",
        "Vendor payloads archived verbatim as provenance. They are a record of "
        "what a provider returned, not text this project wrote.",
    ),
    Exclusion(
        "uv.lock",
        "Generated dependency lock file. Not prose, and not hand-edited.",
    ),
)


def is_excluded(relative_path: str) -> Exclusion | None:
    for exclusion in EXCLUSIONS:
        if exclusion.covers(relative_path):
            return exclusion
    return None


@dataclass(frozen=True)
class Finding:
    """One long dash, located precisely enough to fix without searching."""

    path: str
    line_number: int
    column: int
    character: str
    context: str

    def render(self) -> str:
        name = LONG_DASHES[self.character]
        return f"{self.path}:{self.line_number}:{self.column}: {name}\n    {self.context}"


def tracked_files(repo_root: Path) -> list[str]:
    """Every file git tracks, as repository-relative forward-slash paths.

    Asking git rather than walking the tree is deliberate: it skips the virtual
    environment, the caches and every gitignored local file (`.env`,
    `config/*.local.yaml`, `data/`) without this script having to know what
    those are. A file that is not tracked is not project-authored text that
    anyone will read.
    """
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        ["git", "ls-files", "-z"],  # noqa: S607 -- git resolved from PATH by design
        cwd=str(repo_root),
        capture_output=True,
        check=True,
        timeout=120,
    )
    raw = result.stdout.decode("utf-8", errors="replace")
    return [name for name in raw.split("\0") if name]


@dataclass(frozen=True)
class Allowance:
    """One line-level opt-out, and the reason written beside it."""

    path: str
    line_number: int
    reason: str

    @property
    def has_reason(self) -> bool:
        return bool(self.reason.strip())


def read_allowance(path: str, line_number: int, line: str) -> Allowance | None:
    """The opt-out on this line, if it carries one.

    The reason is whatever follows the marker. It is not parsed beyond being
    non-empty: the point is that a human wrote something a later human can read,
    not that it matches a grammar.
    """
    index = line.find(ALLOW_MARKER)
    if index < 0:
        return None
    return Allowance(
        path=path,
        line_number=line_number,
        reason=line[index + len(ALLOW_MARKER) :].strip(" :-()"),
    )


def scan_text(path: str, text: str) -> tuple[list[Finding], list[Allowance]]:
    """Every long dash in one file's text, and every line-level opt-out used.

    A line carrying `ALLOW_MARKER` produces an allowance instead of findings, so
    the caller can check that the opt-out was justified rather than merely used.
    """
    findings: list[Finding] = []
    allowances: list[Allowance] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        line_number = index + 1
        if not any(character in LONG_DASHES for character in line):
            continue
        # Same line, or the line above. The line above exists because a data
        # line can already be near the length limit, and a gate that forces a
        # lint failure to satisfy itself is a gate people delete.
        previous = lines[index - 1] if index else ""
        allowance = read_allowance(path, line_number, line) or read_allowance(
            path, line_number, previous
        )
        if allowance is not None:
            allowances.append(allowance)
            continue
        for column, character in enumerate(line, start=1):
            if character in LONG_DASHES:
                findings.append(
                    Finding(
                        path=path,
                        line_number=line_number,
                        column=column,
                        character=character,
                        context=_context(line, column),
                    )
                )
    return findings, allowances


def _context(line: str, column: int) -> str:
    """A short window around the offending character, with a marker under it."""
    start = max(0, column - 41)
    window = line[start : column + 40]
    return f"{window.strip()}"


@dataclass(frozen=True)
class ScanResult:
    """What one pass over the repository found, and what it was allowed to skip."""

    findings: tuple[Finding, ...]
    allowances: tuple[Allowance, ...]
    files_scanned: int
    files_excluded: int

    @property
    def ok(self) -> bool:
        return not self.findings


def scan_repository(repo_root: Path | None = None) -> ScanResult:
    """Every long dash in project-authored text. Excluded paths are not read."""
    root = repo_root or REPO_ROOT
    findings: list[Finding] = []
    allowances: list[Allowance] = []
    scanned = 0
    excluded = 0
    for name in tracked_files(root):
        if is_excluded(name) is not None:
            excluded += 1
            continue
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # A file that is not UTF-8 text is not prose. Binary assets reach
            # here only if someone adds one outside `docs/evidence`, and
            # skipping is the honest response: this gate reads text.
            continue
        scanned += 1
        file_findings, file_allowances = scan_text(name, text)
        findings.extend(file_findings)
        allowances.extend(file_allowances)
    return ScanResult(
        findings=tuple(findings),
        allowances=tuple(allowances),
        files_scanned=scanned,
        files_excluded=excluded,
    )


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(argv[0]).resolve() if argv else REPO_ROOT
    result = scan_repository(root)

    unexplained = [a for a in result.allowances if not a.has_reason]
    if result.ok and not unexplained:
        print(f"punctuation: clean ({result.files_scanned} project-authored files scanned)")
        print(
            f"punctuation: {result.files_excluded} files under "
            f"{len(EXCLUSIONS)} declared exclusions, each with a recorded reason"
        )
        print(f"punctuation: {len(result.allowances)} line-level allowances, all explained")
        return 0

    for allowance in unexplained:
        print(
            f"{allowance.path}:{allowance.line_number}: '{ALLOW_MARKER}' with no reason after it",
            file=sys.stderr,
        )

    if result.findings:
        print(
            f"punctuation: {len(result.findings)} long dash(es) in project-authored text",
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        for finding in result.findings:
            print(finding.render(), file=sys.stderr)
        print("", file=sys.stderr)
        print(
            "Use a period, comma, colon, parenthesis or semicolon instead. If these "
            "bytes are NOT project-authored (an employer wrote them, or a digest is "
            "pinned to them), add a declared exclusion with its reason in "
            "scripts/punctuation_check.py, or a line-level "
            f"'{ALLOW_MARKER} <reason>' where the character is data.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
