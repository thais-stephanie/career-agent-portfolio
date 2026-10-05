"""Analyze (PR 11): what Career Agent can verify about one resume revision,
and, with a job, what it shows against what the job explicitly asks.

Deterministic, read-only and recomputed: nothing is stored but the
milestone the caller may take first, so a result is always about the exact
revision (and job ad snapshot) it names, under `VERSION`. It never answers
"will I get hired": no score, no percentage, no prediction. It counts.

* Resume only: the Editor's own findings (`check.findings`, one model), and
  the checks that need more than one line at a time: contact, structure,
  duplication, length, evidence status per line (confirmed, changed, no longer
  confirmed, written by the person, imported), the shared grounding reader
  on evidence-linked lines (`tailor.grounding`: numbers, tools, rank), dates,
  and the export checks already run on this revision (never a new file).
* With a job: the Tailor's own requirements and retrieval (`jd`, `tailor`),
  each requirement SHOWN in this resume with confirmed support, CONFIRMED but
  not shown, mentioned by the resume WITHOUT confirmed support, or NOT FOUND
  in confirmed experience. Eligibility (where, papers, schedule) is apart and
  never counted as coverage.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from career_agent.resume_doc import jd
from career_agent.resume_doc.check import SEVERITIES, finding, findings
from career_agent.resume_doc.models import (
    EVIDENCED_ORIGINS,
    Origin,
    ResumeDocument,
    upgrade_resume_document,
)
from career_agent.resume_doc.store import EvidenceNotConfirmed, ResumeStore, ResumeStoreError
from career_agent.resume_doc.tailor import (
    HAVE,
    _career,
    _lines,
    grounding,
    retrieve,
    sources,
    suggest,
)
from career_agent.storage.db import transaction

VERSION = "resume-analyze-v1"
LONG_SUMMARY_CHARS = 700
MANY_BULLETS = 8
#: Phrases that say nothing a reader can check. Advice, never a fact.
GENERIC = (
    "responsible for", "team player", "hard-working", "hard working", "results-driven",
    "detail-oriented", "go-getter", "think outside the box", "self-starter",
    "responsavel por", "proativo", "proativa", "trabalho em equipe", "orientado a resultados",
)  # fmt: skip
#: Shared grounding codes, as Analyze names them for an evidence-linked line.
_GROUNDING = {"NUMBERS": "UNSUPPORTED_NUMBER", "NAMED_TOOLS": "UNSUPPORTED_TOOL",
              "SENIORITY": "UNSUPPORTED_SENIORITY"}  # fmt: skip
#: Job coverage, from the Tailor's own states.
SHOWN, NOT_SHOWN, UNCONFIRMED, NOT_FOUND, ELIGIBILITY = (
    "SUPPORTED_IN_RESUME", "SUPPORTED_IN_CONFIRMED_EXPERIENCE_NOT_RESUME",
    "RESUME_TEXT_WITHOUT_CONFIRMED_SUPPORT", "NOT_FOUND_IN_CONFIRMED_EXPERIENCE", "ELIGIBILITY",
)  # fmt: skip
_ORDER = {"REQUIRED": 0, "RESPONSIBILITY": 1, "PREFERRED": 2}


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:10]


def _shown(doc: ResumeDocument, section: str) -> list[Any]:
    """The entries of a section that are on the page."""
    if section in doc.layout.hidden_sections:
        return []
    return [e for e in getattr(doc, section) if not e.hidden]


def _bullets(doc: ResumeDocument) -> list[tuple[str, Any]]:
    """(ref, line) for every bullet on the page, custom sections included."""
    out = []
    for section in ("experience", "projects", "education"):
        for entry in _shown(doc, section):
            out += [
                (f"{section}/{entry.id}/bullet/{b.id}", b) for b in entry.bullets if not b.hidden
            ]
    for c in doc.custom_sections:
        if not c.hidden and f"custom:{c.id}" not in doc.layout.hidden_sections:
            out += [(f"custom:{c.id}/bullet/{b.id}", b) for b in c.items if not b.hidden]
    return out


def _structure(doc: ResumeDocument) -> list[dict[str, Any]]:
    out = []
    i = doc.identity
    if not i.email and not i.phone and not i.links:
        out.append(finding("NO_CONTACT", "identity"))
    hidden = set(doc.layout.hidden_sections)
    # A heading cannot be blank (the model refuses it); two alike can be.
    shown = [c for c in doc.custom_sections if not c.hidden and f"custom:{c.id}" not in hidden]
    names = [c.heading.strip().casefold() for c in shown]
    for c in shown:
        if names.count(c.heading.strip().casefold()) > 1:
            out.append(finding("DUPLICATE_SECTION", f"section/custom:{c.id}"))
    for e in _shown(doc, "experience"):
        if e.start is None:
            out.append(finding("ENTRY_INCOMPLETE", f"experience/{e.id}"))
    for ed in _shown(doc, "education"):
        if not (ed.degree or ed.field_of_study):
            out.append(finding("ENTRY_INCOMPLETE", f"education/{ed.id}"))
    if doc.experience and not _shown(doc, "experience"):
        out.append(finding("NO_VISIBLE_EXPERIENCE", "section/experience"))
    return out


def _content(doc: ResumeDocument) -> list[dict[str, Any]]:
    out = []
    hidden = doc.layout.hidden_sections
    if doc.summary and "summary" not in hidden and len(doc.summary.text) > LONG_SUMMARY_CHARS:
        out.append(finding("LONG_SUMMARY", "summary"))
    seen: dict[str, tuple[str, Any]] = {}
    tokens: list[tuple[str, set[str]]] = []
    for ref, b in _bullets(doc):
        same = " ".join(b.text.split())  # exact, but for spacing
        if same in seen:
            # The copy to remove is the one NOT made from evidence, if either is not.
            first_ref, first = seen[same]
            typed = first.origin not in EVIDENCED_ORIGINS and b.origin in EVIDENCED_ORIGINS
            out.append(finding("DUPLICATE_BULLET", first_ref if typed else ref,
                               keep=ref if typed else first_ref))  # fmt: skip
            continue
        seen[same] = (ref, b)
        words = jd.tokens(b.text)
        for other, held in tokens:
            if len(words) >= 4 and len(words & held) / len(words | held) >= 0.8:
                out.append(finding("NEAR_DUPLICATE_BULLET", ref, first=other))
                break
        tokens.append((ref, words))
        if any(g in jd.folded(same) for g in GENERIC):
            out.append(finding("GENERIC_PHRASE", ref))
    for section in ("experience", "projects"):
        for e in _shown(doc, section):
            n = sum(not b.hidden for b in e.bullets)
            if n > MANY_BULLETS:
                out.append(finding("MANY_BULLETS", f"{section}/{e.id}", n=n))
    labels: set[str] = set()
    for g in doc.skills if "skills" not in doc.layout.hidden_sections else []:
        for item in g.items if not g.hidden else []:
            label = jd.folded(item.label).strip()
            if label in labels:
                out.append(finding("DUPLICATE_SKILL", f"skills/{g.id}/item/{item.id}"))
            labels.add(label)
    return out


def _evidence(conn: sqlite3.Connection, doc: ResumeDocument) -> tuple[list, dict[str, int]]:
    """Each shown line's evidence status, by what it is made of: the person's
    own text by its origin (never by ids it carries), evidence lines by
    whether their claims are confirmed now and unchanged; and the shared
    grounding reader on every evidence line against what its evidence says."""
    _, rows = _career(conn)
    confirmed = {r["claim_key"]: r for r in rows if r["state"] == "CONFIRMED"}
    counts = dict.fromkeys(
        ("CONFIRMED", "EVIDENCE_CHANGED", "NO_LONGER_CONFIRMED", "USER_AUTHORED", "IMPORTED"), 0
    )
    out = []
    for holder, line, shown in _lines(doc):
        if not shown or not line.text.strip():
            continue
        ref = line.id
        if line.origin not in EVIDENCED_ORIGINS:
            counts["IMPORTED" if line.origin is Origin.IMPORTED else "USER_AUTHORED"] += 1
            continue
        ids = list(line.evidence_ids)
        if any(k not in confirmed for k in ids):
            counts["NO_LONGER_CONFIRMED"] += 1
            key = f"STALE_EVIDENCE:{ref}:{_fingerprint(*sorted(ids))}"
            out.append(finding("STALE_EVIDENCE", ref, key=key, evidence_ids=ids))
            continue
        held = [confirmed[k] for k in ids]
        said = line.original_text or line.text
        verbatim = line.origin is Origin.EVIDENCE_VERBATIM and len(held) == 1
        if verbatim and jd.folded(said).strip(" .") != jd.folded(held[0]["text"]).strip(" ."):
            counts["EVIDENCE_CHANGED"] += 1
            key = f"EVIDENCE_CHANGED:{ref}:{_fingerprint(held[0]['text'])}"
            out.append(finding("EVIDENCE_CHANGED", ref, key=key, evidence_ids=ids))
            continue
        counts["CONFIRMED"] += 1
        source = " ".join(
            [
                line.original_text or "",
                *(h["text"] + " " + " ".join(h.get("tools") or []) for h in held),
            ]
        )
        tools = {jd.folded(t) for h in held for t in h.get("tools") or []}
        titles = " ".join(
            f"{e.display_title} {e.source_title or ''}"
            for e in doc.experience
            if holder in (None, e.id)
        )
        for code, _ in grounding(line.text, source, tools, ai=True, titles=titles):
            if code in _GROUNDING:
                kind = _GROUNDING[code]
                key = f"{kind}:{ref}:{_fingerprint(line.text)}"
                out.append(finding(kind, ref, key=key, evidence_ids=ids))
    return out, counts  # fmt: skip


def _consistency(doc: ResumeDocument) -> list[dict[str, Any]]:
    out = []
    shown = _shown(doc, "experience")
    for e in shown:
        if e.source_title and e.display_title.strip() != e.source_title.strip():
            out.append(finding("TITLE_RENAMED", f"experience/{e.id}"))
    current = [e for e in shown if e.current]
    if len(current) > 1:
        # Two current roles can be true: said as a check, never as an error.
        out.append(finding("CHECK_DATES", "section/experience", n=len(current)))
    seen = set()
    for e in shown:
        same = (jd.folded(e.employer), jd.folded(e.source_title or e.display_title),
                e.start.key() if e.start else None)  # fmt: skip
        if same in seen:
            out.append(finding("DUPLICATE_ROLE", f"experience/{e.id}"))
        seen.add(same)
    return out


def _export(store: ResumeStore, document_id: str, revision_id: str) -> tuple[list, dict | None]:
    """The export checks already run, never a new file. Only a check of THIS
    revision speaks for it; an older one says the resume changed since."""
    exports = store.list_exports(document_id)
    if not exports:
        return [finding("EXPORT_NOT_CHECKED", "document")], None
    mine = [x for x in exports if x.revision_id == revision_id]
    x = mine[-1] if mine else exports[-1]
    said = [] if mine else [finding("EXPORT_OUTDATED", "document")]
    return said, {"format": x.format, "current": bool(mine), "page_count": x.page_count,
                  "checks": x.ats_check.get("checks", [])}  # fmt: skip


def resume(conn: sqlite3.Connection, doc: ResumeDocument, document_id: str,
           revision_id: str) -> dict[str, Any]:  # fmt: skip
    """Resume-only Analyze of one revision's content."""
    store = ResumeStore(conn)
    evidence, counts = _evidence(conn, doc)
    exported, export = _export(store, document_id, revision_id)
    found = findings(doc) + _structure(doc) + _content(doc) + evidence + _consistency(doc)
    # Each line-level finding carries the line, so it can be found without the Editor.
    texts = {line.id: line.text for _, line, _ in _lines(doc)}
    for f in found:
        line = f["ref"].rsplit("/", 1)[-1]
        if line in texts:
            f["text"] = texts[line]
    return {
        "overview": {"roles": len(_shown(doc, "experience")), "bullets": len(_bullets(doc)),
                     "evidence": counts},
        "findings": found + exported,
        "export": export,
    }  # fmt: skip


