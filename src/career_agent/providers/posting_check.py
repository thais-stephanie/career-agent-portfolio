"""Where a posting page's own vendor answers whether the posting is still open.

Kept here because naming vendors is this package's job; `availability` asks.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: Hosts never asked: LinkedIn's terms forbid automated access.
EXCLUDED_HOSTS = ("linkedin.com",)


def api_url(url: str) -> str | None:
    """The ATS's official posting endpoint for a posting page, when there is one."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    if host in {"job-boards.greenhouse.io", "boards.greenhouse.io"} and "jobs" in segments:
        at = segments.index("jobs")
        if at >= 1 and len(segments) > at + 1 and segments[at + 1].isdigit():
            board, job = segments[at - 1], segments[at + 1]
            return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}"
    if host == "jobs.lever.co" and len(segments) >= 2:
        return f"https://api.lever.co/v0/postings/{segments[0]}/{segments[1]}"
    return None
