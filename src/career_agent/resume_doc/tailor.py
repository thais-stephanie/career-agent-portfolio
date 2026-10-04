"""Deterministic Tailor V2: the Master plus one job ad, into a grounded version.

No model, no provider, no network. The stages, each a plain function whose
output is stored on the run (`tailoring_run`):

1. snapshot the ad (immutable) and capture the Master's exact revision;
2. analyse the ad into quoted requirements (`resume_doc.jd`);
3. retrieve support per requirement from confirmed evidence and the Master:
   SHOWN_IN_MASTER, HAVE_EVIDENCE_NOT_SHOWN or NO_EVIDENCE;
4. decide a strategy (what to add, show, order, hide), citing ids only;
5. draft: copy the Master revision and apply the strategy, every added line a
   confirmed statement verbatim (or with a first-person pronoun dropped);
6. review the draft independently (`review`): grounding, numbers, named
   tools, titles, employers, dates, chronology, length, duplication;
7. validate (requirement ids, quotes, evidence still confirmed now);
8. create the TAILORED document, the run and its changes in one transaction.

WHAT IT MAY DO: select, order, hide, show and lightly rephrase confirmed
material. WHAT IT NEVER DOES: write a number, tool, title, employer, date,
certification or responsibility that no confirmed source holds. The Master is
never written; a gap stays a gap; eligibility (where the job hires, visas,
schedules) is reported and never "covered" by a resume.

The headline and summary are the Master's: no safe deterministic rewrite of
them exists without composing prose, and composed prose is how filler and
invented tenure get in.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from career_agent.clock import new_id
from career_agent.resume_doc import jd
from career_agent.resume_doc.evidence import unconfirmed_lines
from career_agent.resume_doc.master import _career
from career_agent.resume_doc.models import ResumeDocument, upgrade_resume_document
from career_agent.resume_doc.store import ResumeStore, ResumeStoreError, StoredDocument
from career_agent.storage.career_repo import HIGHLIGHT_CATEGORIES, SKILL_CATEGORIES

MODE = "DETERMINISTIC"
SHOWN, HAVE, NONE = "SHOWN_IN_MASTER", "HAVE_EVIDENCE_NOT_SHOWN", "NO_EVIDENCE"
#: Bounds that keep a version readable; a version is a selection, not a dump.
MAX_ADDS, MAX_ADDS_PER_ROLE, MAX_SHOWN_PER_ROLE = 8, 3, 6
LONG_LINE_WORDS = 40
#: A strength at or above this answers an ask fully.
COVERED_AT = 0.75
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_PRONOUN = re.compile(r"^(?:I|Eu|Yo)\s+(?=\w)")


class TailorFailed(ResumeStoreError):
    """The draft failed a blocking check; nothing was saved. `findings` say why."""

    def __init__(self, findings: list[dict[str, Any]]) -> None:
        super().__init__("the tailored draft failed its checks")
        self.findings = findings


# ------------------------------------------------------------------ sources


@dataclass
class Source:
    """One piece of support: a confirmed claim, or a line of a resume."""

    id: str
    text: str
    claim_key: str | None = None
    category: str | None = None
    experience_id: str | None = None
    tools: tuple[str, ...] = ()
    #: In the resume being compared (the Master, or the version itself).
    in_resume: bool = False
    shown: bool = False
    #: The resume line's id, and the entry it sits in.
    line_id: str | None = None
    entry_id: str | None = None
    evidence_ids: tuple[str, ...] = ()

    @property
    def names(self) -> set[str]:
        return jd.named_terms(self.text) | {jd.folded(t) for t in self.tools}

    @property
    def tokens(self) -> set[str]:
        out = jd.tokens(self.text)
        for tool in self.tools:
            out |= jd.tokens(tool)
        return out


def _lines(doc: ResumeDocument) -> list[tuple[str | None, Any, bool]]:
    """(entry id, line, shown) for every bullet-like line of a document."""
    hidden = set(doc.layout.hidden_sections)
    out: list[tuple[str | None, Any, bool]] = []
    for section in ("experience", "projects", "education"):
        for entry in getattr(doc, section):
            for b in entry.bullets:
                out.append((entry.id, b, not (b.hidden or entry.hidden or section in hidden)))
    for custom in doc.custom_sections:
        for b in custom.items:
            out.append((custom.id, b, not (b.hidden or custom.hidden)))
    for block in (doc.headline, doc.summary):
        if block is not None:
            out.append((None, block, True))
    return out


def sources(conn: sqlite3.Connection, doc: ResumeDocument) -> list[Source]:
    """Confirmed claims of THIS profile, and every line and skill of `doc`."""
    out: list[Source] = []
    # A statement shows only as a line; a skill item citing it shows the skill,
    # not what the person did with it.
    shown_keys: set[str] = set()
    shown_skills: set[str] = set()
    for entry_id, line, shown in _lines(doc):
        out.append(
            Source(
                id=line.id, text=line.text, in_resume=True, shown=shown, line_id=line.id,
                entry_id=entry_id, evidence_ids=tuple(line.evidence_ids),
            )
        )  # fmt: skip
        if shown:
            shown_keys |= set(line.evidence_ids)
    for group in doc.skills:
        for item in group.items:
            shown = not group.hidden and "skills" not in doc.layout.hidden_sections
            out.append(
                Source(
                    id=item.id, text=item.label, in_resume=True, shown=shown, line_id=item.id,
                    evidence_ids=tuple(item.evidence_ids), category="SKILL",
                )
            )  # fmt: skip
            if shown:
                shown_skills |= set(item.evidence_ids)
    for cert in doc.certifications:
        out.append(
            Source(id=cert.id, text=cert.name, in_resume=True, shown=not cert.hidden,
                   line_id=cert.id, category="CERTIFICATION")
        )  # fmt: skip
    _, rows = _career(conn)
    for row in rows:
        if row["state"] != "CONFIRMED" or not str(row.get("text") or "").strip():
            continue
        key, category = row["claim_key"], row.get("category")
        shown = key in shown_keys or (category in SKILL_CATEGORIES and key in shown_skills)
        out.append(
            Source(
                id=key, text=row["text"], claim_key=key, category=category,
                experience_id=row.get("experience_id"), tools=tuple(row.get("tools") or ()),
                shown=shown, evidence_ids=(key,),
            )
        )  # fmt: skip
    return out


# ---------------------------------------------------------------- retrieval


def strength(req: jd.Requirement, source: Source) -> float:
    """How well `source` answers `req`: 0 when it does not.

    A named thing the ad asks for (a tool, a product) must be named by the
    source itself; "CRM" never answers "Salesforce". Otherwise the shared
    words must reach two (or all of the ask, when it has fewer)."""
    if not req.concepts and not req.named:
        return 0.0
    text = jd.folded(source.text) + " " + " ".join(jd.folded(t) for t in source.tools)
    named_hit = {
        n for n in req.named if n in source.names or re.search(rf"\b{re.escape(n)}\b", text)
    }
    if req.named and not named_hit:
        return 0.0
    overlap = len(set(req.concepts) & source.tokens)
    if not named_hit and overlap < min(2, len(req.concepts)):
        return 0.0
    named_part = len(named_hit) / len(req.named) if req.named else 1.0
    word_part = overlap / max(1, len(req.concepts))
    return round(0.5 * named_part + 0.5 * word_part, 3)


@dataclass
class Support:
    requirement: jd.Requirement
    state: str
    #: Matching sources, strongest first.
    shown: list[tuple[Source, float]] = field(default_factory=list)
    unshown: list[tuple[Source, float]] = field(default_factory=list)

    @property
    def coverage(self) -> str:
        """COVERED, PARTLY or NOT_FOUND, for the person; ELIGIBILITY is not ours."""
        if self.requirement.eligibility:
            return "ELIGIBILITY"
        best = max([s for _, s in self.shown] or [0.0])
        if not best:
            return "NOT_FOUND"
        # Years are never compared: the work may be shown, the tenure is not judged.
        if self.requirement.kind == "EXPERIENCE":
            return "PARTLY"
        return "COVERED" if best >= COVERED_AT else "PARTLY"


def retrieve(analysis: jd.Analysis, pool: list[Source]) -> list[Support]:
    """Each requirement's support in `pool` (shown in the resume, or not)."""
    out = []
    for req in analysis.requirements:
        if req.eligibility:
            out.append(Support(req, NONE))
            continue
        hits = sorted(((s, strength(req, s)) for s in pool), key=lambda pair: -pair[1])
        hits = [(s, v) for s, v in hits if v > 0]
        shown = [(s, v) for s, v in hits if s.shown]
        unshown = [(s, v) for s, v in hits if not s.shown]
        # Shown only when what shows answers it well, or as well as anything
        # unshown would: a bare tool name in Skills does not hide a confirmed
        # line that says what was done with it.
        best_shown = shown[0][1] if shown else 0.0
        best_unshown = unshown[0][1] if unshown else 0.0
        if shown and (best_shown >= COVERED_AT or best_shown >= best_unshown):
            state = SHOWN
        else:
            state = HAVE if unshown else NONE
        out.append(Support(req, state, shown, unshown))
    return out