def job(conn: sqlite3.Connection, doc: ResumeDocument, text: str) -> dict[str, Any]:
    """Resume + job: each requirement's state, with the employer's own words,
    the resume lines and the confirmed evidence behind it. Counts, not a score."""
    analysis = jd.analyse(text)
    pool = sources(conn, doc)
    supports = retrieve(analysis, pool)
    on_page = {line.id: line.text for _, line, _ in _lines(doc)}
    on_page |= {i.id: i.label for g in doc.skills for i in g.items}
    claims = {s.claim_key: s.text for s in pool if s.claim_key}
    addable = {s["key"][4:] for s in suggest(doc, supports) if s["key"].startswith("add:")}
    rows: list[dict[str, Any]] = []
    for sup in supports:
        req = sup.requirement
        coverage = sup.coverage
        # Where, papers, schedule and a language the job requires are about the
        # person's situation (`jd.ELIGIBILITY_KINDS`), not what a resume shows:
        # apart, never counted. The Tailor's own retrieval decides the rest:
        # confirmed experience the resume does not show (or shows less well) is
        # NOT_SHOWN.
        if req.eligibility:
            state = ELIGIBILITY
        elif sup.state == HAVE:
            state = NOT_SHOWN
        elif coverage in ("COVERED", "PARTLY"):
            state = SHOWN
        elif coverage == "SAID":
            state = UNCONFIRMED
        else:
            state = NOT_FOUND
        evidenced = state == SHOWN
        shown = [s for s, _ in sup.shown if bool(s.claim_key or s.evidence_ids) == evidenced]
        rows.append({
            "id": req.id, "quote": req.source_quote, "hardness": req.hardness, "kind": req.kind,
            "state": state,
            # Partly: some support, or a tenure that is never judged.
            "partly": state == SHOWN and coverage == "PARTLY",
            "lines": [on_page[s.line_id] for s in shown if s.line_id in on_page][:3],
            "evidence": [claims[k] for s, _ in sup.shown + sup.unshown
                         for k in ([s.claim_key] if s.claim_key else s.evidence_ids)
                         if k in claims][:3],
            "apply": f"add:{req.id}" if req.id in addable else None,
            "_rank": (_ORDER.get(req.hardness, _ORDER.get(req.kind, 3)), -req.importance),
        })  # fmt: skip
    # Explicitly required first, then the work itself, then preferred, then the rest.
    rows.sort(key=lambda r: r.pop("_rank"))
    judged = [r for r in rows if r["state"] != ELIGIBILITY]
    counts = {s: sum(r["state"] == s for r in judged) for s in (SHOWN, NOT_SHOWN, UNCONFIRMED,
                                                               NOT_FOUND)}  # fmt: skip
    counts |= {"partly": sum(r["partly"] for r in judged), "total": len(judged)}
    return {"requirements": rows, "counts": counts,
            "eligibility": [r for r in rows if r["state"] == ELIGIBILITY]}  # fmt: skip


