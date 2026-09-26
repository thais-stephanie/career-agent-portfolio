"""All SQL for the M0 and M1A tables, one class per aggregate.

A *repository* is the only place that knows how a thing is stored. Everything
above it asks for objects and gets objects back; nothing above it writes SQL.
That is what makes the eventual PostgreSQL migration a change of driver rather
than a rewrite, and it is why these classes are boring on purpose.

Two conventions used throughout:

* **Identifiers are generated here, in Python** (``new_id()``), never by the
  database. Hazard A1.
* **Upserts use ``ON CONFLICT ... DO UPDATE``**, which is identical in SQLite
  and PostgreSQL. Never ``INSERT OR REPLACE``, which is SQLite-only and which
  deletes-then-inserts, silently firing foreign-key cascades. Hazard A5.
"""

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from typing import Any

from career_agent.clock import new_id, now_utc
from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import CollectionStatus, PipelineRunStatus
from career_agent.storage.records import (
    CompanyRecord,
    JobRecord,
    ProviderPayloadRecord,
    SourceBoardRecord,
)


def sha256_text(text: str) -> str:
    """Content hash used for job text and profile snapshots."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Repo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn


# =====================================================================
# CANDIDATE, PROFILE VERSIONS, CLAIMS
# =====================================================================


class CandidateRepo(_Repo):
    def upsert(self, candidate_key: str, display_name: str) -> str:
        """Create the candidate if absent; return the id either way."""
        existing = self.conn.execute(
            "SELECT id FROM candidate WHERE candidate_key = ?", (candidate_key,)
        ).fetchone()
        if existing is not None:
            self.conn.execute(
                "UPDATE candidate SET display_name = ? WHERE id = ?",
                (display_name, existing["id"]),
            )
            return str(existing["id"])

        candidate_id = new_id()
        self.conn.execute(
            "INSERT INTO candidate (id, candidate_key, display_name, created_at)"
            " VALUES (?, ?, ?, ?)",
            (candidate_id, candidate_key, display_name, now_utc()),
        )
        return candidate_id

    def get(self, candidate_key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM candidate WHERE candidate_key = ?", (candidate_key,)
        ).fetchone()


class SearchProfileVersionRepo(_Repo):
    """Content-addressed snapshots of profile.local.yaml.

    Recording the same YAML twice returns the existing version rather than
    creating a duplicate, so running the pipeline repeatedly does not litter the
    table. A genuine edit produces a new hash and therefore a new version.
    """

    def record(self, candidate_id: str, yaml_text: str) -> str:
        content_hash = sha256_text(yaml_text)
        existing = self.conn.execute(
            "SELECT id FROM search_profile_version WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if existing is not None:
            return str(existing["id"])

        version_id = new_id()
        self.conn.execute(
            "INSERT INTO search_profile_version"
            " (id, candidate_id, content_hash, yaml_snapshot, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (version_id, candidate_id, content_hash, yaml_text, now_utc()),
        )
        return version_id

    def get(self, version_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM search_profile_version WHERE id = ?", (version_id,)
        ).fetchone()

    def latest(self, candidate_id: str) -> sqlite3.Row | None:
        # created_at is ISO-8601 UTC text, so a plain string sort is correct
        # date ordering. Hazard A3: no SQL date function is needed.
        return self.conn.execute(
            "SELECT * FROM search_profile_version WHERE candidate_id = ?"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()


class ClaimRepo(_Repo):
    """Career facts, with a revision chain instead of destructive edits."""

    def add(self, candidate_id: str, claim: VerifiedClaim) -> str:
        claim_id = new_id()
        self.conn.execute(
            "INSERT INTO verified_claim"
            " (id, candidate_id, claim_key, revision, claim_type, text, employer,"
            "  period_start, period_end, source, verified, evidence_ref,"
            "  tools_json, tags_json, valid_from, superseded_by_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
            (
                claim_id,
                candidate_id,
                claim.claim_key,
                claim.revision,
                claim.claim_type.value,
                claim.text,
                claim.employer,
                claim.period_start,
                claim.period_end,
                claim.source.value,
                int(claim.verified),
                claim.evidence_ref,
                json.dumps(claim.tools),
                json.dumps([t.value for t in claim.tags]),
                now_utc(),
                now_utc(),
            ),
        )
        return claim_id

    def supersede(self, candidate_id: str, claim: VerifiedClaim) -> str:
        """Record a corrected claim and retire the revision it replaces.

        The old row is never deleted or edited in place: it stays as history,
        pointing forward at the revision that replaced it.
        """
        previous = self.current_row(candidate_id, claim.claim_key)
        new_claim_id = self.add(candidate_id, claim)
        if previous is not None:
            self.conn.execute(
                "UPDATE verified_claim SET superseded_by_id = ? WHERE id = ?",
                (new_claim_id, previous["id"]),
            )
        return new_claim_id

    def current_row(self, candidate_id: str, claim_key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM verified_claim"
            " WHERE candidate_id = ? AND claim_key = ? AND superseded_by_id IS NULL",
            (candidate_id, claim_key),
        ).fetchone()

    def current(self, candidate_id: str) -> list[VerifiedClaim]:
        """Every claim that has not been superseded, as domain objects."""
        rows = self.conn.execute(
            "SELECT * FROM verified_claim"
            " WHERE candidate_id = ? AND superseded_by_id IS NULL"
            " ORDER BY claim_key",
            (candidate_id,),
        ).fetchall()
        return [self._to_claim(row) for row in rows]

    def states(self, candidate_id: str) -> dict[str, str]:
        """What each current claim IS, read from its whole revision history.

        `verified = 0` on the current revision means two different things,
        and the history tells them apart without a new column:

        * CONFIRMED  the current revision is verified.
        * RETIRED    it is not, and an earlier revision WAS: she stood behind
                     it once and withdrew it (retiring is the only path that
                     un-verifies a confirmed claim, and an edit carries the
                     retirement forward).
        * DRAFT      no revision was ever verified: a statement recorded but
                     never confirmed, such as a `verified: false` entry in a
                     career-facts file. Live, and waiting for a review.
        """
        rows = self.conn.execute(
            "SELECT c.claim_key, c.verified,"
            "       EXISTS (SELECT 1 FROM verified_claim h"
            "                WHERE h.candidate_id = c.candidate_id"
            "                  AND h.claim_key = c.claim_key AND h.verified = 1) AS ever"
            "  FROM verified_claim c"
            " WHERE c.candidate_id = ? AND c.superseded_by_id IS NULL",
            (candidate_id,),
        ).fetchall()
        return {
            str(row["claim_key"]): "CONFIRMED"
            if row["verified"]
            else ("RETIRED" if row["ever"] else "DRAFT")
            for row in rows
        }

    def history(self, candidate_id: str, claim_key: str) -> list[VerifiedClaim]:
        rows = self.conn.execute(
            "SELECT * FROM verified_claim WHERE candidate_id = ? AND claim_key = ?"
            " ORDER BY revision",
            (candidate_id, claim_key),
        ).fetchall()
        return [self._to_claim(row) for row in rows]

    @staticmethod
    def _to_claim(row: sqlite3.Row) -> VerifiedClaim:
        return VerifiedClaim(
            claim_key=row["claim_key"],
            revision=row["revision"],
            claim_type=row["claim_type"],
            text=row["text"],
            employer=row["employer"],
            period_start=row["period_start"],
            period_end=row["period_end"],
            source=row["source"],
            verified=bool(row["verified"]),
            evidence_ref=row["evidence_ref"],
            tools=json.loads(row["tools_json"]),
            tags=json.loads(row["tags_json"]),
        )


# =====================================================================
# COMPANY REGISTRY AND COLLECTION
# =====================================================================


class CompanyRepo(_Repo):
    #: The columns an upsert refreshes. Kept in one place because the insert and
    #: the update have to agree, and they silently would not if edited apart.
    _MUTABLE = (
        "name",
        "website",
        "hq_country",
        "size_estimate",
        "stage",
        "industry",
        "notes",
        "canonical_domain",
        "discovery_source",
        "priority_reason",
    )

    def _values(self, company: CompanyRecord) -> tuple[Any, ...]:
        return tuple(getattr(company, column) for column in self._MUTABLE)

    def upsert(self, company: CompanyRecord) -> str:
        """Insert or update, resolving identity by domain first, then by slug.

        The domain is the anchor (M1D). Two registry entries that disagree about
        the slug but agree on a verified domain are one company, and merging
        them here is what stops a company being collected twice and every
        per-company count being quietly wrong.

        Name similarity is deliberately NOT identity evidence: `Acme`,
        `Acme Health` and `Acme Labs` stay distinct unless their domains match
        exactly. A false merge destroys the evidence needed to undo it, which is
        worse than a duplicate that can still be spotted.
        """
        existing = None
        if company.canonical_domain:
            existing = self.conn.execute(
                "SELECT id FROM company WHERE canonical_domain = ?", (company.canonical_domain,)
            ).fetchone()
        if existing is None:
            existing = self.conn.execute(
                "SELECT id FROM company WHERE slug = ?", (company.slug,)
            ).fetchone()

        now = now_utc()
        if existing is not None:
            assignments = ", ".join(f"{column} = ?" for column in self._MUTABLE)
            self.conn.execute(
                f"UPDATE company SET {assignments}, updated_at = ? WHERE id = ?",
                (*self._values(company), now, existing["id"]),
            )
            return str(existing["id"])

        company_id = new_id()
        columns = ", ".join(self._MUTABLE)
        placeholders = ", ".join("?" for _ in self._MUTABLE)
        self.conn.execute(
            f"INSERT INTO company (id, slug, {columns}, created_at, updated_at)"
            f" VALUES (?, ?, {placeholders}, ?, ?)",
            (company_id, company.slug, *self._values(company), now, now),
        )
        return company_id

    def get_by_slug(self, slug: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM company WHERE slug = ?", (slug,)).fetchone()

    def get_by_domain(self, canonical_domain: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM company WHERE canonical_domain = ?", (canonical_domain,)
        ).fetchone()

    def all(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM company ORDER BY slug").fetchall()


class SourceBoardRepo(_Repo):
    def upsert(self, board: SourceBoardRecord) -> str:
        board_id = new_id()
        self.conn.execute(
            "INSERT INTO source_board"
            " (id, company_id, provider, board_identifier, board_url, active,"
            "  discovery_method, verified_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (provider, board_identifier) DO UPDATE SET"
            "   company_id       = excluded.company_id,"
            "   board_url        = excluded.board_url,"
            "   active           = excluded.active,"
            "   discovery_method = COALESCE(excluded.discovery_method, discovery_method),"
            "   verified_at      = COALESCE(excluded.verified_at, verified_at)",
            (
                board_id,
                board.company_id,
                board.provider,
                board.board_identifier,
                board.board_url,
                int(board.active),
                board.discovery_method,
                board.verified_at,
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM source_board WHERE provider = ? AND board_identifier = ?",
            (board.provider, board.board_identifier),
        ).fetchone()
        return str(row["id"])

    def get_by_identifier(self, provider: str, board_identifier: str) -> sqlite3.Row | None:
        """The board this provider knows by this identifier, if we have it.

        Its one caller wants to count boards it CREATED, which `upsert` cannot
        report: the statement is an upsert precisely so it does not care, and
        making it return "was this new" would put a second meaning on a method
        whose value is having one.
        """
        return self.conn.execute(
            "SELECT * FROM source_board WHERE provider = ? AND board_identifier = ?",
            (provider, board_identifier),
        ).fetchone()

    def mark_collected(self, board_id: str) -> None:
        """Record a SUCCESSFUL collection pass.

        Called only on success. If a network failure advanced this timestamp,
        the closure detector would conclude that every job on the board had
        vanished and mass-close a company's postings.
        """
        self.conn.execute(
            "UPDATE source_board SET last_collected_at = ?, last_error = NULL WHERE id = ?",
            (now_utc(), board_id),
        )

    def mark_error(self, board_id: str, message: str) -> None:
        """Record a failure WITHOUT advancing last_collected_at."""
        self.conn.execute(
            "UPDATE source_board SET last_error = ? WHERE id = ?", (message, board_id)
        )

    def hold_suspicious(self, board_id: str, observed: int) -> None:
        """Flag a board whose successful pass returned implausibly little.

        `suspicious_since` is set only once, so a board held for three passes
        still reports when the trouble started rather than resetting each time.
        `suspicious_observed` is refreshed, because confirmation compares the
        latest observation against the previous one.
        """
        self.conn.execute(
            "UPDATE source_board SET"
            "  suspicious_since = COALESCE(suspicious_since, ?),"
            "  suspicious_observed = ?"
            " WHERE id = ?",
            (now_utc(), observed, board_id),
        )

    def clear_suspicious(self, board_id: str) -> None:
        self.conn.execute(
            "UPDATE source_board SET suspicious_since = NULL, suspicious_observed = NULL"
            " WHERE id = ?",
            (board_id,),
        )

    def suspicious(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT sb.*, c.slug AS company_slug FROM source_board sb"
            " JOIN company c ON c.id = sb.company_id"
            " WHERE sb.suspicious_since IS NOT NULL"
            " ORDER BY sb.suspicious_since"
        ).fetchall()

    def for_company(self, company_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM source_board WHERE company_id = ? ORDER BY provider",
            (company_id,),
        ).fetchall()

    def get(self, board_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM source_board WHERE id = ?", (board_id,)).fetchone()


class JobRawRepo(_Repo):
    """Content-addressed job text.

    Keyed by the hash of the text, so two companies posting identical wording,
    or one company reposting, cost one row and later one extraction.
    """

    def put(self, description_text: str, description_html: str | None = None) -> str:
        content_hash = sha256_text(description_text)
        self.conn.execute(
            "INSERT INTO job_raw"
            " (content_hash, description_text, description_html, byte_length, created_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (content_hash) DO NOTHING",
            (
                content_hash,
                description_text,
                description_html,
                len(description_text.encode("utf-8")),
                now_utc(),
            ),
        )
        return content_hash

    def get(self, content_hash: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM job_raw WHERE content_hash = ?", (content_hash,)
        ).fetchone()


class JobRepo(_Repo):
    def upsert_seen(
        self, job: JobRecord, status: CollectionStatus = CollectionStatus.DISCOVERED
    ) -> str:
        """Record that a posting was seen on this pass.

        A new posting is inserted with first_seen_at; a returning one has
        last_seen_at refreshed and its mutable fields updated. first_seen_at is
        never overwritten, because the age of a posting is a ranking signal and
        for providers that expose no date it is the only one we have.

        **A POSTING THAT COMES BACK GETS ITS STATUS BACK**, and only then.

        `closed_at = NULL` alone left `collection_status` reading `CLOSED` on a
        row that is open, forever. Latent until a board lost and regained
        postings at scale: one production run on 2026-09-09 closed 931 and
        `career-agent integrity` then reported **37 rows where closed_at and
        collection_status disagree** -- the first BROKEN finding this corpus has
        ever carried.

        The status is restored only for a row that was actually closed.
        Overwriting it on every pass would be the opposite defect: a posting
        that reached `NORMALISED` would drop back to whatever a lighter pass
        happened to pass in, and progress would be erased by a routine refresh.
        """
        now = now_utc()
        job_id = new_id()
        self.conn.execute(
            "INSERT INTO job (id, company_id, source_board_id, provider, external_id, url,"
            " title, department, location_raw, posted_at, content_hash, first_seen_at,"
            " last_seen_at, closed_at, collection_status, prefilter_reason,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?)"
            " ON CONFLICT (provider, external_id) DO UPDATE SET"
            "   url               = excluded.url,"
            "   title             = excluded.title,"
            "   department        = excluded.department,"
            "   location_raw      = excluded.location_raw,"
            "   posted_at         = excluded.posted_at,"
            "   content_hash      = excluded.content_hash,"
            "   last_seen_at      = excluded.last_seen_at,"
            "   closed_at         = NULL,"
            # THE ROW WAS CLOSED, so this pass is a REOPENING and the status
            # this pass observed is the true one. A row that was never closed
            # keeps what it had: see the docstring for why overwriting it
            # unconditionally erases progress.
            "   collection_status = CASE WHEN job.closed_at IS NOT NULL"
            "                            THEN excluded.collection_status"
            "                            ELSE job.collection_status END,"
            "   updated_at        = excluded.updated_at",
            (
                job_id,
                job.company_id,
                job.source_board_id,
                job.provider,
                job.external_id,
                job.url,
                job.title,
                job.department,
                job.location_raw,
                job.posted_at,
                job.content_hash,
                now,
                now,
                status.value,
                now,
                now,
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM job WHERE provider = ? AND external_id = ?",
            (job.provider, job.external_id),
        ).fetchone()
        return str(row["id"])

    def repoint_content_hash(self, old_hash: str, new_hash: str) -> int:
        """Move every job pointing at one raw row onto another. Returns the count.

        For re-normalisation only: the posting did not change, our rendering of
        it did. Deliberately touches `content_hash` and `updated_at` and nothing
        else -- not `first_seen_at`, not `last_seen_at`, not `closed_at`, not
        `collection_status`. A normalisation backfill must never look like a new
        observation from the board, because a job that appears newly seen is a
        job whose freshness signal has been quietly falsified.

        The old `job_raw` row is left in place. History here is append-only.
        """
        cursor = self.conn.execute(
            "UPDATE job SET content_hash = ?, updated_at = ? WHERE content_hash = ?",
            (new_hash, now_utc(), old_hash),
        )
        return int(cursor.rowcount)

    def set_status(
        self, job_id: str, status: CollectionStatus, prefilter_reason: str | None = None
    ) -> None:
        self.conn.execute(
            "UPDATE job SET collection_status = ?, prefilter_reason = ?, updated_at = ?"
            " WHERE id = ?",
            (status.value, prefilter_reason, now_utc(), job_id),
        )

    def get(self, job_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone()

    def get_by_external(self, provider: str, external_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM job WHERE provider = ? AND external_id = ?",
            (provider, external_id),
        ).fetchone()

    def by_status(self, status: CollectionStatus) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM job WHERE collection_status = ? ORDER BY last_seen_at DESC",
            (status.value,),
        ).fetchall()

    def open_external_ids(self, source_board_id: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT external_id FROM job WHERE source_board_id = ? AND closed_at IS NULL",
            (source_board_id,),
        ).fetchall()
        return {str(row["external_id"]) for row in rows}

    def close_absent(self, source_board_id: str, seen_external_ids: set[str]) -> int:
        """Close postings the board stopped returning.

        No ATS reports that a job closed, so we detect it by difference: which
        postings did we hold open for this board that the latest pass did not
        return?

        The comparison is on identifiers, deliberately not on timestamps. An
        earlier version compared `last_seen_at` against the moment the pass
        began, which silently did nothing whenever both fell in the same second
        -- timestamps here are second-resolution. Set difference is exact and
        has no dependence on clock granularity at all.

        **The caller must only invoke this after a pass that SUCCEEDED.** A
        board that timed out returns an empty set, and closing everything on
        that basis would destroy a company's posting history.
        """
        absent = self.open_external_ids(source_board_id) - seen_external_ids
        if not absent:
            return 0

        closed_at = now_utc()
        # Chunked so a very large board cannot exceed the SQL variable limit.
        chunk_size = 500
        ordered = sorted(absent)
        for start in range(0, len(ordered), chunk_size):
            chunk = ordered[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            self.conn.execute(
                "UPDATE job SET closed_at = ?, collection_status = ?, updated_at = ?"
                f" WHERE source_board_id = ? AND closed_at IS NULL"
                f" AND external_id IN ({placeholders})",
                (closed_at, CollectionStatus.CLOSED.value, closed_at, source_board_id, *chunk),
            )
        return len(absent)


#: How many ids go into one `IN (...)`. Well under SQLite's variable ceiling
#: (999 on the oldest builds still in the wild), and a whole rescore page of
#: 500 fits in a single round trip.
_IN_CHUNK = 500


class DiscoverySourceRepo(_Repo):
    """Where ELSE a posting we already hold has been seen.

    Migration 0018 carries the full reasoning. The short version: `job` records
    one authoritative origin and does not move, so a second sighting of the same
    posting through an aggregator is recorded here rather than as a second job
    or as an overwrite of the first.

    Every method here is additive. Nothing in this class can modify a `job` row,
    and that is the property that makes "richer ATS data is never replaced by a
    poorer duplicate" true by construction rather than by care.
    """

    def record(
        self,
        job_id: str,
        source: str,
        external_id: str,
        matched_by: str,
        url: str | None = None,
        origin_url: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Record a sighting, or refresh the one already recorded.

        Keyed on `(source, external_id)`, so re-running a retrieval updates
        `last_seen_at` instead of accumulating a row per pass.

        `first_seen_at` is deliberately NOT touched on conflict. When this
        source first showed us a posting is a fact about the past, and a run
        that sees it again does not make it newer.
        """
        now = now_utc()
        payload_json = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO job_discovery_source"
            " (id, job_id, source, external_id, url, origin_url, matched_by,"
            "  first_seen_at, last_seen_at, payload_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (source, external_id) DO UPDATE SET"
            "   job_id = excluded.job_id,"
            "   url = excluded.url,"
            "   origin_url = excluded.origin_url,"
            "   matched_by = excluded.matched_by,"
            "   last_seen_at = excluded.last_seen_at,"
            "   payload_json = excluded.payload_json",
            (
                new_id(),
                job_id,
                source,
                external_id,
                url,
                origin_url,
                matched_by,
                now,
                now,
                payload_json,
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM job_discovery_source WHERE source = ? AND external_id = ?",
            (source, external_id),
        ).fetchone()
        return str(row["id"])

    def for_job(self, job_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM job_discovery_source WHERE job_id = ? ORDER BY source, external_id",
                (job_id,),
            ).fetchall()
        )

    def counts_by_match(self, source: str) -> dict[str, int]:
        """How many sightings each deterministic rule accounted for.

        Reported after a retrieval so the deduplication can be reviewed rather
        than trusted. A duplicate count with no rule beside it is a number
        nobody can check.
        """
        rows = self.conn.execute(
            "SELECT matched_by, COUNT(*) AS n FROM job_discovery_source"
            " WHERE source = ? GROUP BY matched_by",
            (source,),
        ).fetchall()
        return {str(row["matched_by"]): int(row["n"]) for row in rows}

    def sightings_for_jobs(
        self, job_ids: Sequence[str]
    ) -> dict[str, list[tuple[str, dict[str, Any]]]]:
        """Every sighting of each job, as `(source, payload)`, newest last seen
        first. One query per chunk, for the same reason `latest_for_jobs`
        exists: a corpus pass must not pay a round trip per job."""
        out: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        ids = list(job_ids)
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT job_id, source, payload_json FROM job_discovery_source"
                f" WHERE job_id IN ({marks}) ORDER BY last_seen_at DESC",
                chunk,
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except (TypeError, ValueError):
                    payload = {}
                out.setdefault(str(row["job_id"]), []).append((str(row["source"]), payload))
        return out


