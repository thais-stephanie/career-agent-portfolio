"""The candidate's own answers, versioned separately from the machinery.

WHAT THIS SEPARATES, AND WHY IT NEEDED SEPARATING
--------------------------------------------------
This product carries three version numbers and they answer three different
questions:

    MATCH_SCHEMA_VERSION   the CODE moved. A row written under 5 has no
                           `content_completeness` column filled in.
    config_version         the CONFIGURATION moved. Somebody changed a weight,
                           a phrase group, a threshold -- or an answer about
                           herself, because both live in one file.
    profile revision       THE PERSON'S ANSWERS moved. This one, and it did
                           not exist.

The third was missing and its absence was doing damage in one specific way:
`config_version` is the only number that changed when she told the product
where she lives, so "my scores are stale because the matcher changed" and "my
scores are stale because I answered a question" were the same number. A person
looking at a bumped version could not tell which had happened, and neither
could a later reader of the database.

`search_profile_version` has existed since migration 0001 and held zero rows
for the whole of M0 through V1.3. This fills it.

WHAT IS RECORDED, AND WHAT IS DELIBERATELY NOT
-----------------------------------------------
Only the CANDIDATE-OWNED half. `config/ownership.py` is the table that says
which facts those are, so this module asks it rather than keeping a second
list -- a second list is how the two drift, and the whole point of the
ownership table is that the answer lives in one place.

Nothing about the machinery is snapshotted: a weight, a lexicon phrase or a
threshold changing is not a new revision of a person. And nothing here writes
to `search.local.yaml`. This is a record of what she said, not a second writer
of where it lives.

WHAT THIS IS NOT
----------------
It is NOT the ownership migration. The matcher still reads `search.*.yaml` and
keeps reading it; repointing the gates at a profile file would invalidate every
score in the corpus and is an owner decision, stated in
`docs/architecture/candidate-ownership.md` and still open. What this does is
build the history that migration would need, from the answers that already
exist, so that when the decision is taken there is something to migrate rather
than a table with nothing in it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from career_agent.clock import new_id, now_utc
from career_agent.config.ownership import FACTS, Owner
from career_agent.storage.workspace_repo import ensure_candidate


@dataclass(frozen=True, slots=True)
class Revision:
    """One recorded state of the candidate's own answers."""

    #: 1 for the first ever recorded, counting up. Ordinal rather than stored:
    #: `search_profile_version` has no revision column and adding one would be
    #: a second source of truth for a number the rows already imply.
    number: int
    content_hash: str
    created_at: str
    #: Whether THIS call wrote it. False means the answers are unchanged since
    #: the last recording, which is the normal outcome and is not a failure.
    recorded: bool
    #: The answers themselves, as recorded.
    answers: dict[str, Any]


def candidate_answers(config_dir: Path) -> dict[str, Any]:
    """Every candidate-owned answer this build can read, by field id.

    Derived from `ownership.FACTS` rather than from `candidate_writer.FIELDS`,
    and the difference matters: the writer's table is what a SCREEN may change,
    and this is what BELONGS to her. They overlap almost entirely today, and a
    candidate fact that is not editable is still hers and still worth
    recording.
    """
    from career_agent.config.candidate_writer import current_candidate_fields

    current = current_candidate_fields(config_dir)
    return {
        fact.field: current.get(fact.field)
        for fact in FACTS
        if fact.owner is Owner.CANDIDATE and fact.field is not None
    }


def _digest(answers: dict[str, Any]) -> str:
    """A hash of the ANSWERS, not of the file that happens to hold them.

    Canonical JSON with sorted keys, so reformatting `search.local.yaml`,
    reordering its sections or editing a comment produces the same hash and no
    spurious revision. That is the same discipline `config_digest` follows --
    it hashes `model_dump`, never the file bytes -- and for the same reason: a
    version that moved when nothing about the meaning moved teaches people to
    ignore versions.
    """
    canonical = json.dumps(answers, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def latest(conn: sqlite3.Connection) -> Revision | None:
    """The most recently recorded revision, or None if none has been."""
    rows = conn.execute(
        "SELECT content_hash, yaml_snapshot, created_at FROM search_profile_version"
        " ORDER BY created_at, id"
    ).fetchall()
    if not rows:
        return None
    last = rows[-1]
    return Revision(
        number=len(rows),
        content_hash=str(last["content_hash"]),
        created_at=str(last["created_at"]),
        recorded=False,
        answers=json.loads(str(last["yaml_snapshot"])),
    )


def record(conn: sqlite3.Connection, config_dir: Path, *, now: str | None = None) -> Revision:
    """Record the current answers, if they differ from the last recording.

    IDEMPOTENT BY CONTENT. Running it twice with nothing changed records
    nothing and returns the existing revision with `recorded=False`. The
    `content_hash` column is UNIQUE, so even a race could not produce two rows
    for one state -- but the check is done in Python as well, because the
    caller wants to know whether anything happened and an ignored constraint
    violation would not tell it.
    """
    answers = candidate_answers(config_dir)
    digest = _digest(answers)
    existing = latest(conn)
    if existing is not None and existing.content_hash == digest:
        return existing

    candidate_id = ensure_candidate(conn)
    stamp = now or now_utc()
    conn.execute(
        "INSERT INTO search_profile_version"
        " (id, candidate_id, content_hash, yaml_snapshot, created_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT (content_hash) DO NOTHING",
        (
            new_id(),
            candidate_id,
            digest,
            # JSON, in a column named `yaml_snapshot`. The name is from
            # migration 0001, when the whole profile was a YAML file; the
            # column has always been "the serialised state", and rewriting a
            # column name to match a serialisation format would be a migration
            # that changes nothing anybody can observe.
            json.dumps(answers, sort_keys=True, ensure_ascii=False, default=str),
            stamp,
        ),
    )
    return Revision(
        number=(existing.number + 1) if existing else 1,
        content_hash=digest,
        created_at=stamp,
        recorded=True,
        answers=answers,
    )


def history(conn: sqlite3.Connection) -> list[Revision]:
    """Every recorded revision, oldest first."""
    rows = conn.execute(
        "SELECT content_hash, yaml_snapshot, created_at FROM search_profile_version"
        " ORDER BY created_at, id"
    ).fetchall()
    return [
        Revision(
            number=index,
            content_hash=str(row["content_hash"]),
            created_at=str(row["created_at"]),
            recorded=False,
            answers=json.loads(str(row["yaml_snapshot"])),
        )
        for index, row in enumerate(rows, start=1)
    ]


def changed_between(before: Revision, after: Revision) -> dict[str, tuple[Any, Any]]:
    """Which answers differ between two revisions, and what each said.

    Named both ways round on purpose. "Your profile changed" is not something
    anybody can act on; "where you live: BR -> PT" is.
    """
    fields = set(before.answers) | set(after.answers)
    return {
        field: (before.answers.get(field), after.answers.get(field))
        for field in sorted(fields)
        if before.answers.get(field) != after.answers.get(field)
    }
