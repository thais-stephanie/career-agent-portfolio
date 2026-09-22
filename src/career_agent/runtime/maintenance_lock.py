"""An OS-owned lock, released even if a maintenance process is killed.

The empty sidecar is not a running marker. Only the operating system lock is.
Legacy collector invocations do not participate; operators must not overlap them.
"""

import sys
from contextlib import contextmanager
from pathlib import Path


def maintenance_running(db: Path) -> bool:
    """Read the OS lock, not the age of a possibly abandoned run row."""
    path = db.resolve().with_suffix(db.suffix + ".maintenance.lock")
    if not path.exists():
        return False
    with path.open("rb") as handle:
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBRLCK, 1)
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            return True
    return False


@contextmanager
def maintenance_lock(db: Path):
    path = db.resolve().with_suffix(db.suffix + ".maintenance.lock")
    with path.open("a+b") as handle:
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Source maintenance is already running for this database.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
