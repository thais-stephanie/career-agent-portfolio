"""Phase D: which reader decides work matching in a pass (ADR-0037).

Two sources. `legacy` (the default) is readers-12 as shipped: `match_job`
finds a profile's phrases in the advert itself. `semantic_v2` hands
`match_job` the signals `profile_match` matches from the posting's SHARED
semantic facts (text structure, sentence contexts, evidence notes), stored
once in the catalogue (`job_features`), weighed by the profile's evidence
policy. The gates, seniority, pay, engagement and the score's arithmetic are
the same code in both.

The source is `CAREER_AGENT_WORK_MATCH_SOURCE` (`match.identity`), read by
the process, so a change takes effect at a restart. It moves the reader
identity (`identity.reader_identity`): turning it on makes every stored
score read the other way, so the next targeted pass recomputes them, and
turning it back makes the same pass recompute them the legacy way. Nothing
is migrated in either direction and nothing is deleted.

A POSTING WITHOUT CURRENT FACTS IS READ, NEVER GUESSED: no stored row, a
row written under another component version or other inputs, or a
catalogue without the table, and the facts are computed for this pass in
memory (counted, never written). Facts are materialised by `materialize`.

ELIGIBILITY keeps its own switch (`phase_c`): with it on `legacy` the
stored eligibility facts are dropped and the gates read the advert.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field, replace

from career_agent.config.search_config import SearchConfig
from career_agent.domain.matching import MatchResult
from career_agent.match.engine import JobFacts, SemanticEvidence
from career_agent.match.gates import negation_key
from career_agent.match.identity import (
    WORK_MATCH_ENV,
    WORK_MATCH_LEGACY,
    WORK_MATCH_SEMANTIC,
    work_match_source,
)
from career_agent.matching_v2 import components as comp
from career_agent.matching_v2 import features as feat
from career_agent.matching_v2 import phase_c
from career_agent.matching_v2 import profile_match as pm
from career_agent.matching_v2.store import FeatureStore

ENV = WORK_MATCH_ENV
LEGACY = WORK_MATCH_LEGACY
SEMANTIC_V2 = WORK_MATCH_SEMANTIC

#: What a profile's semantic answer reads from the stored row, with lineage.
_NEEDED = ("work_facts", "pay_facts", "seniority_facts", "engagement_facts", "eligibility_facts")


@dataclass
class WorkMatchReader:
    """One pass's semantic work matcher, with its counts."""

    conn: sqlite3.Connection
    config: SearchConfig
    environ: dict[str, str] | None = None
    counts: Counter = field(default_factory=Counter)

    def __post_init__(self) -> None:
        self._store = FeatureStore(self.conn)
        self._headings = (
            tuple(self.config.prominence.primary_headings),
            tuple(self.config.prominence.secondary_headings),
        )
        self._negation = negation_key(self.config.negation)
        self._available = phase_c._has_table(self.conn, "job_features")
        self._stored_eligibility = phase_c.eligibility_source(self.environ) == phase_c.V2

    def match(
        self,
        job: JobFacts,
        content_hash: str,
        *,
        computed_at: str,
        semantic: SemanticEvidence | None = None,
    ) -> MatchResult:
        features = self._features(job, content_hash)
        shared = features.shared or feat.shared_from_facts(job, features)
        if not self._stored_eligibility:
            shared = replace(shared, eligibility=None)
        features.shared = shared
        return pm.match(
            self.config, job, features, computed_at=computed_at, semantic=semantic
        ).result

    def _features(self, job: JobFacts, content_hash: str) -> feat.JobFeatures:
        held = self._store.stored(str(job.job_id)) if self._available else None
        if held is not None:
            versions, inputs, payloads = held
            current = feat.job_inputs(job, content_hash, self._headings, self._negation)
            if not comp.stale(_NEEDED, versions, inputs, current):
                self.counts["from_store"] += 1
                return feat.JobFeatures(str(job.job_id), content_hash, payloads, {}, reads=0)
            self.counts["read_stale"] += 1
        else:
            self.counts["read_no_table" if not self._available else "read_not_materialised"] += 1
        return feat.extract(job, content_hash, self._headings, negation=self.config.negation)


def reader_for(
    conn: sqlite3.Connection, config: SearchConfig, environ: dict[str, str] | None = None
) -> WorkMatchReader | None:
    """The pass's semantic reader when the source is `semantic_v2`, else None."""
    if work_match_source(environ) == SEMANTIC_V2:
        return WorkMatchReader(conn, config, environ)
    return None
