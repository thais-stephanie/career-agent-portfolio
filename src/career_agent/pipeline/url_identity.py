"""Rule 3 of posting identity: a job ANOTHER provider stored at the same URL.

Every feed collector asked this once per posting with
`SELECT id, url FROM job WHERE provider != ? AND url LIKE '%<host>%'`. A LIKE
with a leading wildcard can use no index, so each posting read the whole job
table: 4,901 scans of 218,036 rows, 1,003 of the 1,482 seconds of a Gupy run
(profiled 2026-10-20). The answer cannot change during a run, because a
collector writes only rows of its own provider and those are excluded, so it
is read once per site and kept for the run.

`site` is the host's last two labels (`acme.gupy.io` -> `gupy.io`): a superset
of the old `%<host>%` candidates. Equality of `canonical_posting_url`, which
keeps the lowercased host, still decides, so the jobs found are the same; the
first one in the same query's order wins, as before.
"""

from __future__ import annotations

import sqlite3
from urllib.parse import urlsplit

from career_agent.providers.registry import canonical_posting_url


class OtherProviderUrls:
    """One collector run's view of the URLs other providers hold.

    ponytail: a row another PROCESS writes during the run is not seen; the old
    per-posting scan saw it. Refreshes run one source at a time.
    """

    def __init__(self, conn: sqlite3.Connection, provider: str) -> None:
        self.conn = conn
        self.provider = provider
        self._sites: dict[str, dict[str, str]] = {}

    def find(self, url: str) -> str | None:
        target = canonical_posting_url(url)
        host = urlsplit(url).hostname or ""
        if not target or not host:
            return None
        site = ".".join(host.split(".")[-2:])
        index = self._sites.get(site)
        if index is None:
            index = {}
            for job_id, stored in self.conn.execute(
                "SELECT id, url FROM job WHERE provider != ? AND url LIKE ?",
                (self.provider, f"%{site}%"),
            ):
                index.setdefault(canonical_posting_url(str(stored)), str(job_id))
            self._sites[site] = index
        return index.get(target)