# ----------------------------------------------------------------- strategy


def rewrite(text: str) -> str:
    """The only rewrite: a leading first-person pronoun goes ("I built" -> "Built")."""
    cut = _PRONOUN.sub("", text.strip(), count=1)
    return cut[:1].upper() + cut[1:] if cut != text.strip() else text.strip()


def strategy(doc: ResumeDocument, supports: list[Support]) -> dict[str, Any]:
    """What to add, show, order and hide, as ids. Decided before any line is written."""
    entries = {e.experience_id: e for e in doc.experience if e.experience_id and not e.hidden}
    present = {jd.folded(line.text) for _, line, _ in _lines(doc)}
    skill_labels = {jd.folded(i.label) for g in doc.skills for i in g.items}
    plan: dict[str, Any] = {"add": [], "show": [], "skills": [], "relevance": {}, "gaps": []}
    per_role: dict[str, int] = {}
    ranked = sorted(supports, key=lambda s: (-s.requirement.importance, s.requirement.start))
    for sup in ranked:
        rid = sup.requirement.id
        for source, value in sup.shown:
            if source.line_id:
                plan["relevance"][source.line_id] = plan["relevance"].get(source.line_id, 0) + (
                    sup.requirement.importance * value
                )
        if sup.state == NONE and not sup.requirement.eligibility:
            plan["gaps"].append(rid)
        if sup.state != HAVE or len(plan["add"]) + len(plan["show"]) >= MAX_ADDS:
            continue
        for source, _ in sup.unshown:
            if source.in_resume and source.line_id:  # a line the resume hides
                if source.line_id not in plan["show"]:
                    plan["show"].append(source.line_id)
                    plan["relevance"][source.line_id] = sup.requirement.importance
                break
            if source.claim_key is None:
                continue
            entry = entries.get(source.experience_id or "")
            text = rewrite(source.text)
            if (
                source.category in HIGHLIGHT_CATEGORIES
                and entry is not None
                and jd.folded(text) not in present
                and per_role.get(entry.id, 0) < MAX_ADDS_PER_ROLE
            ):
                present.add(jd.folded(text))
                per_role[entry.id] = per_role.get(entry.id, 0) + 1
                plan["add"].append(
                    {"entry_id": entry.id, "text": text, "evidence_id": source.claim_key,
                     "verbatim": text == source.text.strip(), "requirement_ids": [rid]}
                )  # fmt: skip
                break
            labels = [source.text] if source.category in SKILL_CATEGORIES else []
            labels += [t for t in source.tools if jd.folded(t) in set(sup.requirement.named)]
            label = next((x for x in labels if jd.folded(x) not in skill_labels), None)
            if label and len(label) <= 60:
                skill_labels.add(jd.folded(label))
                plan["skills"].append(
                    {"label": label, "evidence_id": source.claim_key, "requirement_ids": [rid]}
                )
                break
    return plan


