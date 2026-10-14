"""`busy_elsewhere` sees a collection lock held by ANOTHER process, and the
probe never takes the lock away from this process's own collection."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from career_agent.storage.catalogue import CatalogueWriteLock

HOLD = """
import sys, time
from pathlib import Path
from career_agent.storage.catalogue import CatalogueWriteLock
lock = CatalogueWriteLock.for_catalogue(Path(sys.argv[1]))
lock.acquire("other-process")
print("held", flush=True)
sys.stdin.readline()
lock.release("other-process")
"""


def test_a_lock_held_by_another_process_is_seen_and_released_is_not(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.db"
    lock = CatalogueWriteLock.for_catalogue(catalogue)
    assert lock.busy_elsewhere() is False
    other = subprocess.Popen(
        [sys.executable, "-c", HOLD, str(catalogue)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert other.stdout is not None and other.stdout.readline().strip() == "held"
        assert lock.busy_elsewhere() is True
    finally:
        other.communicate("\n", timeout=30)
    assert lock.busy_elsewhere() is False
    # The probe left nothing held: this process can still collect.
    lock.acquire("mine")
    try:
        assert lock.held and lock.busy_elsewhere() is False
    finally:
        lock.release("mine")