class ProviderPayloadRepo(_Repo):
    """The provider's structured metadata, archived verbatim.

    Stored so that PROVIDER_FIELD evidence can be verified at M2 by resolving a
    JSON path against this exact payload. The JSON path is resolved in Python,
    never in SQL: hazard A17.
    """

    def put(self, record: ProviderPayloadRecord) -> str:
        payload_json = json.dumps(record.payload, sort_keys=True, ensure_ascii=False)
        payload_hash = sha256_text(payload_json)
        payload_id = new_id()
        self.conn.execute(
            "INSERT INTO job_provider_payload"
            " (id, job_id, provider, payload_hash, payload_json, captured_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (job_id, payload_hash) DO NOTHING",
            (payload_id, record.job_id, record.provider, payload_hash, payload_json, now_utc()),
        )
        row = self.conn.execute(
            "SELECT id FROM job_provider_payload WHERE job_id = ? AND payload_hash = ?",
            (record.job_id, payload_hash),
        ).fetchone()
        return str(row["id"])

    def latest_for_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload_json FROM job_provider_payload WHERE job_id = ?"
            " ORDER BY captured_at DESC, id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        loaded: dict[str, Any] = json.loads(row["payload_json"])
        return loaded

    def latest_for_jobs(self, job_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """`latest_for_job` for a whole page of jobs, in one query per chunk.

        The reason this exists rather than a loop over `latest_for_job`: a
        corpus pass calls it 18,550 times, and a per-job round trip turns a
        6 ms/job matcher into something else entirely. Same ordering rule as
        the singular method -- newest `captured_at`, ties broken by `id` -- so
        the two can never disagree about which payload is current.

        Ordered ASCENDING and overwritten as it goes, so the last row written
        for a job is the one the DESC query would have returned first. JSON is
        parsed only for the survivors: a job with four archived payloads costs
        one decode, not four.

        Jobs with no payload are simply absent from the result. That is the
        caller's cue to treat them as "nothing archived", never as "no salary".
        """
        latest: dict[str, str] = {}
        for start in range(0, len(job_ids), _IN_CHUNK):
            chunk = job_ids[start : start + _IN_CHUNK]
            if not chunk:
                continue
            # The only interpolation is the placeholder count. Every id is
            # still bound, so nothing a caller supplies reaches the SQL text.
            marks = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                "SELECT job_id, payload_json FROM job_provider_payload"
                f" WHERE job_id IN ({marks})"
                " ORDER BY job_id, captured_at, id",
                tuple(chunk),
            ).fetchall()
            for row in rows:
                latest[str(row["job_id"])] = str(row["payload_json"])
        decoded: dict[str, dict[str, Any]] = {}
        for job_id, payload_json in latest.items():
            loaded = json.loads(payload_json)
            if isinstance(loaded, dict):
                decoded[job_id] = loaded
        return decoded


