"""A versioned, read-only export of the Jobs list, for programs that run while the app is closed.

**THE SAME ANSWER AS THE SCREEN.** Every item is produced by `JobsApi.list_jobs`, the
handler behind `GET /api/jobs`, so a score, a band, an eligibility reading or a deep
level in the file is by construction the one the interface shows. Nothing here scores,
ranks or decides; it pages through that answer and writes it down.

**READ-ONLY, AND IT SAYS SO TO SQLITE.** The app's connections are replaced by
`open_read_only` (`mode=ro`) connections, so a stray write fails loudly instead of
changing the database. Pending migrations are refused, never applied.

**COMPLETE OR ABSENT.** Items are streamed to a temporary file beside the target and
renamed into place only after the last page, so an interrupted export never looks like a
finished one. If the list moved while it was being read (a collection or a rescore
committed between pages), the export is refused: pages from two different moments are
not one list.

Private fields stay out: the person's notes, and the advert excerpt (the advert itself
is the employer's text and the reader can follow the link).
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

FORMAT = "career-agent-jobs-export"
SCHEMA_VERSION = 1
PAGE_SIZE = 500
#: The statuses that mean "I did something with this posting". Always exported, whatever
#: the list's filters hide, so a reader never loses a job the person is tracking.
TRACKED_STATUSES = ("SHORTLISTED", "TO_APPLY", "APPLIED", "INTERVIEW", "OFFER")
#: Never written: the person's own notes, and the advert excerpt.
EXCLUDED_FIELDS = frozenset({"notes", "description_excerpt"})


class ExportRefused(Exception):
    """The export could not be made complete. `code` is the process exit status."""

    def __init__(self, code: int, reason: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason


def _read_only(path: Path) -> sqlite3.Connection:
    """`mode=ro` on the database and its catalogue: no write reaches either file. Not
    `query_only`, which also forbids the TEMP table the deep order builds per connection
    (`ScoredJobQuery._deep_table`), and TEMP never touches the database file."""
    from career_agent.storage.catalogue import open_read_only

    conn = open_read_only(path)
    conn.row_factory = sqlite3.Row
    return conn


def preflight(db_path: Path) -> None:
    """Refuse a database this export must not read: missing, not personal, or not migrated."""
    from career_agent.runtime.mode import RuntimeMode, read_identity
    from career_agent.storage.db import MigrationError, pending_migrations

    if not db_path.exists():
        raise ExportRefused(2, "NO_DATABASE", f"No database at {db_path}.")
    conn = _read_only(db_path)
    try:
        identity = read_identity(conn)
        if identity is None or identity.kind is not RuntimeMode.PERSONAL:
            raise ExportRefused(2, "NOT_PERSONAL", "The export reads a personal database only.")
        try:
            pending = pending_migrations(conn)
        except MigrationError as exc:
            raise ExportRefused(2, "MIGRATION_LINEAGE", str(exc)) from exc
        if pending:
            raise ExportRefused(
                2,
                "MIGRATIONS_PENDING",
                f"{len(pending)} database migrations are pending; export never migrates. "
                "Open Career Agent once to apply them.",
            )
    finally:
        conn.close()


def check_profile_pair(db_path: Path, config_dir: Path) -> None:
    """`cli_local._check_profile_pair`, on a read-only connection: a database stamped for
    one local profile is exported only with that profile's own settings folder."""
    from career_agent.runtime.profiles import ProfileError, bound_profile, load_registry

    try:
        registry = load_registry(Path.cwd())
    except ProfileError:
        return
    if registry is None or not db_path.exists():
        return
    conn = _read_only(db_path)
    try:
        bound = bound_profile(conn)
    finally:
        conn.close()
    owner = next((p for p in registry.profiles if p.id == bound), None)
    if owner is not None and config_dir.resolve() != (Path.cwd() / owner.config_dir).resolve():
        raise ExportRefused(
            2,
            "WRONG_PROFILE_SETTINGS",
            f"This database belongs to the local profile {owner.label!r}; "
            f"use --config-dir {owner.config_dir}.",
        )


def _app(db_path: Path, config_dir: Path) -> Any:
    from career_agent.web.api import JobsApi
    from career_agent.web.server import ServerConfig

    class ReadOnlyJobsApi(JobsApi):
        def connect(self) -> sqlite3.Connection:  # type: ignore[override]
            return _read_only(self.config.db_path)

    return ReadOnlyJobsApi(ServerConfig(db_path=db_path, config_dir=config_dir, port=0), quiet=True)


