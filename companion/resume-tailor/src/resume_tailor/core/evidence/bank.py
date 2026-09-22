# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Loading and indexing the evidence bank.

The bank is a JSON document (``data/evidence/evidence_bank.json``). A second,
optional document (``data/evidence/user_overrides.json``) holds facts the
candidate has confirmed. Overrides are applied in memory at load time: the
source bank is never rewritten, every touched object records which override
changed it, and resolved conflicts keep both original statements.

This module also adds the indexes the matcher and validator need: by id, by
position, a term index (canonical lexicon term -> record ids), numbers per
record, and tenure computed on an explicit basis.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from functools import cached_property
from pathlib import Path

from resume_tailor.core.lexicon import canonical, find_terms
from resume_tailor.core.models import (
    EvidenceBank,
    EvidenceRecord,
    Position,
    PositionKind,
    UserOverride,
    Verification,
)
from resume_tailor.core.text import months_between, number_variants, numbers_in

#: Which position kinds count for each tenure basis.
TENURE_BASES: dict[str, set[PositionKind]] = {
    # every relevant project/consulting experience, including junior-enterprise work
    "total_relevant": {
        PositionKind.EMPLOYMENT,
        PositionKind.INTERNSHIP,
        PositionKind.JUNIOR_ENTERPRISE,
        PositionKind.INDEPENDENT,
    },
    # paid employment including the internship
    "employment": {PositionKind.EMPLOYMENT, PositionKind.INTERNSHIP, PositionKind.INDEPENDENT},
    # professional tenure after the internship ended
    "professional": {PositionKind.EMPLOYMENT, PositionKind.INDEPENDENT},
}


def apply_overrides(bank: EvidenceBank, overrides: list[UserOverride]) -> EvidenceBank:
    """Apply user-confirmed facts in memory. Provenance is preserved: the bank file
    is untouched, conflicts keep their statements and gain ``resolved_by``, and
    each patched object lists the override ids that changed it."""
    positions = {p.id: p for p in bank.positions}
    records = {r.id: r for r in bank.records}
    certs = {c.id: c for c in bank.certifications}
    conflicts = {c.id: c for c in bank.conflicts}
    for o in overrides:
        if o.applies_to == "position":
            p = positions[o.target_id]
            setattr(p, o.field, o.value)
            p.overrides_applied.append(o.id)
        elif o.applies_to == "candidate":
            setattr(bank.candidate, o.field, o.value)
            bank.candidate.overrides_applied.append(o.id)
        elif o.applies_to == "record":
            r = records[o.target_id]
            updates = dict(o.values)
            if o.field:
                updates[o.field] = o.value
            # the original (derived) wording stays on disk and in ``superseded_text``; the confirmed
            # wording replaces it in memory, field by field, through the model so nested values validate
            validated = type(r).model_validate({**r.model_dump(mode="json"), **updates})
            for k in updates:
                setattr(r, k, getattr(validated, k))
            if "user_verified" not in r.sources:
                r.sources.append("user_verified")
            r.source_reference = (
                r.source_reference + " | " if r.source_reference else ""
            ) + f"user_verified: {o.id} ({o.topic})"
        elif o.applies_to == "certification":
            c = certs[o.target_id]
            setattr(c, o.field, o.value)
        elif o.applies_to == "conflict":
            pass  # resolution only
        else:
            raise ValueError(f"override {o.id}: unknown applies_to {o.applies_to!r}")
        if o.conflict_id:
            c = conflicts[o.conflict_id]
            c.resolved_by = f"user_verified:{o.id}"
            c.resolution = (
                f"user_verified ({o.confirmed_at}): {o.value if o.value is not None else o.note}"
            )
        for rid, ver in o.record_verification.items():
            records[rid].verification = Verification(ver)
        for cid, ver in o.certification_verification.items():
            certs[cid].verification = Verification(ver)
            certs[cid].overrides_applied.append(o.id)
    bank.overrides = list(overrides)
    bank.sources.setdefault("user_verified", "data/evidence/user_overrides.json")
    return bank