# -------------------------------------------------------------------- draft


def draft(
    master: ResumeDocument, plan: dict[str, Any], *, target: dict[str, Any], provenance: dict
) -> tuple[ResumeDocument, list[dict[str, Any]]]:
    """The Master revision with the plan applied, and the changes it made."""
    data = master.model_dump(mode="json")
    changes: list[dict[str, Any]] = []
    relevance: dict[str, float] = plan["relevance"]
    by_entry = {e["id"]: e for e in data["experience"]}
    for add in plan["add"]:
        line = {
            "id": new_id(),
            "text": add["text"],
            "origin": "EVIDENCE_VERBATIM" if add["verbatim"] else "RULE_REWRITE",
            "evidence_ids": [add["evidence_id"]],
            "requirement_ids": add["requirement_ids"],
        }
        by_entry[add["entry_id"]]["bullets"].append(line)
        relevance[line["id"]] = 99.0
        changes.append(
            {"op": "ADD_BULLET", "line_id": line["id"], "entry_id": add["entry_id"],
             "after": add["text"], "requirement_ids": add["requirement_ids"],
             "evidence_ids": [add["evidence_id"]]}
        )  # fmt: skip
    for entry in data["experience"]:
        before = [b["id"] for b in entry["bullets"]]
        for b in entry["bullets"]:
            if b["id"] in plan["show"] and b.get("hidden"):
                b["hidden"] = False
                changes.append({"op": "SHOW_BULLET", "line_id": b["id"], "after": b["text"],
                                "evidence_ids": b.get("evidence_ids", [])})  # fmt: skip
        # Relevant lines first; the rest keep their order. Chronology of roles is untouched.
        entry["bullets"].sort(key=lambda b: -relevance.get(b["id"], 0.0))
        if [b["id"] for b in entry["bullets"]] != before:
            changes.append({"op": "REORDER_BULLETS", "entry_id": entry["id"]})
        visible = [b for b in entry["bullets"] if not b.get("hidden")]
        for b in visible[MAX_SHOWN_PER_ROLE:]:
            if relevance.get(b["id"], 0.0) == 0.0:
                b["hidden"] = True
                changes.append({"op": "HIDE_BULLET", "line_id": b["id"], "after": b["text"]})
    if plan["skills"]:
        if not data["skills"]:
            data["skills"].append({"id": new_id(), "name": "Skills", "hidden": False, "items": []})
        group = data["skills"][0]
        for skill in plan["skills"]:
            item = {"id": new_id(), "label": skill["label"], "origin": "EVIDENCE_VERBATIM",
                    "evidence_ids": [skill["evidence_id"]]}  # fmt: skip
            group["items"].insert(0, item)
            changes.append(
                {"op": "ADD_SKILL", "line_id": item["id"], "after": skill["label"],
                 "requirement_ids": skill["requirement_ids"],
                 "evidence_ids": [skill["evidence_id"]]}
            )  # fmt: skip
    data.update(
        id=new_id(),
        kind="TAILORED",
        title=" · ".join(filter(None, [target["title"], target.get("company")]))[:300],
        target=target,
        provenance=provenance,
    )
    return upgrade_resume_document(data), changes


