"""Shared facts in the catalogue, per-profile results in the profile (ADR-0037).

`FeatureStore.ensure` answers "the shared facts of this posting" from what is
stored when every component's stamp is still the one this build would write,
and computes only the stale components otherwise. `MatchStore.ensure` does the
same for one profile. Both count what they reused and what they computed, so
a test or a benchmark can say how much work a change caused.

The stamp of every component is computable WITHOUT reading the advert (it is
versions plus input digests), which is what makes "is this stale?" cheap.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from career_agent.config.search_config import SearchConfig
from career_agent.match.engine import JobFacts
from career_agent.matching_v2 import components as comp
from career_agent.matching_v2 import features as feat
from career_agent.matching_v2 import profile_match as pm


def _upsert(table: str) -> str:
    return (
        f"INSERT INTO {table} (job_id, component, content_hash, stamp, payload_json, computed_at)"
        " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(job_id, component) DO UPDATE SET"
        " content_hash = excluded.content_hash, stamp = excluded.stamp,"
        " payload_json = excluded.payload_json, computed_at = excluded.computed_at"
    )


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Tally:
    reused: Counter = field(default_factory=Counter)
    computed: Counter = field(default_factory=Counter)
    #: How many times an advert was read (the expensive half).
    reads: int = 0


class FeatureStore:
    """`job_features`: one row per (posting, component), in the catalogue."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.tally = Tally()

    def stored(self, job_id: str) -> dict[str, tuple[str, Any]]:
        rows = self.conn.execute(
            "SELECT component, stamp, payload_json FROM job_features WHERE job_id = ?", (job_id,)
        ).fetchall()
        return {str(r[0]): (str(r[1]), json.loads(r[2])) for r in rows}

    def ensure(
        self, job: JobFacts, content_hash: str, headings: tuple[tuple[str, ...], tuple[str, ...]]
    ) -> feat.JobFeatures:
        inputs = feat.job_inputs(job, content_hash, headings)
        expected = {name: comp.stamp(name, inputs) for name in comp.JOB_COMPONENTS}
        stored = self.stored(str(job.job_id))
        stale = {n for n, s in expected.items() if stored.get(n, ("",))[0] != s}
        payloads = {n: stored[n][1] for n in expected if n not in stale}
        shared = None
        if stale:
            fresh = feat.extract(job, content_hash, headings, only=stale)
            self.tally.reads += 1
            shared = fresh.shared
            payloads.update(fresh.payloads)
            stamp_at = _now()
            self.conn.executemany(
                _upsert("job_features"),
                [
                    (
                        str(job.job_id),
                        n,
                        content_hash,
                        expected[n],
                        json.dumps(fresh.payloads[n]),
                        stamp_at,
                    )
                    for n in sorted(stale)
                ],
            )
        self.tally.computed.update(stale)
        self.tally.reused.update(set(expected) - stale)
        return feat.JobFeatures(
            str(job.job_id), content_hash, payloads, expected, int(bool(stale)), shared
        )


class MatchStore:
    """`profile_job_match`: one row per (posting, component), in a profile."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.tally = Tally()

    def stored(self, job_id: str) -> dict[str, tuple[str, Any]]:
        rows = self.conn.execute(
            "SELECT component, stamp, payload_json FROM profile_job_match WHERE job_id = ?",
            (job_id,),
        ).fetchall()
        return {str(r[0]): (str(r[1]), json.loads(r[2])) for r in rows}

    def expected(self, config: SearchConfig, job: JobFacts, content_hash: str) -> dict[str, str]:
        headings = (
            tuple(config.prominence.primary_headings),
            tuple(config.prominence.secondary_headings),
        )
        inputs = {**feat.job_inputs(job, content_hash, headings), **pm.profile_inputs(config)}
        return dict(pm._stamps(tuple(sorted(inputs.items()))))

    def stale(self, config: SearchConfig, job: JobFacts, content_hash: str) -> set[str]:
        expected = self.expected(config, job, content_hash)
        stored = self.stored(str(job.job_id))
        return {n for n, s in expected.items() if stored.get(n, ("",))[0] != s}

    def ensure(
        self,
        config: SearchConfig,
        job: JobFacts,
        content_hash: str,
        features: feat.JobFeatures,
    ) -> dict[str, Any]:
        """The profile's components for one posting; computes when any is stale.

        The profile half is one call (`profile_match.match`): it is cheap, and
        recomputing a component in isolation would duplicate the arithmetic of
        `match_job`. What the stamps decide is WHETHER to call it, and which
        rows it replaces."""
        expected = self.expected(config, job, content_hash)
        stored = self.stored(str(job.job_id))
        stale = {n for n, s in expected.items() if stored.get(n, ("",))[0] != s}
        if not stale:
            self.tally.reused.update(expected)
            return {n: stored[n][1] for n in expected}
        result = pm.match(config, job, features)
        payloads = _payloads(config, result)
        stamp_at = _now()
        self.conn.executemany(
            _upsert("profile_job_match"),
            [
                (str(job.job_id), n, content_hash, expected[n], json.dumps(payloads[n]), stamp_at)
                for n in sorted(stale)
            ],
        )
        self.tally.computed.update(stale)
        self.tally.reused.update(set(expected) - stale)
        return {n: (payloads[n] if n in stale else stored[n][1]) for n in expected}


def _payloads(config: SearchConfig, m: pm.ProfileJobMatch) -> dict[str, Any]:
    r = m.result
    by_id = {c.component_id: c for c in r.components}

    def component(component_id: str) -> dict[str, Any] | None:
        found = by_id.get(component_id)
        if found is None:
            return None
        return {"id": found.component_id, "points": found.points, "max": found.max_points}

    def evidence(kind: set[str]) -> list[dict[str, Any]]:
        return [
            {
                "signal": e.signal_id,
                "state": e.state,
                "relation": e.relation,
                "context": e.context,
                "quote": e.quote,
            }
            for e in m.evidence
            if e.signal_id in kind
        ]

    # Which phrases are work and which are tools is the person's own
    # configuration (the weights of each component), never a name rule.
    parts = config.scoring.components
    work = set(parts.responsibilities.weights) | set(parts.automation_integration.weights)
    tools = set(parts.technologies.weights)
    return {
        "work_match": {"component": component("responsibilities"), "evidence": evidence(work)},
        "tools_match": {"component": component("technologies"), "evidence": evidence(tools)},
        "eligibility": {
            "status": str(r.eligibility_status),
            "gates": [[g.gate, str(g.result), g.code] for g in r.gates],
        },
        "compensation_match": {"component": component("compensation_contract")},
        "seniority_match": {"component": component("seniority")},
        "score": {
            "match_score": r.match_score,
            "fit_band": str(r.fit_band),
            "screening": str(r.screening_state),
            "uncertain": len(m.uncertain),
        },
    }