class EvidenceIndex:
    """Read-only query surface over an ``EvidenceBank``."""

    def __init__(self, bank: EvidenceBank):
        self.bank = bank
        self.by_id: dict[str, EvidenceRecord] = {r.id: r for r in bank.records}
        self.positions: dict[str, Position] = {p.id: p for p in bank.positions}
        self._terms_cache: dict[str, frozenset[str]] = {}
        self.by_position: dict[str, list[EvidenceRecord]] = defaultdict(list)
        for r in bank.records:
            self.by_position[r.position_id].append(r)
        dup = len(bank.records) - len(self.by_id)
        if dup:
            raise ValueError(f"evidence bank has {dup} duplicate ids")
        missing = [r.id for r in bank.records if r.position_id not in self.positions]
        if missing:
            raise ValueError(f"records reference unknown positions: {missing}")
        for r in bank.records:
            p = self.positions[r.position_id]
            if p.kind != PositionKind.CROSS_CUTTING and (
                r.start < p.start or (p.end and r.end and r.end > p.end)
            ):
                raise ValueError(
                    f"record {r.id} dates {r.start}..{r.end} fall outside position {p.id} {p.start}..{p.end}"
                )

    # ---- terms ---------------------------------------------------------
    @cached_property
    def term_index(self) -> dict[str, set[str]]:
        idx: dict[str, set[str]] = defaultdict(set)
        for r in self.default_records():  # excluded-by-default records never vouch for anything
            for t in self.record_terms(r):
                idx[t].add(r.id)
        return idx

    def record_terms(self, r: EvidenceRecord) -> set[str]:
        """Canonical terms this record vouches for (explicit lists + terms found in its text).

        Cached per record id: the lexicon regex over a record's text costs ~15 ms and the
        matcher asks for every record on every requirement."""
        cached = self._terms_cache.get(r.id)
        if cached is None:
            terms = {canonical(t) for t in r.terms}
            terms.update(find_terms(f"{r.claim} {r.resume_text} {r.detailed_context}"))
            cached = frozenset(terms - {canonical(t) for t in r.not_evidence_for})
            self._terms_cache[r.id] = cached
        return set(cached)

    @cached_property
    def all_terms(self) -> set[str]:
        return set(self.term_index.keys())

    def allowed_terms(self, evidence_ids: list[str]) -> set[str]:
        out: set[str] = set()
        for eid in evidence_ids:
            r = self.by_id.get(eid)
            if r:
                out |= self.record_terms(r)
        return out

    # ---- numbers -------------------------------------------------------
    def record_numbers(self, r: EvidenceRecord) -> set[str]:
        nums: set[str] = set()
        blob = " ".join([r.claim, r.resume_text, r.detailed_context, *r.metrics])
        for n in numbers_in(blob):
            nums |= number_variants(n)
        # the record's own dates and the official dates of the position it belongs to
        # (a resume may say "At <Employer> (2025-2026)" about any of that employer's records)
        pos = self.positions.get(r.position_id)
        for d in (r.start, r.end, pos.start if pos else None, pos.end if pos else None):
            if d:
                nums.add(d[:4])
        return nums

    def allowed_numbers(self, evidence_ids: list[str]) -> set[str]:
        out: set[str] = set()
        for eid in evidence_ids:
            r = self.by_id.get(eid)
            if r:
                out |= self.record_numbers(r)
        return out

    @staticmethod
    def record_text(r: EvidenceRecord) -> str:
        return " ".join(
            [
                r.claim,
                r.resume_text,
                r.summary_text or "",
                r.detailed_context,
                *r.responsibilities,
                *r.skills,
            ]
        ).lower()

    # ---- provenance ----------------------------------------------------
    def strength(self, r: EvidenceRecord):
        from resume_tailor.core.evidence.provenance import source_strength

        return source_strength(r, self.by_id)

    def secondary_only(self, r: EvidenceRecord) -> bool:
        from resume_tailor.core.evidence.provenance import is_secondary_only

        return is_secondary_only(r, self.by_id)

    def effective_proficiency(self, r: EvidenceRecord, term: str | None = None):
        from resume_tailor.core.evidence.provenance import effective_proficiency

        return effective_proficiency(r, term, self.by_id)

    # ---- tenure --------------------------------------------------------
    def tenure_years(self, basis: str = "professional") -> float:
        """Years of experience on an explicit basis (see ``TENURE_BASES``), overlaps merged."""
        kinds = TENURE_BASES[basis]
        spans = [(p.start, p.end) for p in self.bank.positions if p.kind in kinds]
        return _merged_years(spans)

    def tenure_summary(self) -> dict[str, float]:
        return {b: self.tenure_years(b) for b in TENURE_BASES}

    def total_experience_years(self, include_optional: bool = False) -> float:
        """Backwards-compatible alias: professional tenure, or total relevant when ``include_optional``."""
        return self.tenure_years("total_relevant" if include_optional else "employment")

    def position_months(self, position_id: str) -> int:
        p = self.positions[position_id]
        return months_between(p.start, p.end)

    def default_records(self) -> list[EvidenceRecord]:
        return [r for r in self.bank.records if r.include_by_default]


def _merged_years(spans: list[tuple[str, str | None]]) -> float:
    if not spans:
        return 0.0

    def key(s: str) -> int:
        return int(s[:4]) * 12 + int(s[5:7])

    now = date.today()  # noqa: DTZ011 - tenure uses the local calendar date.
    now_k = now.year * 12 + now.month
    ranges = sorted((key(s), key(e) if e else now_k) for s, e in spans)
    merged: list[list[int]] = []
    for s, e in ranges:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return round(sum(e - s for s, e in merged) / 12, 1)


def load_overrides(path: Path | None) -> list[UserOverride]:
    if path is None or not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [UserOverride.model_validate(o) for o in data.get("overrides", [])]


def load_bank(path: Path, overrides_path: Path | None = None) -> EvidenceBank:
    data = json.loads(path.read_text(encoding="utf-8"))
    bank = EvidenceBank.model_validate(data)
    if overrides_path is None:
        overrides_path = path.with_name("user_overrides.json")
    return apply_overrides(bank, load_overrides(overrides_path))


def load_index(path: Path, overrides_path: Path | None = None) -> EvidenceIndex:
    return EvidenceIndex(load_bank(path, overrides_path))
