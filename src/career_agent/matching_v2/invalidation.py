"""Which postings a change can actually move, before recomputing anything.

A version bump makes every artefact of a component stale by stamp. For a
change to the LANGUAGE TABLE that is too broad: adding `estoque = inventory`
can only move postings that contain `estoque`. `language_change` compares two
tables, finds the words whose canonical root moved, and names the postings
whose stored work facts contain one of them; every other posting's work facts
would come out identical and can be re-stamped without being read again
(`restamp`). Selection reads the stored payloads, never an advert.

A prototype: it scans `job_features` rows. An index of canonical roots per
posting is the next step when a real table change shows the scan matters.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from career_agent.matching_v2.language import LanguageTable


@dataclass(frozen=True)
class LanguageChange:
    #: Canonical roots, as stored by the OLD table, whose meaning moved.
    old_roots: frozenset[str]
    #: First roots of fixed expressions that appeared, disappeared or moved,
    #: as the OLD table tokenised them (a sequence starts with one).
    sequence_heads: frozenset[str]

    def touches(self, stored_roots: set[str]) -> bool:
        return bool(stored_roots & (self.old_roots | self.sequence_heads))


def diff(old: LanguageTable, new: LanguageTable) -> LanguageChange:
    """The stored roots a move from `old` to `new` can change."""
    moved: set[str] = set()
    for key in set(old.words) | set(new.words):
        if old.words.get(key) != new.words.get(key):
            moved.add(old.canonical(key)[0])
            moved.add(key)
    for form in set(old.surface) | set(new.surface):
        if old.surface.get(form) != new.surface.get(form):
            moved.add(old.surface.get(form, form))
    if old.morphology != new.morphology:
        # A rule on roots can move any root ending in its suffix.
        suffixes = {s for _, s in set(old.morphology) ^ set(new.morphology)}
        moved |= {f"*{s}" for s in suffixes}
    heads: set[str] = set()
    for seq in set(old.fixed) | set(new.fixed):
        if old.fixed.get(seq) != new.fixed.get(seq):
            heads.add(old.canonical(seq[0])[0])
            heads.add(seq[0])
    return LanguageChange(frozenset(moved), frozenset(heads))


def _stored_roots(payload: dict) -> set[str]:
    roots = {t[0] for t in payload.get("title", [])}
    for _s, _e, _c, toks in payload.get("sentences", []):
        roots |= {t[0] for t in toks}
    return roots


def affected_jobs(conn: sqlite3.Connection, change: LanguageChange) -> tuple[list[str], list[str]]:
    """(postings the change can move, postings it cannot), from stored facts."""
    suffix_rules = {r[1:] for r in change.old_roots if r.startswith("*")}
    plain = LanguageChange(
        frozenset(r for r in change.old_roots if not r.startswith("*")), change.sequence_heads
    )
    moved, unmoved = [], []
    for job_id, payload in conn.execute(
        "SELECT job_id, payload_json FROM job_features WHERE component = 'work_facts'"
    ):
        roots = _stored_roots(json.loads(payload))
        hit = plain.touches(roots) or any(r.endswith(s) for r in roots for s in suffix_rules)
        (moved if hit else unmoved).append(str(job_id))
    return moved, unmoved


def restamp(
    conn: sqlite3.Connection, job_ids: list[str], component: str, stamps: dict[str, str]
) -> int:
    """Give unaffected postings the new stamp without recomputing them."""
    conn.executemany(
        "UPDATE job_features SET stamp = ? WHERE job_id = ? AND component = ?",
        [(stamps[j], j, component) for j in job_ids],
    )
    return len(job_ids)
