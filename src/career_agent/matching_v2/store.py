"""Shared facts in the catalogue, per-profile results in the profile (ADR-0037).

`FeatureStore.ensure` answers "the shared facts of this posting" from what is
stored when no component is stale, and computes only the stale components
otherwise. `MatchStore.ensure` does the same for one profile. Both count what
they reused and what they computed, so a test or a benchmark can say how much
work a change caused.

ONE COMPACT ROW PER POSTING (migrations 0056 and 0057). A row keeps the
digests of the inputs it was made from and the id of a versions map; whether
a component is stale is `components.stale` over those two, never a read of a
payload. Evidence is stored as spans of the posting's text and quoted from it
when shown. Measured sizes are in `docs/checkpoints/matching-architecture-v2.md`.
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

#: Stored next to each other in `job_features`; everything else of a posting
#: is recomputed on demand (`features.work_units`, `resolve_place`).
_FACT_COMPONENTS = ("text_parser", "language", "pay_facts", "seniority_facts", "engagement_facts")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pack(digests: dict[str, str]) -> str:
    return json.dumps(digests, sort_keys=True, separators=(",", ":"))


def _versions_id(conn: sqlite3.Connection, table: str, versions: dict[str, str]) -> str:
    vid = comp.digest(versions)[:12]
    conn.execute(
        f"INSERT INTO {table} (id, versions_json) VALUES (?, ?) ON CONFLICT(id) DO NOTHING",
        (vid, _pack(versions)),
    )
    return vid


def _versions_of(conn: sqlite3.Connection, table: str, vid: str) -> dict[str, str]:
    row = conn.execute(f"SELECT versions_json FROM {table} WHERE id = ?", (vid,)).fetchone()
    return json.loads(row[0]) if row else {}


@dataclass
class Tally:
    reused: Counter = field(default_factory=Counter)
    computed: Counter = field(default_factory=Counter)
    #: How many times an advert was read (the expensive half).
    reads: int = 0


class FeatureStore:
    """`job_features`: one compact row per posting, in the catalogue."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.tally = Tally()
        self._versions: dict[str, dict[str, str]] = {}

    def _versions_map(self, vid: str) -> dict[str, str]:
        if vid not in self._versions:
            self._versions[vid] = _versions_of(self.conn, "job_feature_versions", vid)
        return self._versions[vid]

    def stored(self, job_id: str) -> tuple[dict[str, str], dict[str, str], dict[str, Any]] | None:
        """(versions, inputs, payloads) of a stored posting, or None."""
        row = self.conn.execute(
            "SELECT inputs, versions, sentences, facts_json FROM job_features WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        payloads: dict[str, Any] = json.loads(row[3])
        payloads["sentence_context"] = row[2]
        payloads.setdefault("work_facts", None)
        payloads.setdefault("location_facts", None)
        return self._versions_map(row[1]), json.loads(row[0]), payloads

    def ensure(
        self, job: JobFacts, content_hash: str, headings: tuple[tuple[str, ...], tuple[str, ...]]
    ) -> feat.JobFeatures:
        inputs = feat.job_inputs(job, content_hash, headings)
        expected = {name: comp.stamp(name, inputs) for name in comp.JOB_COMPONENTS}
        held = self.stored(str(job.job_id))
        if held is None:
            stale = set(comp.JOB_COMPONENTS)
            versions: dict[str, str] = {}
            payloads: dict[str, Any] = {}
        else:
            versions, stored_inputs, payloads = held
            stale = comp.stale(comp.JOB_COMPONENTS, versions, stored_inputs, inputs)
        shared = None
        if stale:
            fresh = feat.extract(job, content_hash, headings, only=stale)
            self.tally.reads += 1
            shared = fresh.shared
            payloads = {**payloads, **fresh.payloads}
            versions = {**versions, **comp.versions(stale)}
            self._write(job, content_hash, inputs, versions, payloads)
        self.tally.computed.update(stale)
        self.tally.reused.update(set(expected) - stale)
        return feat.JobFeatures(
            str(job.job_id), content_hash, payloads, expected, int(bool(stale)), shared
        )

    def _write(
        self,
        job: JobFacts,
        content_hash: str,
        inputs: dict[str, str],
        versions: dict[str, str],
        payloads: dict[str, Any],
    ) -> None:
        vid = _versions_id(self.conn, "job_feature_versions", versions)
        facts = {k: payloads[k] for k in _FACT_COMPONENTS if payloads.get(k) is not None}
        self.conn.execute(
            "INSERT INTO job_features (job_id, content_hash, inputs, versions, sentences,"
            " facts_json, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(job_id) DO UPDATE SET content_hash = excluded.content_hash,"
            " inputs = excluded.inputs, versions = excluded.versions,"
            " sentences = excluded.sentences, facts_json = excluded.facts_json,"
            " computed_at = excluded.computed_at",
            (
                str(job.job_id),
                content_hash,
                _pack(inputs),
                vid,
                payloads.get("sentence_context") or "",
                json.dumps(facts, separators=(",", ":"), ensure_ascii=False),
                _now(),
            ),
        )


_STATE = {pm.MATCH: "M", pm.UNCERTAIN: "U", pm.EXCLUDED_CONTEXT: "X"}


def compact_result(m: pm.ProfileJobMatch) -> dict[str, Any]:
    """What a profile's answer stores: points per component, gates, and the
    evidence as spans (`[signal, state, relation, context, start, end,
    field, cause]`). Quotes are cut from the posting when shown."""
    r = m.result
    return {
        "components": {c.component_id: [c.points, c.max_points] for c in r.components},
        "penalty": r.penalty_total,
        "gates": [[g.gate, str(g.result), g.code, g.char_start, g.char_end] for g in r.gates],
        "evidence": [
            [
                e.signal_id,
                _STATE[e.state],
                e.relation,
                e.context,
                e.start,
                e.end,
                "t" if e.context == "TITLE" else "d",
                e.reason,
            ]
            for e in m.evidence
        ],
    }


class MatchStore:
    """`profile_job_match`: one compact row per posting, in a profile."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.tally = Tally()
        self._versions: dict[str, dict[str, str]] = {}
        self._inputs: dict[str, dict[str, str]] = {}

    def _inputs_id(self, profile: dict[str, str]) -> str:
        pid = comp.digest(profile)[:12]
        if pid not in self._inputs:
            self.conn.execute(
                "INSERT INTO profile_match_inputs (id, inputs_json) VALUES (?, ?)"
                " ON CONFLICT(id) DO NOTHING",
                (pid, _pack(profile)),
            )
            self._inputs[pid] = profile
        return pid

    def _held(self, job_id: str) -> tuple[dict[str, str], dict[str, str], dict[str, Any]] | None:
        row = self.conn.execute(
            "SELECT job_inputs, profile_inputs, versions, match_score, fit_band,"
            " eligibility_status, screening_state, result_json"
            " FROM profile_job_match WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        pid, vid = str(row[1]), str(row[2])
        if pid not in self._inputs:
            got = self.conn.execute(
                "SELECT inputs_json FROM profile_match_inputs WHERE id = ?", (pid,)
            ).fetchone()
            self._inputs[pid] = json.loads(got[0]) if got else {}
        if vid not in self._versions:
            self._versions[vid] = _versions_of(self.conn, "profile_match_versions", vid)
        inputs = {**json.loads(row[0]), **self._inputs[pid]}
        answer = {
            "match_score": row[3],
            "fit_band": row[4],
            "eligibility_status": row[5],
            "screening_state": row[6],
            **json.loads(row[7]),
        }
        return self._versions[vid], inputs, answer

    def _current_inputs(
        self, config: SearchConfig, job: JobFacts, content_hash: str
    ) -> dict[str, str]:
        headings = (
            tuple(config.prominence.primary_headings),
            tuple(config.prominence.secondary_headings),
        )
        return {**feat.job_inputs(job, content_hash, headings), **pm.profile_inputs(config)}

    def stale(self, config: SearchConfig, job: JobFacts, content_hash: str) -> set[str]:
        held = self._held(str(job.job_id))
        if held is None:
            return set(comp.PROFILE_COMPONENTS)
        versions, inputs, _ = held
        current = self._current_inputs(config, job, content_hash)
        return comp.stale(comp.PROFILE_COMPONENTS, versions, inputs, current)

    def ensure(
        self,
        config: SearchConfig,
        job: JobFacts,
        content_hash: str,
        features: feat.JobFeatures,
    ) -> dict[str, Any]:
        """The profile's answer for one posting; computed when any component
        is stale. The profile half is one call (`profile_match.match`): it is
        cheap, and recomputing a component in isolation would duplicate the
        arithmetic of `match_job`. What staleness decides is WHETHER to call
        it, and what the tally says moved."""
        current = self._current_inputs(config, job, content_hash)
        held = self._held(str(job.job_id))
        if held is None:
            stale = set(comp.PROFILE_COMPONENTS)
        else:
            versions, inputs, answer = held
            stale = comp.stale(comp.PROFILE_COMPONENTS, versions, inputs, current)
            if not stale:
                self.tally.reused.update(comp.PROFILE_COMPONENTS)
                return answer
        m = pm.match(config, job, features)
        r = m.result
        result = compact_result(m)
        # The whole lineage: a profile component is stale when a shared
        # component it depends on moved, too.
        vid = _versions_id(self.conn, "profile_match_versions", comp.versions(comp.REGISTRY))
        profile_in = pm.profile_inputs(config)
        job_in = {k: v for k, v in current.items() if k not in profile_in}
        self.conn.execute(
            "INSERT INTO profile_job_match (job_id, content_hash, job_inputs, profile_inputs,"
            " versions, match_score, fit_band, eligibility_status, screening_state,"
            " result_json, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(job_id) DO UPDATE SET content_hash = excluded.content_hash,"
            " job_inputs = excluded.job_inputs, profile_inputs = excluded.profile_inputs,"
            " versions = excluded.versions, match_score = excluded.match_score,"
            " fit_band = excluded.fit_band, eligibility_status = excluded.eligibility_status,"
            " screening_state = excluded.screening_state, result_json = excluded.result_json,"
            " computed_at = excluded.computed_at",
            (
                str(job.job_id),
                content_hash,
                _pack(job_in),
                self._inputs_id(profile_in),
                vid,
                r.match_score,
                str(r.fit_band),
                str(r.eligibility_status),
                str(r.screening_state),
                json.dumps(result, separators=(",", ":"), ensure_ascii=False),
                _now(),
            ),
        )
        self.tally.computed.update(stale)
        self.tally.reused.update(set(comp.PROFILE_COMPONENTS) - stale)
        return {
            "match_score": r.match_score,
            "fit_band": str(r.fit_band),
            "eligibility_status": str(r.eligibility_status),
            "screening_state": str(r.screening_state),
            **result,
        }