# ------------------------------------------------------------------- review


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUMBER.findall(text)}


def review(
    draft_doc: ResumeDocument, master: ResumeDocument, pool: list[Source], supports: list[Support]
) -> list[dict[str, Any]]:
    """Independent checks of the draft against the Master and the evidence.

    Every finding is `{check, outcome: PASS|WARN|FAIL, ref, detail}`. A FAIL
    means something no source holds reached the draft."""
    findings: list[dict[str, Any]] = []
    claims = {s.claim_key: s for s in pool if s.claim_key}
    before = {line.id: line.text for _, line, _ in _lines(master)}
    before |= {i.id: i.label for g in master.skills for i in g.items}

    def fail(check: str, ref: str, detail: str) -> None:
        findings.append({"check": check, "outcome": "FAIL", "ref": ref, "detail": detail})

    def warn(check: str, ref: str, detail: str) -> None:
        findings.append({"check": check, "outcome": "WARN", "ref": ref, "detail": detail})

    lines = [(line.id, line.text, list(line.evidence_ids)) for _, line, _ in _lines(draft_doc)]
    lines += [(i.id, i.label, list(i.evidence_ids)) for g in draft_doc.skills for i in g.items]
    for line_id, text, cited in lines:
        if before.get(line_id) == text:
            continue  # the Master's own line, unchanged
        if not cited or any(k not in claims for k in cited):
            fail("GROUNDING", line_id, "a new line cites no confirmed evidence")
            continue
        source = " ".join(claims[k].text + " " + " ".join(claims[k].tools) for k in cited)
        extra_numbers = _numbers(text) - _numbers(source)
        if extra_numbers:
            fail("NUMBERS", line_id, f"numbers not in its evidence: {sorted(extra_numbers)}")
        known = jd.named_terms(source) | {jd.folded(t) for k in cited for t in claims[k].tools}
        extra_names = {
            n
            for n in jd.named_terms(text, sentence_start=False) - known
            if n not in jd.folded(source)
        }
        if extra_names:
            fail("NAMED_TOOLS", line_id, f"named terms not in its evidence: {sorted(extra_names)}")
        if not set(jd.tokens(text)) <= jd.tokens(source) | jd.tokens(before.get(line_id, "")):
            fail("OVERSTATEMENT", line_id, "words not in its evidence")
    old = [(e.id, e.employer, e.source_title, e.display_title, e.start, e.end, e.current)
           for e in master.experience]  # fmt: skip
    new = [(e.id, e.employer, e.source_title, e.display_title, e.start, e.end, e.current)
           for e in draft_doc.experience]  # fmt: skip
    if [o[0] for o in old] != [n[0] for n in new]:
        fail("CHRONOLOGY", "experience", "roles were added, removed or reordered")
    for o, n in zip(old, new, strict=False):
        if o[1] != n[1]:
            fail("EMPLOYER", o[0], "an employer changed")
        if o[2:4] != n[2:4]:
            fail("SENIORITY", o[0], "a job title changed")
        if o[4:] != n[4:]:
            fail("DATES", o[0], "dates changed")
    if [c.name for c in master.certifications] != [c.name for c in draft_doc.certifications]:
        fail("CERTIFICATIONS", "certifications", "certifications changed")
    if master.education != draft_doc.education:
        fail("EDUCATION", "education", "education changed")
    for entry in draft_doc.experience:
        texts = [jd.folded(b.text) for b in entry.bullets if not b.hidden]
        if len(texts) != len(set(texts)):
            warn("DUPLICATION", entry.id, "the same line twice in one role")
        for b in entry.bullets:
            if not b.hidden and len(b.text.split()) > LONG_LINE_WORDS:
                warn("LENGTH", b.id, f"{len(b.text.split())} words")
    shown = sum(1 for _, _, visible in _lines(draft_doc) if visible)
    if shown > 30:
        warn("LENGTH", "document", f"{shown} lines shown")
    for sup in supports:
        if (
            sup.requirement.hardness == "REQUIRED"
            and sup.state == NONE
            and not sup.requirement.eligibility
        ):
            warn("COVERAGE", sup.requirement.id, "a required ask has no confirmed support")
    if not any(f["outcome"] == "FAIL" for f in findings):
        findings.append({"check": "GROUNDING", "outcome": "PASS", "ref": "document", "detail": ""})
    return findings


