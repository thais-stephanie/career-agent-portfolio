"""Storing an extraction: the document, its citations, and what was asked.

Two repositories, because they answer to different lifecycles. A fingerprint is
written once a document has survived assembly, verification and validation. An
`llm_call` row is written whether or not anything survived -- it is the record
of what a model actually said, and a table that only holds successes is a table
that cannot explain a regression.

WHY THE DOCUMENT IS STORED TWICE
--------------------------------
`fingerprint.data_json` holds the whole document; the child tables hold the
same observations as rows. That is deliberate duplication with a purpose: M3,
M4 and M5 ask questions *across* jobs -- "which postings have Salesforce at
CORE?", "how many state a hard language requirement?" -- and answering those by
parsing thousands of JSON blobs is a decision that is cheap today and immovable
later. The JSON stays authoritative; the rows are an index.

VERIFICATION IS STORED, NOT RECOMPUTED
--------------------------------------
`evidence.verified` and `match_kind` come from the verification report produced
during extraction. They are not re-derived here, because re-deriving would mean
a second implementation of the verifier and the row would eventually disagree
with the run that produced it.
"""

import json
import sqlite3
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from career_agent.clock import new_id, now_utc
from career_agent.domain.enums import EvidenceSourceKind
from career_agent.domain.fingerprint import JobFingerprint
from career_agent.domain.verify import VerificationReport
from career_agent.storage.records import CachedAnswer, LLMCallRecord

#: How many cache keys go into one `IN (...)` lookup. SQLite's default host
#: parameter limit is 999; this leaves room and keeps the statement readable.
_LOOKUP_CHUNK = 500


