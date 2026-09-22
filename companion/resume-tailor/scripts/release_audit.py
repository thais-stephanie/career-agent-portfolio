"""Public-release audit: scan a tree intended for publication and fail on private data.

Checks every text file's CONTENT (not only filenames) for:

* configured private-data markers (an optional local ``private_markers.txt``,
  one marker per line, kept OUT of the public tree);
* obvious secrets: ``sk-`` style keys, ``api_key``/``token``/``password`` assignments
  with values, AWS-style key ids;
* forbidden files: ``.env``, anything under ``input/``, private candidate workspaces,
  local application data;
* binary documents (PDF/DOCX) that are not explicitly allow-listed — a resume or
  source document must never ride along unnoticed.

Honesty note: regex scanning cannot GUARANTEE privacy. This audit is a gate, not a
substitute for the manual review every public release must also receive.

Usage:  python scripts/release_audit.py <target-dir> [--markers scripts/private_markers.txt]
Exit code 0 = no findings; 1 = findings (printed).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".json", ".md", ".txt", ".toml", ".html", ".css",
                 ".yml", ".yaml", ".cfg", ".ini", ".bat", ".ps1", ".lock", ".svg"}
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
]
FORBIDDEN_NAMES = {".env"}
FORBIDDEN_DIR_PARTS = {"input", ".resume-tailor", "node_modules", "__pycache__"}
ALLOWED_BINARY_DOCS: set[str] = set()  # relative posix paths of explicitly reviewed documents
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist"}


def load_markers(path: Path | None) -> list[str]:
    if path is None or not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def audit(target: Path, markers: list[str]) -> list[str]:
    findings: list[str] = []
    for p in sorted(target.rglob("*")):
        rel = p.relative_to(target).as_posix()
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_dir():
            if p.name in FORBIDDEN_DIR_PARTS:
                findings.append(f"forbidden directory present: {rel}/")
            continue
        if p.name in FORBIDDEN_NAMES:
            findings.append(f"forbidden file present: {rel}")
            continue
        if p.suffix.lower() in (".pdf", ".docx", ".doc") and rel not in ALLOWED_BINARY_DOCS:
            findings.append(f"binary document not allow-listed (could contain candidate data): {rel}")
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        for marker in markers:
            if marker.lower() in low:
                findings.append(f"private marker '{marker}' found in {rel}")
        for pat in SECRET_PATTERNS:
            m = pat.search(text)
            if m:
                findings.append(f"possible secret in {rel}: {m.group(0)[:24]}…")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", type=Path)
    ap.add_argument("--markers", type=Path, default=Path(__file__).with_name("private_markers.txt"))
    args = ap.parse_args()
    markers = load_markers(args.markers)
    if not markers:
        print("note: no private_markers.txt found; only generic checks ran", file=sys.stderr)
    findings = audit(args.target, markers)
    for f in findings:
        print(f"AUDIT: {f}")
    print(f"\n{len(findings)} finding(s). "
          + ("DO NOT PUBLISH until resolved." if findings else "Automated audit clean — a manual review is still required before publishing."))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