def run(
    conn: sqlite3.Connection, document_id: str, *, snapshot_id: str | None = None
) -> dict[str, Any]:
    """Analyze the document's exact content as a milestone: the latest
    revision when the working copy is that revision, else a new milestone of
    it (the caller saved first). When the working copy cites experience no
    longer confirmed, no milestone can be taken: the working copy itself is
    analyzed, anchored by its hash. With a snapshot, also against that ad.
    Dismissed findings are left out and counted."""
    store = ResumeStore(conn)
    stored = store.get_document(document_id)
    latest = store.latest_revision_id(document_id)
    rev = store.get_revision(latest) if latest else None
    if rev is None or rev.content_sha256 != stored.working_sha256:
        try:
            rev = store.checkpoint_revision(document_id, "MANUAL_CHECKPOINT")
        except EvidenceNotConfirmed:
            rev = None
    doc = rev.content if rev else stored.working
    out: dict[str, Any] = {
        "version": VERSION, "document_id": document_id,
        "revision_id": rev.id if rev else None,
        "content_sha256": rev.content_sha256 if rev else stored.working_sha256,
        "title": stored.title, "kind": stored.kind.value,
        "jd_snapshot_id": stored.jd_snapshot_id,
        **resume(conn, doc, document_id, rev.id if rev else ""),
    }  # fmt: skip
    if snapshot_id:
        snap = store.get_jd_snapshot(snapshot_id)
        out["job"] = {"snapshot_id": snap.id, "title": snap.title, "company": snap.company,
                      "job_id": snap.job_id, **job(conn, doc, snap.text)}  # fmt: skip
    dismissed = store.dismissed_findings(document_id)
    out["dismissed"] = len(dismissed & {f["key"] for f in out["findings"]})
    out["findings"] = sorted(
        (f for f in out["findings"] if f["key"] not in dismissed),
        key=lambda f: (SEVERITIES.index(f["severity"]), f["category"], f["ref"]),
    )
    return out


def remove_duplicate(
    conn: sqlite3.Connection, document_id: str, key: str, *, expected_sha256: str
) -> None:
    """Remove the exact repeat a current DUPLICATE_BULLET finding names (the
    copy not made from evidence, when one is not); the other stays. Anything
    else is refused."""
    store = ResumeStore(conn)
    with transaction(conn):
        stored = store.get_document(document_id)
        found = next((f for f in _content(stored.working)
                      if f["key"] == key and f["kind"] == "DUPLICATE_BULLET"), None)  # fmt: skip
        if found is None:
            raise ResumeStoreError("this finding no longer applies")
        section, bullet_id = found["ref"].split("/")[0], found["ref"].rsplit("/", 1)[-1]
        data = stored.working.model_dump(mode="json")
        holders = (
            data["custom_sections"] if section.startswith("custom:")
            else data[section]
        )  # fmt: skip
        for entry in holders:
            field = "items" if section.startswith("custom:") else "bullets"
            entry[field] = [b for b in entry[field] if b["id"] != bullet_id]
        store.save_working_copy(
            document_id, upgrade_resume_document(data), expected_sha256=expected_sha256
        )