def _json_value(value: Any) -> str:
    """Serialise one observation's value for a `*_json` column.

    `'null'` rather than SQL NULL for an absent value, matching the column
    defaults in migration 0003: NOT_STATED and NOT_APPLICABLE both carry no
    value, and a SQL NULL here would break the eventual JSONB conversion.
    """
    if isinstance(value, BaseModel):
        return json.dumps(value.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


class FingerprintRepo:
    """Everything one extraction leaves behind, written in one transaction.

    The caller wraps this in `transaction(conn)`. A half-written fingerprint --
    a document row with no evidence, or observations citing evidence that was
    never inserted -- would satisfy no foreign key and violate no constraint,
    and would be discovered months later as a fingerprint that cannot explain
    itself.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def existing(self, fp: JobFingerprint) -> str | None:
        """The id of an identical extraction, if one is already stored.

        Identity is the UNIQUE constraint from migration 0003: same source,
        same interpretation, same model. Asking before writing is what makes
        re-running a batch free rather than a duplicate-key error, and it is
        the database-level half of the cache the family keys implement.
        """
        row = self.conn.execute(
            "SELECT id FROM fingerprint WHERE content_hash = ? AND payload_hash IS ?"
            " AND schema_version = ? AND prompt_version = ? AND model = ?",
            (
                fp.meta.content_hash,
                fp.meta.payload_hash,
                fp.meta.schema_version,
                fp.meta.prompt_version,
                fp.meta.model,
            ),
        ).fetchone()
        return None if row is None else str(row["id"])

    def store(self, job_id: str, fp: JobFingerprint, verification: VerificationReport) -> str:
        """Write the document and every row derived from it. Returns its id."""
        already = self.existing(fp)
        if already is not None:
            return already

        fingerprint_id = new_id()
        self._insert_document(fingerprint_id, job_id, fp)
        self._insert_evidence(fingerprint_id, fp, verification)
        self._insert_observations(fingerprint_id, fp)
        return fingerprint_id

    # -- the document ------------------------------------------------------

    def _insert_document(self, fingerprint_id: str, job_id: str, fp: JobFingerprint) -> None:
        meta = fp.meta
        self.conn.execute(
            "INSERT INTO fingerprint (id, job_id, content_hash, payload_hash, schema_version,"
            " transport_version, prompt_version, responsibility_vocabulary_version,"
            " metadata_vocabulary_version, model, data_json, partial, truncated, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fingerprint_id,
                job_id,
                meta.content_hash,
                meta.payload_hash,
                meta.schema_version,
                # The document records the transport shape it was assembled
                # from. Per-family prompt and schema versions live on the
                # llm_call rows, where a retried family can legitimately differ
                # from its sibling.
                meta.transport_version,
                meta.prompt_version,
                meta.responsibility_vocabulary_version,
                meta.metadata_vocabulary_version,
                meta.model,
                fp.model_dump_json(),
                int(meta.partial),
                int(meta.truncated),
                now_utc(),
            ),
        )

    # -- citations ---------------------------------------------------------

    def _insert_evidence(
        self, fingerprint_id: str, fp: JobFingerprint, verification: VerificationReport
    ) -> None:
        results = {r.evidence_id: r for r in verification.results}
        for item in fp.evidence:
            result = results.get(item.id)
            is_description = item.source_kind is EvidenceSourceKind.JOB_DESCRIPTION
            self.conn.execute(
                "INSERT INTO evidence (id, fingerprint_id, source_kind, content_hash, quote,"
                " char_start, char_end, provider, payload_hash, source_field, source_value,"
                " verified, match_kind, match_score, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"{fingerprint_id}:{item.id}",
                    fingerprint_id,
                    item.source_kind.value,
                    fp.meta.content_hash if is_description else None,
                    item.quote,
                    result.char_start if result else None,
                    result.char_end if result else None,
                    item.provider,
                    fp.meta.payload_hash if not is_description else None,
                    item.source_field,
                    item.source_value,
                    int(bool(result and result.verified)),
                    result.match_kind.value if result else None,
                    result.match_score if result else None,
                    now_utc(),
                ),
            )

    def _evidence_row_id(self, fingerprint_id: str, evidence_id: str | None) -> str | None:
        """Citations are namespaced per fingerprint when they become rows.

        The model numbers evidence from `ev_01` on every call, so `ev_d01`
        belongs to exactly one document. A globally unique row id keeps the
        foreign keys honest without asking two independent completions to agree
        on numbering, which they cannot.
        """
        return None if not evidence_id else f"{fingerprint_id}:{evidence_id}"

    # -- observations ------------------------------------------------------

    def _insert_observations(self, fingerprint_id: str, fp: JobFingerprint) -> None:
        for item in fp.responsibilities:
            self.conn.execute(
                "INSERT INTO fp_responsibility (id, fingerprint_id, category, prominence,"
                " status, confidence, raw_phrase, evidence_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(),
                    fingerprint_id,
                    item.category.value,
                    item.prominence.value,
                    item.status.value,
                    item.confidence,
                    item.raw_phrase,
                    self._evidence_row_id(fingerprint_id, item.evidence_id),
                ),
            )

        for tool in fp.software:
            self.conn.execute(
                "INSERT INTO fp_software (id, fingerprint_id, raw_mention, canonical_tool,"
                " centrality, alternative_group, status, confidence, evidence_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(),
                    fingerprint_id,
                    tool.raw_mention,
                    tool.canonical_suggestion,
                    tool.centrality.value,
                    tool.alternative_group,
                    tool.status.value,
                    tool.confidence,
                    self._evidence_row_id(fingerprint_id, tool.evidence_id),
                ),
            )

        for language in fp.languages:
            self.conn.execute(
                "INSERT INTO fp_language (id, fingerprint_id, language_code, requirement_level,"
                " status, confidence, evidence_id) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (fingerprint_id, language_code) DO NOTHING",
                (
                    new_id(),
                    fingerprint_id,
                    language.language_code,
                    language.requirement_level.value,
                    language.status.value,
                    language.confidence,
                    self._evidence_row_id(fingerprint_id, language.evidence_id),
                ),
            )

        for dimension, extracted in fp.scalar_fields().items():
            self.conn.execute(
                "INSERT INTO fp_eligibility (id, fingerprint_id, dimension, value_json, status,"
                " not_applicable_because, confidence, evidence_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(),
                    fingerprint_id,
                    dimension,
                    _json_value(extracted.value),
                    extracted.status.value,
                    extracted.not_applicable_because,
                    extracted.confidence,
                    self._evidence_row_id(fingerprint_id, extracted.evidence_id),
                ),
            )

        # The second provenance channel, in its own table so that the two
        # cannot blend even by an accidental join.
        for observation in fp.provider_observations:
            self.conn.execute(
                "INSERT INTO provider_observation (id, fingerprint_id, dimension, value_json,"
                " status, confidence, evidence_id) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (fingerprint_id, dimension) DO NOTHING",
                (
                    new_id(),
                    fingerprint_id,
                    observation.dimension.value,
                    _json_value(observation.value),
                    observation.status.value,
                    observation.confidence,
                    self._evidence_row_id(fingerprint_id, observation.evidence_id),
                ),
            )


class LLMCallRepo:
    """What was asked, what came back, and how it was obtained.

    Written for every attempt including the failures, and never overwritten.
    `UNIQUE (cache_key, attempt)` makes re-importing the same batch idempotent
    rather than duplicative -- which matters because the Cowork harness is
    explicitly meant to be re-run while a prompt is being worked on.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- reading the cache -------------------------------------------------
    #
    # The cache key already carries the model identity, the prompt version, the
    # transport version and the complete model-visible input, so matching on it
    # is by construction a match on "the same question". `model` and `provider`
    # are matched anyway: they are redundant with the key, and redundancy is
    # exactly what makes an accidental cross-arm hit impossible to introduce by
    # editing the key derivation alone.

    def validated_answer(self, cache_key: str, *, model: str, vendor: str) -> CachedAnswer | None:
        """The stored, ACCEPTED answer for this key under this arm, if any.

        `validated_ok = 1` is necessary and was never sufficient. It says the
        document matched its schema; it says nothing about whether the evidence
        resolves or whether an EXPLICIT claim cites anything, and a
        schema-perfect answer that cited nothing was served from here once
        already. `cache_disposition = 'ACCEPTED'` is the question this method
        actually means to ask, and an unjudged row answers it with UNVERIFIED.
        """
        row = self.conn.execute(
            "SELECT cache_key, raw_output, model, input_tokens, output_tokens FROM llm_call"
            " WHERE cache_key = ? AND model = ? AND provider = ? AND validated_ok = 1"
            " AND cache_disposition = 'ACCEPTED'"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            (cache_key, model, vendor),
        ).fetchone()
        return None if row is None else self._as_cached(row)

    def validated_answers(
        self, cache_keys: Sequence[str], *, model: str, vendor: str
    ) -> dict[str, CachedAnswer]:
        """The same lookup for many keys at once, for planning a run.

        A preflight that asked one key at a time would be correct and would also
        make "how many live calls will this cost?" an O(n) round trip for a
        question the caller asks before every run.
        """
        found: dict[str, CachedAnswer] = {}
        unique = list(dict.fromkeys(cache_keys))
        for start in range(0, len(unique), _LOOKUP_CHUNK):
            chunk = unique[start : start + _LOOKUP_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                "SELECT cache_key, raw_output, model, input_tokens, output_tokens FROM llm_call"
                f" WHERE cache_key IN ({placeholders}) AND model = ? AND provider = ?"
                " AND validated_ok = 1 AND cache_disposition = 'ACCEPTED'"
                " ORDER BY created_at ASC, id ASC",
                (*chunk, model, vendor),
            ).fetchall()
            for row in rows:
                found[str(row["cache_key"])] = self._as_cached(row)
        return found

    @staticmethod
    def _as_cached(row: Any) -> CachedAnswer:
        return CachedAnswer(
            cache_key=str(row["cache_key"]),
            raw_output=str(row["raw_output"]),
            model=str(row["model"]),
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
        )

    def production_calls(self) -> list[tuple[str, str, int | None, int | None, str | None]]:
        """Every call that reached a vendor, as `pricing.audit_spend` reads them.

        `PRODUCTION_API` only. Every other runner names a way of obtaining an
        answer that never reached a vendor and therefore never cost anything,
        so including one would pad the denominator of an accounting question
        with events that are not billable by construction.
        """
        return [
            (
                str(row["provider"]),
                str(row["model"]),
                row["input_tokens"],
                row["output_tokens"],
                row["error"],
            )
            for row in self.conn.execute(
                "SELECT provider, model, input_tokens, output_tokens, error FROM llm_call "
                "WHERE runner = 'PRODUCTION_API'"
            )
        ]

    def record(self, call: LLMCallRecord, fingerprint_id: str | None = None) -> None:
        self.conn.execute(
            # No ON CONFLICT clause, deliberately. This was
            # `ON CONFLICT (cache_key, attempt) DO NOTHING`, and it silently
            # discarded 18 real HTTP attempts -- a whole benchmark run's
            # evidence, and the $0.0954 of billed inference that produced it --
            # because an earlier run had failed under the same key. A request
            # that reached a vendor must leave a row. If the event identity is
            # somehow already taken that is a bug, and it must be loud.
            "INSERT INTO llm_call (id, fingerprint_id, job_id, cache_key, execution_id,"
            " family, purpose, provider, model, reasoning_config, prompt_version,"
            " schema_version, input_hash, structured_output, raw_output, response_envelope,"
            " parsed_ok, validated_ok, cache_disposition, error, attempt, input_tokens,"
            " output_tokens, cost_usd, runner, transport, created_at)"
            " VALUES (" + ", ".join("?" * 27) + ")",
            (
                new_id(),
                fingerprint_id,
                call.job_id,
                call.cache_key,
                call.execution_id,
                call.family,
                call.purpose,
                call.provider,
                call.model,
                call.reasoning_config,
                call.prompt_version,
                call.schema_version,
                # The cache key *is* the input digest: it is derived from the
                # complete model-visible input plus every version that changes
                # what that input means. Storing a second hash of the same
                # thing would be two answers to one question.
                call.cache_key,
                call.structured_output,
                call.raw_output,
                call.response_envelope,
                int(call.parsed_ok),
                int(call.validated_ok),
                call.cache_disposition,
                call.error,
                call.attempt,
                call.input_tokens,
                call.output_tokens,
                call.cost_usd,
                call.runner,
                call.transport,
                now_utc(),
            ),
        )
