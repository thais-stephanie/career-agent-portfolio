"""Which evidence a ResumeDocument cites that this profile has NOT confirmed.

A line's `evidence_ids` are Career Evidence claim keys. They are grounding
only while each one names a claim of THIS profile's candidate whose current
revision (not superseded) is confirmed. The browser is never trusted with
that: every write and every export asks again here, because a claim can be
retired after a document cited it.

Typed and imported lines may cite nothing. Any id a line does cite is
checked, whatever its origin, so an edited evidence line keeps its ids only
while they still hold. A project, education or certification entry's
`claim_key` is a claim key too (the Master sets it from a confirmed claim),
checked the same way under the entry's id.

`ResumeStore` asks this on every create, save and checkpoint; export asks it
before making a file. It is the one place the rule is written.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from career_agent.resume_doc.models import (
    CertificationEntry,
    EducationEntry,
    ProjectEntry,
    ResumeDocument,
    TextBlock,
)
from career_agent.storage.workspace_repo import candidate_id_of


def _cited(doc: ResumeDocument) -> Iterator[tuple[str, list[str]]]:
    """(line id, evidence ids) for every line that can cite evidence."""
    blocks: list[TextBlock] = [b for b in (doc.headline, doc.summary) if b is not None]
    for entries in (doc.experience, doc.projects, doc.education):
        for entry in entries:
            blocks += entry.bullets
    for custom in doc.custom_sections:
        blocks += custom.items
    for block in blocks:
        yield block.id, block.evidence_ids
    for group in doc.skills:
        for item in group.items:
            yield item.id, item.evidence_ids
    keyed: list[ProjectEntry | EducationEntry | CertificationEntry] = [
        *doc.projects,
        *doc.education,
        *doc.certifications,
    ]
    for one in keyed:
        if one.claim_key is not None:
            yield one.id, [one.claim_key]


def unconfirmed_lines(conn: sqlite3.Connection, doc: ResumeDocument) -> list[str]:
    """The ids of the lines citing anything that is not a confirmed, current
    claim of this profile's candidate. Empty when every citation holds."""
    cited = list(_cited(doc))
    keys = {key for _, ids in cited for key in ids}
    if not keys:
        return []
    candidate = candidate_id_of(conn)
    confirmed: set[str] = set()
    if candidate:
        marks = ",".join("?" * len(keys))
        confirmed = {
            row[0]
            for row in conn.execute(
                "SELECT claim_key FROM verified_claim WHERE candidate_id = ?"
                f" AND superseded_by_id IS NULL AND verified = 1 AND claim_key IN ({marks})",
                (candidate, *keys),
            )
        }
    return [line for line, ids in cited if any(key not in confirmed for key in ids)]
