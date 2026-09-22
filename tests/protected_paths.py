"""Files a test suite may never change, and the machinery that proves it did not.

WHY THIS EXISTS
---------------
On 2026-09-08 the owner's `config/search.local.yaml` went from `config_version`
4 to 21 while a test run was in progress. `config_version` is not an edit
counter: it is what a stored score is TRUE RELATIVE TO, so every one of her
19,469 `job_match` rows stopped answering the current question at once. The
product then said, correctly and uselessly, that these jobs had not been scored
yet. It took a digest comparison against a backup zip to prove nothing had been
lost.

`tests/support.py` already states the rule -- a test reads only committed
configuration -- and states it as prose. This module is the part that finds out.

THE CONTRACT, NOT ONE PERSON'S PATHS
------------------------------------
Protection is expressed as PATTERNS under the repository root, so it is a
statement about which KINDS of file are operational rather than a list of one
machine's filenames. `data/m1d2/career.db` is protected because it matches
`data/**/*.db`, not because it is hers.

Two tiers, because the cost of checking differs by three orders of magnitude:

* SMALL text files are hashed AND held in memory, so a violation can be
  reported and the file put back exactly as it was. They are tens of kilobytes.
* DATABASES are checked by size and modification time only. Hashing 2.1 GB per
  test is not a guard, it is an outage, and a test has no business opening one
  for writing in the first place.

Detection rather than prevention, deliberately. Intercepting every write in
every subprocess would be a large amount of machinery to enforce a rule that a
single honest check at teardown enforces just as well -- and names the exact
test, which is the part that gets it fixed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Glob patterns, relative to the repository root, naming operational data.
#:
#: `*.local.yaml` is the owner's real search and profile. The `.backup` sidecar
#: is the same file one rename away. `data/**/*.db` is every corpus, including
#: the empty default -- a test that writes into `data/career.db` is a test that
#: would have written into the real one had the resolution gone differently.
PROTECTED_PATTERNS: tuple[str, ...] = (
    "config/*.local.yaml",
    "config/*.local.yaml.*",
    "data/*.db",
    "data/**/*.db",
)

#: Above this, a file is checked by size and mtime rather than by content.
#: A configuration is tens of kilobytes; a corpus is gigabytes.
HASH_LIMIT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Guarded:
    """One protected file as it was when the session began.

    `content` is None for anything too large to hold, which is exactly the
    set of files a restore must never attempt.
    """

    path: Path
    digest: str
    content: bytes | None

    @property
    def restorable(self) -> bool:
        return self.content is not None


def protected_files(root: Path | None = None) -> list[Path]:
    """Every operational file present right now, absolute and deduplicated.

    Absolute, because `monkeypatch.chdir` is ordinary in this suite and a
    relative path resolved from a temp directory silently protects nothing.
    The first version of this check used relative paths and reported every
    chdir-ing test as a writer.
    """
    base = (root or REPO_ROOT).resolve()
    found: set[Path] = set()
    for pattern in PROTECTED_PATTERNS:
        for path in base.glob(pattern):
            if path.is_file():
                found.add(path.resolve())
    return sorted(found)


def _fingerprint(path: Path) -> tuple[str, bytes | None]:
    stat = path.stat()
    if stat.st_size > HASH_LIMIT_BYTES:
        # Cheap and sufficient: a test that rewrote a corpus changes both.
        return f"size={stat.st_size}:mtime={stat.st_mtime_ns}", None
    content = path.read_bytes()
    return hashlib.sha256(content).hexdigest(), content


def snapshot(root: Path | None = None) -> dict[Path, Guarded]:
    """What every protected file looks like at this moment."""
    out: dict[Path, Guarded] = {}
    for path in protected_files(root):
        digest, content = _fingerprint(path)
        out[path] = Guarded(path=path, digest=digest, content=content)
    return out


def changed(before: dict[Path, Guarded]) -> list[Path]:
    """Which protected files no longer match the snapshot.

    A file that has been DELETED counts as changed, and a file that appeared
    since the snapshot does not: creating `config/search.local.yaml` on a
    machine that has none is what `career-agent setup` is for, and a test doing
    it in a temp directory is invisible here anyway because this only ever
    looks under the repository root.
    """
    out: list[Path] = []
    for path, guarded in before.items():
        if not path.exists():
            out.append(path)
            continue
        digest, _ = _fingerprint(path)
        if digest != guarded.digest:
            out.append(path)
    return out


def restore(before: dict[Path, Guarded], paths: list[Path]) -> list[Path]:
    """Put back what can be put back. Returns what could not.

    Restoring does not excuse the violation -- the run still fails -- but the
    owner's operational data should not be collateral damage from a test she
    did not write. A file too large to have been held is reported instead.
    """
    unrestored: list[Path] = []
    for path in paths:
        guarded = before.get(path)
        if guarded is None or not guarded.restorable:
            unrestored.append(path)
            continue
        path.write_bytes(guarded.content or b"")
    return unrestored


def describe(paths: list[Path], root: Path | None = None) -> str:
    """The violation, as a sentence naming files rather than absolute paths."""
    base = (root or REPO_ROOT).resolve()
    names = []
    for path in paths:
        try:
            names.append(str(path.relative_to(base)))
        except ValueError:
            names.append(path.name)
    return ", ".join(sorted(names))
