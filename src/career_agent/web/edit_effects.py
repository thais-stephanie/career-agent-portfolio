"""What an edit of the profile changes, decided by what it really changed.

A save used to answer in one of two ways: nothing, or "recalculate". Neither
is the truth for most edits:

* skills, history and evidence are read by the Deep reading (when the person
  shared evidence with that provider) and by resume preparation, never by the
  local score, so they make the affected Deep readings out of date and need no
  recalculation and no new search;
* preferences the matcher reads (`config_version` moved) make the stored local
  scores out of date, and only those;
* the words and markets a search is built from (`discovery.plan.query_terms`,
  `regions.target`) decide what the NEXT search looks for, so the person is
  offered a search, never given one;
* anything else (names, display choices, organisation) changes nothing.

The effects are computed by comparing two small snapshots around the write,
using the same digests the stale checks already use, so this module has no
opinion of its own about what matters. It reads; it never starts a collection,
a recalculation or a model call.
"""

from __future__ import annotations

import re
from contextlib import closing
from dataclasses import dataclass
from typing import Any

#: The writes that can move any of the three effects. Everything else is
#: dispatched without a snapshot.
_EDIT_ROUTES = (
    ("PATCH", re.compile(r"^/api/profile$")),
    ("PATCH", re.compile(r"^/api/preferences$")),
    ("PATCH", re.compile(r"^/api/search-review$")),
    ("POST", re.compile(r"^/api/first-search$")),
    ("PATCH", re.compile(r"^/api/role-anchors$")),
    ("POST", re.compile(r"^/api/evidence$")),
    ("PATCH", re.compile(r"^/api/evidence/[^/]+$")),
    ("POST", re.compile(r"^/api/evidence/[^/]+/(retire|confirm)$")),
    ("POST", re.compile(r"^/api/cv/imports/[^/]+/decide$")),
)


def is_edit(method: str, path: str) -> bool:
    return any(m == method and pattern.match(path) for m, pattern in _EDIT_ROUTES)


@dataclass(frozen=True)
class Snapshot:
    config_version: int
    #: What the next search is built from.
    discovery: tuple[Any, ...]
    #: Postings whose newest Deep reading answers the profile as it is now.
    deep_current: frozenset[str]


def snapshot(app: Any) -> Snapshot:
    from career_agent.discovery.anchors import load_anchors
    from career_agent.discovery.plan import query_terms
    from career_agent.semantic.deep_run import context

    config = app.search_config()
    terms = tuple(
        sorted(t.text.casefold() for t in query_terms(config, load_anchors(app.config.config_dir)))
    )
    regions = tuple(sorted(str(r) for r in config.regions.target))
    with closing(app.connect()) as conn:
        digests = {share: context(conn, config, share).digest for share in (False, True)}
        rows = conn.execute(
            "SELECT job_id, profile_digest, json_extract(sent_json, '$.shared') AS shared"
            "  FROM deep_review r WHERE created_at = ("
            "   SELECT max(created_at) FROM deep_review x WHERE x.job_id = r.job_id)"
        ).fetchall()
    current = frozenset(
        str(r["job_id"]) for r in rows if str(r["profile_digest"]) == digests[bool(r["shared"])]
    )
    return Snapshot(int(app._identity()[1]), (terms, regions), current)


def effects(before: Snapshot, after: Snapshot) -> dict[str, Any]:
    """The consequences, as data the screen words in its own language."""
    return {
        # Stored local scores answer older preferences.
        "needs_rescore": after.config_version != before.config_version,
        # A search built now would look for something different.
        "offer_discovery": after.discovery != before.discovery,
        # Deep readings that were current and no longer are.
        "deep_stale": len(before.deep_current - after.deep_current),
    }
