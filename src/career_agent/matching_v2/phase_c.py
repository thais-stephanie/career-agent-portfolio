"""Phase C: where a pass reads a posting's eligibility facts from (ADR-0037).

Two sources, one answer. `legacy` (the default) is readers-12 as shipped:
each profile reads what a posting states about who may take it from the
advert itself (`gates.evaluate_gates` with no facts). `v2` hands the gates
the posting's SHARED eligibility facts, stored once in the catalogue
(`job_features`), so a profile decides without reading the advert again.
The decision is the same code over the same facts (`read_eligibility_facts`
is what both run), which is why this switch moves no score, no gate and no
reader identity: no readers-13, no recalculation.

ONLY ELIGIBILITY. Every other reading of the posting (sections, seniority,
engagement, pay, work matching) stays the production one: `read_shared`
without facts, then the stored facts attached. Work matching and the score
are Phase D and are not reachable from here.

A POSTING WITHOUT CURRENT FACTS IS READ, NEVER GUESSED. No stored row, a row
written under another component version, another text, other board metadata,
other headings or other negation lists (`components.stale`), or a catalogue
without the table: the gates read the advert, exactly as `legacy` does, and
the pass counts it. Facts are materialised by `materialize`, never by a
rescore, so turning v2 on writes nothing and needs nothing first.

ROLLBACK is the environment variable back to `legacy` (or unset). Nothing is
rebuilt and nothing is migrated back: `job_features` stays where it is, read
by nobody.
"""

from __future__ import annotations

import os
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace

from career_agent.config.search_config import SearchConfig
from career_agent.match.engine import JobFacts, SharedReading, read_shared
from career_agent.match.gates import negation_key
from career_agent.matching_v2 import components as comp
from career_agent.matching_v2 import features as feat
from career_agent.matching_v2.store import FeatureStore

ENV = "CAREER_AGENT_ELIGIBILITY_SOURCE"
LEGACY = "legacy"
V2 = "v2"
SOURCES = (LEGACY, V2)

#: What the eligibility facts read: `eligibility_facts` and its lineage.
_ELIGIBILITY = ("eligibility_facts",)


def eligibility_source(environ: dict[str, str] | None = None) -> str:
    """The configured source; unset is `legacy`. A value that is neither is
    refused by name rather than read as either: a typo must not decide which
    reader runs."""
    raw = (environ if environ is not None else os.environ).get(ENV, "").strip().lower()
    if not raw:
        return LEGACY
    if raw not in SOURCES:
        raise ValueError(f"{ENV}={raw!r}: expected one of {', '.join(SOURCES)}")
    return raw


@dataclass
class EligibilityReader:
    """One pass's reader of stored eligibility facts, with its counts."""

    conn: sqlite3.Connection
    config: SearchConfig
    counts: Counter = field(default_factory=Counter)

    def __post_init__(self) -> None:
        self._store = FeatureStore(self.conn)
        self._headings = (
            tuple(self.config.prominence.primary_headings),
            tuple(self.config.prominence.secondary_headings),
        )
        self._negation = negation_key(self.config.negation)
        self._available = _has_table(self.conn, "job_features")

    def shared(self, job: JobFacts, content_hash: str) -> SharedReading:
        """The production reading of `job` with its stored eligibility facts
        attached, or without any (the gates then read the advert)."""
        base = read_shared(job, list(self._headings[0]), list(self._headings[1]))
        facts = self._stored_facts(job, content_hash)
        if facts is None:
            return base
        return replace(base, eligibility=facts)

    def _stored_facts(self, job: JobFacts, content_hash: str):
        if not self._available:
            self.counts["read_no_table"] += 1
            return None
        held = self._store.stored(str(job.job_id))
        if held is None:
            self.counts["read_not_materialised"] += 1
            return None
        versions, inputs, payloads = held
        current = feat.job_inputs(job, content_hash, self._headings, self._negation)
        if comp.stale(_ELIGIBILITY, versions, inputs, current) or (
            payloads.get("eligibility_facts") is None
        ):
            self.counts["read_stale"] += 1
            return None
        self.counts["from_store"] += 1
        return feat.eligibility_from_dict(payloads["eligibility_facts"], job.description)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    for schema in ("main", "catalogue"):
        try:
            row = conn.execute(
                f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?", (name,)
            ).fetchone()
        except sqlite3.OperationalError:  # no such schema
            continue
        if row:
            return True
    return False


def reader_for(
    conn: sqlite3.Connection, config: SearchConfig, environ: dict[str, str] | None = None
) -> EligibilityReader | None:
    """The pass's reader when the source is `v2`, else None (legacy)."""
    return EligibilityReader(conn, config) if eligibility_source(environ) == V2 else None


def materialize(
    conn: sqlite3.Connection,
    config: SearchConfig,
    jobs: Iterable[tuple[JobFacts, str]],
    *,
    should_stop: Callable[[], bool] = lambda: False,
) -> Counter:
    """Write the shared facts of `jobs` (in the order given: the caller puts
    the eligible and the best-scored first) that are missing or stale. Fresh
    rows are left alone, so a run interrupted anywhere resumes by being run
    again. Each posting is its own write."""
    store = FeatureStore(conn)
    headings = (
        tuple(config.prominence.primary_headings),
        tuple(config.prominence.secondary_headings),
    )
    done: Counter = Counter()
    for job, content_hash in jobs:
        if should_stop():
            done["stopped"] += 1
            break
        before = store.tally.reads
        store.ensure(job, content_hash, headings, negation=config.negation)
        done["written" if store.tally.reads > before else "fresh"] += 1
    return done


def priority_order(conn: sqlite3.Connection, config: SearchConfig) -> list[str]:
    """Open postings with text, in the order their facts are worth having:
    the ones this profile's stored score says are eligible first, then by
    stored score, then every other posting. Postings already current are
    kept in the order and skipped cheaply by `materialize`."""
    rows = conn.execute(
        "SELECT j.id FROM job j LEFT JOIN job_match m ON m.job_id = j.id"
        " AND m.config_id = ? AND m.config_version = ?"
        " WHERE j.closed_at IS NULL AND j.content_hash IS NOT NULL"
        " ORDER BY (m.eligibility_status = 'VERIFIED_ELIGIBLE') DESC,"
        " coalesce(m.match_score, -1) DESC, j.id",
        (str(config.config_id), int(config.config_version)),
    )
    return [str(r[0]) for r in rows]
