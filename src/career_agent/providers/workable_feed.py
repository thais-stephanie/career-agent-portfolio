"""Shared, resumable state for the Workable XML feed: one copy per installation.

The feed is one public file (about 800 MiB on 2026-10-11, regenerated hourly, no
filters). Every profile on this computer reads the same catalogue, so the file is
downloaded and read ONCE for all of them, and what is left over between two runs
lives here, beside the catalogue, not in any profile:

    data/shared/workable/state.json     what was fully processed, what is pending
    data/shared/workable/feed.xml       the download, only until it has been staged
    data/shared/workable/stage.sqlite   the feed read once, in order, with a cursor

`processed_last_modified` is the server's own `Last-Modified` of the last feed
that was read to its end. It is the only validator Workable publishes (there is
no ETag, and Range requests are ignored), so a later run sends it back as
`If-Modified-Since` and a 304 means "nothing changed since the last full read".

A pending entry means a read was cut short (time budget, Stop, closed app). The
next run continues from its cursor without downloading again. Nothing in here is
ever treated as a completed update until `finish` is called, and nothing here
closes a posting the feed no longer lists.
"""

from __future__ import annotations

import json
import os
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

#: A leftover read older than this is abandoned: the feed it came from is long
#: replaced, and well over a gigabyte of disk is not worth holding for it.
PENDING_MAX_AGE = timedelta(days=7)


class FeedInterrupted(Exception):
    """The read was asked to stop (Stop pressed, time budget). Not a failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FeedSession:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.xml_path = self.dir / "feed.xml"
        self.stage_path = self.dir / "stage.sqlite"
        self.state_path = self.dir / "state.json"

    def read(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def write(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, self.state_path)

    def pending(self) -> dict[str, Any] | None:
        pending = self.read().get("pending")
        if not isinstance(pending, dict):
            return None
        started = _parse(pending.get("started_at"))
        if started and datetime.now(UTC) - started > PENDING_MAX_AGE:
            self.abandon()
            return None
        return pending

    def start_pending(self, **fields: Any) -> dict[str, Any]:
        state = self.read()
        pending = {"started_at": _stamp(), "staged": False, "cursor": 0, **fields}
        state["pending"] = pending
        self.write(state)
        return pending

    def update_pending(self, **fields: Any) -> None:
        state = self.read()
        if isinstance(state.get("pending"), dict):
            state["pending"].update(fields)
            self.write(state)

    def checked_at(self) -> datetime | None:
        return _parse(self.read().get("checked_at"))

    def processed_last_modified(self) -> str | None:
        value = self.read().get("processed_last_modified")
        return value if isinstance(value, str) else None

    def finish(self, last_modified: str | None) -> None:
        """The feed was read to its end: only now is it an update."""
        state = self.read()
        state.pop("pending", None)
        state["processed_last_modified"] = last_modified
        state["checked_at"] = _stamp()
        self.write(state)
        self.discard_files()

    def confirm_unchanged(self) -> None:
        """A 304: the server confirms the last full read is still the feed."""
        state = self.read()
        state["checked_at"] = _stamp()
        self.write(state)

    def abandon(self) -> None:
        state = self.read()
        state.pop("pending", None)
        self.write(state)
        self.discard_files()

    def discard_files(self) -> None:
        for path in (self.xml_path, self.stage_path):
            with suppress(OSError):
                path.unlink(missing_ok=True)


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
