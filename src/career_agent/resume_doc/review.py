"""Before a resume leaves this computer: what the person must look at first.

Recomputed from the document as SAVED, never stored. An item is

* a line linked to confirmed evidence that its evidence does not hold as
  worded (the Analyze reading, `analyze._evidence`, so the review and Analyze
  never disagree), or whose evidence was reworded since the line was made:
  kind NOT_AS_WORDED or EVIDENCE_CHANGED, with the findings that say why;
* a line, a skill or an entry the person wrote (WRITTEN) or imported from a
  file (IMPORTED): no confirmed evidence supports it, which is not to say it
  is wrong.

For each the person edits it, puts back the evidence's wording (a line with
an `original_text`), or acknowledges it as their own statement. An
acknowledgement is recorded apart, in `resume_statement_acknowledgement`:
the line keeps its origin, its evidence ids and its history, and nothing
calls it supported. It belongs to the STATEMENT (`statement_sha256`), not to
one document, so an unchanged statement is not asked about again on every
export or in another version OF THIS PROFILE (each profile has its own
database). A statement is its words folded, plus:

* its CONTEXT: the role (its Career Profile experience, or employer and title),
  project, education or section it sits in. The same words under another
  role are another claim;
* what it rests on: origin, the evidence ids it cites, the evidence's wording
  it replaced, and the CURRENT wording of that evidence. Evidence revised
  after the acknowledgement makes it a new statement.

Nothing here says a statement is true. An acknowledgement says who stands
behind it. `export.export_revision` refuses a file while any item waits.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from career_agent.clock import new_id, now_utc
from career_agent.resume_doc import jd
from career_agent.resume_doc.models import (
    EVIDENCED_ORIGINS,
    Origin,
    PartialDate,
    ResumeDocument,
    TextBlock,
)
from career_agent.resume_doc.store import ResumeStore, StaleDocument

NOT_AS_WORDED, EVIDENCE_CHANGED, WRITTEN, IMPORTED = (
    "NOT_AS_WORDED", "EVIDENCE_CHANGED", "WRITTEN", "IMPORTED",
)  # fmt: skip
#: The Analyze findings that make an evidence line an item.
_WORDING = frozenset(
    {
        "UNSUPPORTED_NUMBER", "UNSUPPORTED_TOOL", "UNSUPPORTED_SENIORITY",
        "UNSUPPORTED_RESPONSIBILITY", "UNSUPPORTED_SCOPE", "UNSUPPORTED_CAUSALITY",
        "UNSUPPORTED_CLAIM", "UNSUPPORTED_WORDING", "EVIDENCE_CHANGED",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Item:
    ref: str
    section: str
    kind: str
    text: str
    #: The Analyze findings behind a NOT_AS_WORDED item, in order.
    reasons: tuple[str, ...]
    #: The evidence's wording, for "put it back"; None when there is none.
    original_text: str | None
    evidence_ids: tuple[str, ...]
    statement_sha256: str
    acknowledged: bool

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["reasons"], out["evidence_ids"] = list(self.reasons), list(self.evidence_ids)
        return out


class ReviewRequired(Exception):
    """Items wait for a decision; nothing was exported."""

    def __init__(self, waiting: list[Item]) -> None:
        super().__init__(f"{len(waiting)} statement(s) wait for a decision")
        self.items = waiting


class NothingToAcknowledge(ValueError):
    """`ref` is not an item of this document as saved."""


def material(text: str) -> str:
    """What a statement SAYS: folded (no case, no accents), spacing collapsed,
    the final full stop dropped. A changed word or number is a change."""
    return " ".join(jd.folded(text).split()).strip(" .;")


def statement_sha256(text: str, basis: str) -> str:
    return hashlib.sha256(f"{basis}\n{material(text)}".encode()).hexdigest()


def _date(value: PartialDate | None) -> str:
    if value is None:
        return ""
    return f"{value.year}-{value.month:02d}" if value.month else str(value.year)


class _Collect:
    """Items in page order, before their acknowledgement is looked up."""

    def __init__(self, reasons: dict[str, list[str]], claims: dict[str, str]) -> None:
        self.reasons = reasons
        #: The CURRENT wording of every confirmed claim, by key.
        self.claims = claims
        self.raw: list[dict[str, Any]] = []

    def line(self, section: str, block: TextBlock | None, shown: bool, context: str) -> None:
        if block is None or not shown or not block.text.strip():
            return
        if block.origin in EVIDENCED_ORIGINS:
            said = self.reasons.get(block.id, [])
            if not said:
                return
            kind = EVIDENCE_CHANGED if said == ["EVIDENCE_CHANGED"] else NOT_AS_WORDED
            ids = sorted(block.evidence_ids)
            evidence = hashlib.sha256(
                "\n".join(material(self.claims.get(k, "")) for k in ids).encode()
            ).hexdigest()
            basis = "|".join([block.origin.value, ",".join(ids),
                              material(block.original_text or ""), evidence, context])  # fmt: skip
        else:
            kind = IMPORTED if block.origin is Origin.IMPORTED else WRITTEN
            said, basis = [], f"{kind}|{context}"
        self._add(block.id, section, kind, block.text, tuple(said), block.original_text,
                  tuple(block.evidence_ids), basis)  # fmt: skip

    def typed(self, ref: str, section: str, origin: Origin, text: str) -> None:
        kind = IMPORTED if origin is Origin.IMPORTED else WRITTEN
        self._add(ref, section, kind, text, (), None, (), f"{kind}:{section}")

    def _add(self, ref: str, section: str, kind: str, text: str, reasons: tuple[str, ...],
             original: str | None, ids: tuple[str, ...], basis: str) -> None:  # fmt: skip
        self.raw.append({
            "ref": ref, "section": section, "kind": kind, "text": text, "reasons": reasons,
            "original_text": original, "evidence_ids": ids,
            "statement_sha256": statement_sha256(text, basis),
        })  # fmt: skip


def items(conn: sqlite3.Connection, doc: ResumeDocument) -> list[Item]:
    """Every item of `doc` in page order, each marked acknowledged or not."""
    # Imported here: `analyze` reads the export module, which reads this one.
    from career_agent.resume_doc.analyze import _evidence
    from career_agent.resume_doc.tailor import _career

    found, _ = _evidence(conn, doc)
    _, rows = _career(conn)
    claims = {r["claim_key"]: r["text"] for r in rows if r["state"] == "CONFIRMED"}
    reasons: dict[str, list[str]] = {}
    for f in found:
        if f["kind"] in _WORDING:
            reasons.setdefault(f["ref"], []).append(f["kind"])
    hidden = set(doc.layout.hidden_sections)
    out = _Collect(reasons, claims)
    for name in ("headline", "summary"):
        out.line(name, getattr(doc, name), name not in hidden, name)
    labels = {
        "experience": lambda e: [e.display_title, e.employer, e.location],
        "projects": lambda e: [e.name, e.role],
        "education": lambda e: [e.institution, e.degree, e.field_of_study],
    }
    #: What a line inside an entry is said OF: its linked record, else the
    #: entry's own identifying words.
    contexts = {
        "experience": lambda e: e.experience_id or material(f"{e.employer} {e.display_title}"),
        "projects": lambda e: e.claim_key or material(e.name),
        "education": lambda e: e.claim_key or material(e.institution),
    }
    for section, label in labels.items():
        for entry in getattr(doc, section):
            if entry.hidden or section in hidden:
                continue
            linked = entry.experience_id if section == "experience" else entry.claim_key
            if not linked:
                dates = "-".join(filter(None, [_date(entry.start), _date(entry.end)]))
                text = " · ".join(filter(None, [*label(entry), dates]))
                out.typed(entry.id, section, Origin.USER_AUTHORED, text)
            context = f"{section}:{contexts[section](entry)}"
            for b in entry.bullets:
                out.line(section, b, not b.hidden, context)
    for cert in doc.certifications:
        if not (cert.hidden or "certifications" in hidden or cert.claim_key):
            text = " · ".join(filter(None, [cert.name, cert.issuer, _date(cert.issued)]))
            out.typed(cert.id, "certifications", Origin.USER_AUTHORED, text)
    for group in doc.skills:
        if group.hidden or "skills" in hidden:
            continue
        for skill in group.items:
            if skill.origin not in EVIDENCED_ORIGINS:
                out.typed(skill.id, "skills", skill.origin, skill.label)
    for custom in doc.custom_sections:
        off = custom.hidden or f"custom:{custom.id}" in hidden
        for b in custom.items:
            out.line("custom", b, not (b.hidden or off), f"custom:{material(custom.heading)}")
    held = _acknowledged(conn, [r["statement_sha256"] for r in out.raw])
    return [Item(**r, acknowledged=r["statement_sha256"] in held) for r in out.raw]


def _acknowledged(conn: sqlite3.Connection, digests: list[str]) -> set[str]:
    if not digests:
        return set()
    marks = ",".join("?" * len(digests))
    rows = conn.execute(
        "SELECT statement_sha256 FROM resume_statement_acknowledgement"
        f" WHERE withdrawn_at IS NULL AND statement_sha256 IN ({marks})",
        digests,
    )
    return {r[0] for r in rows}


def pending(conn: sqlite3.Connection, doc: ResumeDocument) -> list[Item]:
    return [i for i in items(conn, doc) if not i.acknowledged]


def _saved_item(conn: sqlite3.Connection, document_id: str, ref: str, expected_sha256: str) -> Item:
    stored = ResumeStore(conn).get_document(document_id)
    if stored.working_sha256 != expected_sha256:
        raise StaleDocument(document_id, stored.working_sha256)
    for item in items(conn, stored.working):
        if item.ref == ref:
            return item
    raise NothingToAcknowledge(ref)


def acknowledge(
    conn: sqlite3.Connection,
    document_id: str,
    ref: str,
    *,
    expected_sha256: str,
    now: str | None = None,
) -> Item:
    """The person says the item `ref`, AS SAVED, is their own statement."""
    with ResumeStore(conn)._tx():
        item = _saved_item(conn, document_id, ref, expected_sha256)
        if not item.acknowledged:
            conn.execute(
                "INSERT INTO resume_statement_acknowledgement (id, statement_sha256,"
                " statement_text, document_id, item_ref, acknowledged_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (new_id(), item.statement_sha256, item.text, document_id, ref, now or now_utc()),
            )
    return _saved_item(conn, document_id, ref, expected_sha256)


def withdraw(
    conn: sqlite3.Connection,
    document_id: str,
    ref: str,
    *,
    expected_sha256: str,
    now: str | None = None,
) -> Item:
    """Take an acknowledgement back. The row stays, with when it was withdrawn."""
    with ResumeStore(conn)._tx():
        item = _saved_item(conn, document_id, ref, expected_sha256)
        conn.execute(
            "UPDATE resume_statement_acknowledgement SET withdrawn_at = ?"
            " WHERE statement_sha256 = ? AND withdrawn_at IS NULL",
            (now or now_utc(), item.statement_sha256),
        )
    return _saved_item(conn, document_id, ref, expected_sha256)
