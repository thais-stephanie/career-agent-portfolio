"""Settings, Backup: what the app itself keeps, read only.

Lists the automatic backups of storage/auto_backup.py (count, size, date, the
upgrade each protects) and states the policy. It lists nothing else in
`data/backups`: manual backups, exports and older files are not this module's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from career_agent.storage.auto_backup import listing
from career_agent.web.server import ApiError

if TYPE_CHECKING:
    from career_agent.web.api import JobsApi


def register_backups(app: JobsApi) -> None:
    def data_dir() -> Any:
        host = getattr(app, "profile_host", None)
        return host.root / "data" if host is not None else app.config.db_path.parent

    def read(*, query: dict, body: dict) -> dict:
        if query or body:
            raise ApiError(400, "Reading the backups takes no parameters.")
        return listing(data_dir())

    app.register("GET", r"/api/backups", read)
