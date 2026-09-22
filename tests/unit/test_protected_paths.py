"""The guard that would have caught the 2026-09-08 configuration incident.

Everything here runs against a FABRICATED repository root under `tmp_path`.
A test proving that a write to the real `config/search.local.yaml` is detected
by writing to the real `config/search.local.yaml` would be the very accident it
exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.protected_paths import (
    HASH_LIMIT_BYTES,
    PROTECTED_PATTERNS,
    REPO_ROOT,
    changed,
    describe,
    protected_files,
    restore,
    snapshot,
)


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A directory shaped like this repository, holding nothing real."""
    (tmp_path / "config").mkdir()
    (tmp_path / "data" / "m1d2").mkdir(parents=True)
    (tmp_path / "config" / "search.local.yaml").write_text("config_version: 4\n", encoding="utf-8")
    (tmp_path / "config" / "search.local.yaml.backup").write_text(
        "config_version: 3\n", encoding="utf-8"
    )
    # Committed files, which are NOT protected: a test is free to read them and
    # `git checkout` is what puts them back.
    (tmp_path / "config" / "search.worked-example.yaml").write_text("x: 1\n", encoding="utf-8")
    (tmp_path / "data" / "m1d2" / "career.db").write_bytes(b"SQLite format 3\x00" + b"0" * 64)
    return tmp_path


# =========================================================================
# 1. WHAT IS PROTECTED
# =========================================================================


def test_it_finds_the_private_configuration_and_every_corpus(fake_repo: Path) -> None:
    names = {p.name for p in protected_files(fake_repo)}

    assert names == {"search.local.yaml", "search.local.yaml.backup", "career.db"}


def test_a_committed_file_is_not_protected(fake_repo: Path) -> None:
    """The example is version-controlled. Guarding it would be noise, and it
    would fire on the one legitimate reason to edit `config/` in a test."""
    assert "search.worked-example.yaml" not in {p.name for p in protected_files(fake_repo)}


def test_the_contract_is_patterns_rather_than_one_persons_paths() -> None:
    """`data/m1d2/career.db` is protected because it is a corpus, not because
    it is hers. A different machine's layout gets the same protection."""
    for pattern in PROTECTED_PATTERNS:
        assert not pattern.startswith("/")
        assert "m1d2" not in pattern
        assert "thais" not in pattern.lower()


def test_the_paths_are_absolute_so_a_chdir_cannot_blind_it(fake_repo: Path) -> None:
    """The first version of this check used relative paths and reported every
    `monkeypatch.chdir` test as a writer, which buried the real signal."""
    for path in protected_files(fake_repo):
        assert path.is_absolute()


# =========================================================================
# 2. WHAT IT NOTICES
# =========================================================================


def test_an_untouched_session_reports_nothing(fake_repo: Path) -> None:
    before = snapshot(fake_repo)

    assert changed(before) == []


def test_a_rewritten_configuration_is_caught(fake_repo: Path) -> None:
    """**The incident, in one assertion.** A version bump and nothing else."""
    before = snapshot(fake_repo)
    target = fake_repo / "config" / "search.local.yaml"
    target.write_text("config_version: 21\n", encoding="utf-8")

    damaged = changed(before)

    assert damaged == [target]
    assert "search.local.yaml" in describe(damaged, fake_repo)


def test_a_deleted_file_counts_as_changed(fake_repo: Path) -> None:
    """Absence is not the same as untouched, here least of all."""
    before = snapshot(fake_repo)
    (fake_repo / "config" / "search.local.yaml").unlink()

    assert (fake_repo / "config" / "search.local.yaml") in changed(before)


def test_a_rewritten_corpus_is_caught_without_hashing_it(fake_repo: Path) -> None:
    """A corpus is checked by size and mtime. Hashing 2.1 GB per test is not a
    guard, it is an outage."""
    database = fake_repo / "data" / "m1d2" / "career.db"
    database.write_bytes(b"x" * (HASH_LIMIT_BYTES + 1))
    before = snapshot(fake_repo)
    assert before[database].content is None, "a large file must not be held in memory"

    database.write_bytes(b"y" * (HASH_LIMIT_BYTES + 2))

    assert changed(before) == [database]


# =========================================================================
# 3. WHAT IT PUTS BACK
# =========================================================================


def test_a_damaged_configuration_is_restored_byte_for_byte(fake_repo: Path) -> None:
    """Restoring does not excuse the violation. It stops the owner's data being
    collateral damage from a test she did not write."""
    target = fake_repo / "config" / "search.local.yaml"
    original = target.read_bytes()
    before = snapshot(fake_repo)
    target.write_text("config_version: 21\nand: junk\n", encoding="utf-8")

    unrestored = restore(before, changed(before))

    assert unrestored == []
    assert target.read_bytes() == original
    assert changed(before) == []


def test_a_corpus_is_reported_as_unrestorable_rather_than_guessed_at(fake_repo: Path) -> None:
    """It was never held, so it cannot be put back, and saying so is the only
    honest option. Silently reporting success would be the worse failure."""
    database = fake_repo / "data" / "m1d2" / "career.db"
    database.write_bytes(b"x" * (HASH_LIMIT_BYTES + 1))
    before = snapshot(fake_repo)
    database.write_bytes(b"y" * (HASH_LIMIT_BYTES + 2))

    unrestored = restore(before, changed(before))

    assert unrestored == [database]


# =========================================================================
# 4. AND IT IS ACTUALLY INSTALLED
# =========================================================================


def test_the_guard_is_watching_this_very_session() -> None:
    """A guard nobody wired in is a document.

    `tests/conftest.py` sits at the root of `testpaths`, so pytest loads it for
    unit, integration and browser alike -- the damage does not care which
    directory a test lived in.

    "Watching" means the baseline covers every protected file that EXISTS.
    On the owner's machine that is a private search and a corpus; on a fresh
    clone it is nothing, and a guard over an empty set is still installed.
    The first version asserted the baseline was non-empty, which conflated
    "the guard is wired in" with "this checkout has operational data" and
    failed on every clean checkout (2026-09-18).
    """
    from tests import conftest

    assert hasattr(conftest, "pytest_runtest_teardown")
    assert set(conftest._BASELINE) == set(snapshot()), (
        "the session baseline does not cover the protected files that exist"
    )


def test_the_real_repository_has_something_worth_guarding() -> None:
    """If this ever finds nothing ON A MACHINE THAT HAS DATA, the patterns have
    drifted from the layout and every other test in this file is passing over
    an empty set. A fresh clone has no `data/` and no `*.local.yaml`, so it
    has nothing to find and this says so rather than failing; the pattern
    contract itself is exercised against `fake_repo` above on every machine."""
    if not (REPO_ROOT / "data").exists() and not list((REPO_ROOT / "config").glob("*.local.yaml")):
        pytest.skip("this checkout holds no operational data (a fresh clone)")
    found = {path.name for path in protected_files()}

    assert any(name.endswith(".db") for name in found) or any(
        name.endswith(".local.yaml") for name in found
    ), found