class PipelineRunRepo(_Repo):
    def start(self, stage: str) -> str:
        run_id = new_id()
        self.conn.execute(
            "INSERT INTO pipeline_run (id, stage, started_at, status, stats_json)"
            " VALUES (?, ?, ?, ?, '{}')",
            (run_id, stage, now_utc(), PipelineRunStatus.RUNNING.value),
        )
        # A run that writes the shared job catalogue holds its collection lock
        # until it finishes. Taken after the insert, inside the caller's
        # transaction: when another process holds it, the refusal rolls the
        # row back and nothing was started.
        from career_agent.storage.catalogue import hold_for_run

        hold_for_run(self.conn, run_id, stage)
        return run_id

    def finish(
        self,
        run_id: str,
        status: PipelineRunStatus,
        stats: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        finished = now_utc()
        self.conn.execute(
            "UPDATE pipeline_run SET finished_at = ?, status = ?, stats_json = ?, error = ?"
            " WHERE id = ?",
            (finished, status.value, json.dumps(stats or {}), error, run_id),
        )
        # How the collection went, for every profile (sources/public_health.py):
        # outcome and counts only, never this profile's queries. Never allowed
        # to break the run it describes.
        row = self.conn.execute(
            "SELECT stage, started_at FROM pipeline_run WHERE id = ?", (run_id,)
        ).fetchone()
        if row is not None:
            import sqlite3

            from career_agent.sources import public_health

            try:
                public_health.record(
                    self.conn,
                    stage=str(row[0]),
                    started_at=str(row[1]),
                    finished_at=finished,
                    status=status.value,
                    stats=stats or {},
                )
            except sqlite3.Error:
                import logging

                logging.getLogger(__name__).warning("source health not recorded", exc_info=True)
        from career_agent.storage.catalogue import release_for_run

        release_for_run(self.conn, run_id)

    def progress(self, run_id: str, stats: dict[str, Any]) -> None:
        """Record what a run has done SO FAR, without ending it.

        THE GAP THIS CLOSES. `start` writes `stats_json = '{}'` and `finish`
        replaces it, so for the whole life of a run -- which for Gupy is hours --
        the only honest thing anybody could say about it was that it had begun.
        A screen cannot show a candidate `54,200 of ~79,000` from a row that
        holds an empty object.

        Deliberately NOT a new table. Every counter a progress display needs is
        already in `stats_json` when the run ends; the only thing missing was
        writing it more than once. A collector calls this at the point it
        already commits a page, so a heartbeat costs one UPDATE per page and
        crash-safety comes free: whatever the last page recorded survives the
        process being killed.

        `finished_at` and `status` are untouched. A row with a `finished_at` of
        NULL and a non-empty `stats_json` is exactly "running, and here is how
        far it got", which is the state this product could not express.
        """
        # `heartbeat_at` says the run is ALIVE, not just that it began: a
        # process killed mid-run leaves a RUNNING row behind, and only the age
        # of its last heartbeat tells that row apart from one still working.
        self.conn.execute(
            "UPDATE pipeline_run SET stats_json = ? WHERE id = ? AND finished_at IS NULL",
            (json.dumps({**stats, "heartbeat_at": now_utc()}), run_id),
        )

    def get(self, run_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM pipeline_run WHERE id = ?", (run_id,)).fetchone()

    def recent(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM pipeline_run ORDER BY started_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
