"""Workable's public XML feed as ONE shared, resumable, stoppable update.

Why this is not simply `collect-workable`
-----------------------------------------
The feed is a single file of about 800 MiB that Workable regenerates hourly and
will not filter. Reading it on opening the app has to satisfy things the full
pass never had to:

* it runs once for the whole installation, however many profiles or tabs ask
  (an operating-system lock beside the catalogue, plus the catalogue's own
  record of when the feed was last confirmed);
* it asks Workable "has it changed?" first. Workable publishes `Last-Modified`
  and no ETag (measured 2026-10-11), and answers 304 to `If-Modified-Since`;
* it leaves postings the feed repeats unchanged alone, so a re-read writes and
  scores only what is new or different;
* it can be stopped, and a read cut short keeps its place (see
  `providers/workable_feed.py`) and says so: it is a pending update, never a
  finished one, and it never closes a posting the feed stopped listing;
* it refuses to start without room on disk for the download and its staging.

It reads no model and recalculates nothing; the postings it wrote are queued for
the ordinary targeted score by the database triggers, like any collection.
"""

from __future__ import annotations

import shutil
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from career_agent.domain.enums import PipelineRunStatus
from career_agent.pipeline.workable_collect import WorkableCollector, WorkableStats
from career_agent.providers.workable_feed import FeedSession
from career_agent.runtime.maintenance_lock import maintenance_lock
from career_agent.storage.catalogue import catalogue_path
from career_agent.storage.db import transaction
from career_agent.storage.repositories import PipelineRunRepo

#: The download (~0.8 GiB) and its staging copy (about as large) coexist, plus
#: room to grow: do not start a read the disk cannot hold.
MIN_FREE_BYTES = 3 * 1024**3


def feed_directory(conn: Any) -> Path:
    """`data/shared/workable`, beside the catalogue every profile shares."""
    main = next(
        (r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main" and r[2]), None
    )
    base = catalogue_path(Path(main)).parent if main else Path.cwd() / "shared"
    return base / "workable"


class WorkableUpdate:
    def __init__(
        self,
        conn: Any,
        fetcher: Any,
        *,
        directory: Path | None = None,
        stop_reason: Any = None,
        progress: Any = None,
        fresh_hours: float | None = None,
        force: bool = False,
        free_bytes: Any = None,
    ) -> None:
        self.conn = conn
        self.fetcher = fetcher
        self.directory = directory
        self.stop_reason = stop_reason
        self.progress = progress
        self.fresh_hours = fresh_hours
        self.force = force
        self._free = free_bytes or (lambda path: shutil.disk_usage(path).free)

    def collect(self) -> WorkableStats:
        session = FeedSession(self.directory or feed_directory(self.conn))
        with ExitStack() as stack:
            try:
                stack.enter_context(maintenance_lock(session.dir / "feed.lock"))
            except RuntimeError:
                # Another profile, tab or window is reading it right now.
                busy = WorkableStats()
                busy.deferred_reason = "ALREADY_RUNNING"
                return busy
            pending = session.pending()
            if not self.force and not pending and self.fresh_hours is not None:
                checked = session.checked_at()
                if checked and datetime.now(UTC) - checked < timedelta(hours=self.fresh_hours):
                    return self._receipt(None, shared_at=checked)
            have_files = bool(
                pending and (session.xml_path.exists() or session.stage_path.exists())
            )
            if not have_files and self._free(session.dir) < MIN_FREE_BYTES:
                return self._receipt("NOT_ENOUGH_DISK")
            return WorkableCollector(
                self.conn,
                self.fetcher,
                session=session,
                skip_unchanged=True,
                stop_reason=self.stop_reason,
                progress=self.progress,
            ).collect()

    def _receipt(self, deferred: str | None, shared_at: datetime | None = None) -> WorkableStats:
        """A run row for a read that did not need to (or could not) happen."""
        stats = WorkableStats()
        stats.deferred_reason = deferred
        stats.elapsed_ms = 1
        data = stats.as_dict()
        if shared_at is not None:
            data["shared_at"] = shared_at.isoformat(timespec="seconds")
        runs = PipelineRunRepo(self.conn)
        with transaction(self.conn):
            run_id = runs.start("collect-workable")
            runs.finish(run_id, PipelineRunStatus.OK, stats=data, error=None)
        return stats