# --------------------------------------------------------------------- run


def job_ad(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    """A Career Agent job as an ad to snapshot: its text exactly as stored."""
    row = conn.execute(
        "SELECT j.title, j.url, c.name AS company, r.description_text"
        " FROM job j JOIN company c ON c.id = j.company_id"
        " LEFT JOIN job_raw r ON r.content_hash = j.content_hash WHERE j.id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return None
    title = str(row["title"] or "").strip() or "Job"
    return {
        "job_id": job_id,
        "title": title,
        "company": row["company"] or None,
        "url": row["url"] or None,
        "text": str(row["description_text"] or "").strip() or title,
    }


def tailor(
    conn: sqlite3.Connection,
    *,
    ad: dict[str, Any],
    pause: Any = None,
) -> tuple[StoredDocument, dict[str, Any]]:
    """Run every stage in ONE write transaction and store the result.

    `ad` is `{job_id?, title, company?, url?, text}`. `pause(stage)` is called
    between stages (tests use it to change the world mid-run). Raises
    `NotFound` without a Master, `TailorFailed` on a blocking finding and
    `EvidenceNotConfirmed` when evidence was retired before saving: in each
    case nothing is written."""
    from career_agent.resume_doc.store import NotFound
    from career_agent.storage.db import transaction

    store = ResumeStore(conn)
    timings: dict[str, float] = {}
    clock = time.perf_counter()

    def mark(stage: str) -> None:
        nonlocal clock
        now = time.perf_counter()
        timings[stage] = round((now - clock) * 1000, 1)
        clock = now
        if pause is not None:
            pause(stage)

    with transaction(conn):
        master_row = store.current_master()
        if master_row is None:
            raise NotFound("there is no Master resume yet")
        revision = store.checkpoint_revision(master_row.id, "MANUAL_CHECKPOINT")
        master = revision.content
        analysis = jd.analyse(ad["text"])
        snap = store.create_jd_snapshot(
            text=ad["text"], title=ad["title"], company=ad.get("company"),
            job_id=ad.get("job_id"), url=ad.get("url"), language=analysis.language,
        )  # fmt: skip
        mark("analysis")
        pool = sources(conn, master)
        supports = retrieve(analysis, pool)
        mark("retrieval")
        plan = strategy(master, supports)
        mark("strategy")
        run_id = new_id()
        target = {"jd_snapshot_id": snap.id, "job_id": snap.job_id, "title": snap.title,
                  "company": snap.company}  # fmt: skip
        provenance = {"created_from": "TAILOR", "master_document_id": master_row.id,
                      "master_revision_id": revision.id, "tailoring_run_id": run_id}  # fmt: skip
        doc, changes = draft(master, plan, target=target, provenance=provenance)
        mark("draft")
        findings = review(doc, master, pool, supports)
        mark("review")
        if any(f["outcome"] == "FAIL" for f in findings):
            raise TailorFailed([f for f in findings if f["outcome"] == "FAIL"])
        problems = validate(conn, doc, analysis, ad["text"], revision.id, master_row.id)
        mark("validation")
        if problems:
            raise TailorFailed(problems)
        stored = store.create_document(doc, reason="GENERATED", parent_document_id=master_row.id)
        store.create_tailoring_run(stored.id, mode=MODE, options={"ai": False}, run_id=run_id)
        report: dict[str, Any] = {
            "analysis": {"language": analysis.language,
                         "requirements": [_requirement(r) for r in analysis.requirements]},
            "retrieval": {"supports": [_support(s) for s in supports]},
            "strategy": {k: v for k, v in plan.items() if k != "relevance"},
            "review": {"findings": findings},
            "validation": {"problems": [], "master_revision_id": revision.id,
                           "timings_ms": {**timings, "total": round(sum(timings.values()), 1)}},
        }  # fmt: skip
        store.update_tailoring_run_stage(run_id, status="DONE", **report)
        for change in changes:
            made = store.record_tailoring_change(
                run_id, op=change, source="RULE",
                evidence_ids=change.get("evidence_ids"),
                requirement_ids=change.get("requirement_ids"),
                reason=change["op"],
            )  # fmt: skip
            store.decide_tailoring_change(made.id, "ACCEPTED")
    return stored, report


def validate(
    conn: sqlite3.Connection,
    doc: ResumeDocument,
    analysis: jd.Analysis,
    text: str,
    revision_id: str,
    master_id: str,
) -> list[dict[str, Any]]:
    """The last word before saving: ids, quotes and evidence, asked again now."""
    problems = []
    known = {r.id for r in analysis.requirements}
    for _, line, _ in _lines(doc):
        if set(line.requirement_ids) - known:
            problems.append(
                {"check": "REQUIREMENT", "ref": line.id, "detail": "unknown requirement"}
            )
    for r in analysis.requirements:
        if text[r.start : r.start + len(r.source_quote)] != r.source_quote:
            problems.append({"check": "QUOTE", "ref": r.id, "detail": "quote not in the ad"})
    for line_id in unconfirmed_lines(conn, doc):
        problems.append(
            {"check": "EVIDENCE", "ref": line_id, "detail": "evidence not confirmed now"}
        )
    latest = conn.execute(
        "SELECT id FROM resume_revision WHERE document_id = ? ORDER BY seq DESC LIMIT 1",
        (master_id,),
    ).fetchone()
    if latest is None or latest["id"] != revision_id:
        problems.append({"check": "MASTER", "ref": master_id, "detail": "the Master moved"})
    for p in problems:
        p["outcome"] = "FAIL"
    return problems


def _requirement(r: jd.Requirement) -> dict[str, Any]:
    return {
        "id": r.id, "quote": r.source_quote, "start": r.start, "section": r.section,
        "kind": r.kind, "hardness": r.hardness, "importance": r.importance,
        "language": r.language, "concepts": list(r.concepts), "named": list(r.named),
        "also_quoted": list(r.also_quoted),
    }  # fmt: skip


def _support(s: Support) -> dict[str, Any]:
    return {
        "requirement_id": s.requirement.id, "state": s.state, "coverage": s.coverage,
        "shown": [{"id": src.id, "strength": v} for src, v in s.shown[:3]],
        "unshown": [{"id": src.id, "strength": v} for src, v in s.unshown[:3]],
    }  # fmt: skip


# ----------------------------------------------------- explain, make it better


def explain(conn: sqlite3.Connection, stored: StoredDocument) -> dict[str, Any]:
    """A job version, against its ad: coverage, why it changed, gaps, and
    what would make it better now. Read only; recomputed on the version as it
    is, so a suggestion already applied is gone."""
    store = ResumeStore(conn)
    if stored.jd_snapshot_id is None:
        return {}
    snap = store.get_jd_snapshot(stored.jd_snapshot_id)
    doc = stored.working
    analysis = jd.analyse(snap.text)
    supports = retrieve(analysis, sources(conn, doc))
    entries = {e.id: e for e in doc.experience}
    quotes = {r.id: r.source_quote for r in analysis.requirements}

    def where(entry_id: str | None) -> dict[str, str] | None:
        entry = entries.get(entry_id or "")
        return {"title": entry.display_title, "employer": entry.employer} if entry else None

    changes = []
    run_id = doc.provenance.tailoring_run_id
    if run_id and doc.provenance.created_from.value == "TAILOR":
        for change in store.list_tailoring_changes(run_id):
            op = change.op
            changes.append(
                {"op": op["op"], "text": op.get("after"), "where": where(op.get("entry_id")),
                 "asks": [quotes[r] for r in change.requirement_ids if r in quotes]}
            )  # fmt: skip
    dismissed = store.dismissed_findings(stored.id)
    suggestions: list[dict[str, Any]] = []
    for sup in supports:
        req = sup.requirement
        if sup.state == HAVE:
            plan = strategy(doc, [sup])
            action: dict[str, Any] | None = None
            if plan["show"]:
                action = {"type": "show", "line_id": plan["show"][0]}
            elif plan["add"]:
                add = plan["add"][0]
                action = {"type": "add_bullet", "entry_id": add["entry_id"], "text": add["text"],
                          "origin": "EVIDENCE_VERBATIM" if add["verbatim"] else "RULE_REWRITE",
                          "evidence_ids": [add["evidence_id"]], "requirement_ids": [req.id],
                          "where": where(add["entry_id"])}  # fmt: skip
            elif plan["skills"]:
                skill = plan["skills"][0]
                action = {"type": "add_skill", "label": skill["label"],
                          "evidence_ids": [skill["evidence_id"]]}  # fmt: skip
            if action is not None:
                suggestions.append({"key": f"add:{req.id}", "kind": "UNSHOWN_EVIDENCE",
                                    "ask": req.source_quote, "action": action})  # fmt: skip
        elif sup.state == NONE and not req.eligibility:
            suggestions.append({"key": f"gap:{req.id}", "kind": "NO_EVIDENCE",
                                "ask": req.source_quote, "action": None})  # fmt: skip
    for _, line, shown in _lines(doc):
        words = len(line.text.split())
        if shown and words > LONG_LINE_WORDS:
            suggestions.append({"key": f"long:{line.id}", "kind": "LONG_LINE", "words": words,
                                "line_id": line.id, "text": line.text, "action": None})  # fmt: skip
    coverage = [
        {"ask": s.requirement.source_quote, "hardness": s.requirement.hardness,
         "kind": s.requirement.kind, "coverage": s.coverage}
        for s in sorted(supports, key=lambda s: (-s.requirement.importance, s.requirement.start))
    ]  # fmt: skip
    judged = [c for c in coverage if c["coverage"] != "ELIGIBILITY"]
    return {
        "job": {"title": snap.title, "company": snap.company, "job_id": snap.job_id},
        "tailored": doc.provenance.created_from.value == "TAILOR",
        "coverage": coverage,
        "supported": sum(c["coverage"] in ("COVERED", "PARTLY") for c in judged),
        "total": len(judged),
        "changes": changes,
        "suggestions": [s for s in suggestions if s["key"] not in dismissed],
    }