def _pages(app: Any, base: dict[str, list[str]], page_size: int) -> Iterator[dict]:
    offset = 0
    while True:
        page = app.list_jobs(
            query={**base, "limit": [str(page_size)], "offset": [str(offset)]}, body={}
        )
        yield page
        offset += page_size
        if not page["items"] or offset >= page["total"]:
            return


def _card(item: dict, scope: str) -> dict:
    card = {k: v for k, v in item.items() if k not in EXCLUDED_FIELDS}
    card["export_scope"] = scope
    return card


@contextmanager
def _atomic(out: Path) -> Iterator[TextIO]:
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_name(f".{out.name}.{os.getpid()}.partial")
    handle = temp.open("w", encoding="utf-8", newline="\n")
    try:
        yield handle
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(temp, out)
    except BaseException:
        handle.close()
        temp.unlink(missing_ok=True)
        raise


def export_jobs(
    db_path: Path,
    config_dir: Path,
    out: Path,
    *,
    include_unresolved: bool = False,
    page_size: int = PAGE_SIZE,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Write the export to `out` and return its summary. Raises ExportRefused."""
    from career_agent import __version__, install_id

    preflight(db_path)
    app = _app(db_path, config_dir)
    generated_at = now().isoformat(timespec="seconds")
    base = {"lane": ["shortlist"], "sort": ["deep"], "narrowings": ["later"]}
    if include_unresolved:
        base["include_unresolved"] = ["true"]
    tracked = {"lane": ["all"], "narrowings": ["later"], "status": list(TRACKED_STATUSES)}

    seen: set[str] = set()
    first: dict | None = None
    counts = {"default_list": 0, "tracked": 0}
    rescoring = False
    with _atomic(out) as handle:
        handle.write("{\n")
        handle.write(f'"format": {json.dumps(FORMAT)},\n"schema_version": {SCHEMA_VERSION},\n')
        handle.write('"items": [\n')
        for scope, query in (("default_list", base), ("tracked", tracked)):
            for page in _pages(app, query, page_size):
                if first is None:
                    first = page
                if page["freshness"] != first["freshness"]:
                    raise ExportRefused(
                        4,
                        "LIST_MOVED",
                        "The list changed while it was being exported (a collection or a "
                        "rescore committed). Nothing was written; run the export again.",
                    )
                rescoring = rescoring or bool(page["revision"].get("is_building"))
                if scope == "default_list" and page["total"] != first["total"]:
                    raise ExportRefused(4, "LIST_MOVED", "The list total changed between pages.")
                for item in page["items"]:
                    if item["job_id"] in seen:
                        continue
                    handle.write(("," if seen else "") + json.dumps(_card(item, scope)) + "\n")
                    seen.add(item["job_id"])
                    counts[scope] += 1
        assert first is not None  # the first page always exists
        if counts["default_list"] != first["total"]:
            raise ExportRefused(
                4,
                "INCOMPLETE",
                f"Read {counts['default_list']} of {first['total']} postings in the list.",
            )
        revision = first["revision"]
        summary = {
            "generated_at": generated_at,
            "career_agent": {"version": __version__, "install": install_id()},
            "database": {"mode": "PERSONAL", "freshness": first["freshness"]},
            "scoring": {
                # Which scored population the items answer: `serving`, which can be the
                # previous preferences for the minutes after an edit (`is_current` false).
                "serving": revision.get("serving"),
                "is_current": revision.get("is_current"),
                "rescoring": rescoring,
                "authority": "Career Agent scored every item; this file only copies it.",
            },
            "scope": {
                "lane": "shortlist",
                "sort": "deep",
                "include_unresolved": include_unresolved,
                "tracked_statuses": list(TRACKED_STATUSES),
                "excluded": [
                    "postings the default list hides (explicitly ineligible, off target, "
                    "hidden by you, outside your age, level or work-model limits)",
                    *(
                        []
                        if include_unresolved
                        else ["postings that do not say where they hire (UNRESOLVED)"]
                    ),
                    "unless their application status is one of tracked_statuses",
                ],
                "excluded_fields": sorted(EXCLUDED_FIELDS),
            },
            "completeness": {
                "complete": True,
                "default_list_total": first["total"],
                "default_list_count": counts["default_list"],
                "tracked_extra_count": counts["tracked"],
                "item_count": len(seen),
            },
        }
        handle.write("],\n")
        for i, (key, value) in enumerate(summary.items()):
            handle.write(f"{json.dumps(key)}: {json.dumps(value)}")
            handle.write(",\n" if i < len(summary) - 1 else "\n")
        handle.write("}\n")
    return summary
