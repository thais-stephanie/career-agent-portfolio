-- Personal MVP -- the deterministic score, and where the human is with a job.
--
-- WHY THESE TABLES ARE NOT PART OF `fingerprint`
-- ----------------------------------------------
-- `fingerprint` answers "what does this posting say?". It is candidate-
-- independent by construction (ADR-0005): the same posting must produce the
-- same document for every candidate, forever. Nothing about a person may enter
-- it, and nothing in it may change when a preference changes.
--
-- `job_match` answers a different question -- "is this the work THIS candidate
-- wants, and could they take it?" -- and its answer is only true relative to
-- one configuration version. Raising a salary floor or reweighting a signal
-- invalidates every row here and not one row of `fingerprint`. Putting the two
-- in one table would mean either re-extracting on every preference edit, or
-- keeping a stale score beside a fresh document with no way to tell which is
-- which. `(config_id, config_version)` is what keeps them honest: a score
-- computed under older semantics is visibly stale rather than silently
-- reinterpreted.
--
-- `job_application` answers a third question -- "where is the PERSON with this
-- job?" -- and it is the only table in this file a human writes to directly.
-- It survives re-scoring, re-extraction and even the posting being closed by
-- the board, which is exactly why it cannot be a column on either of the
-- other two.
--
-- WHY BOTH A JSON BLOB AND SCALAR COLUMNS ON `job_match`
-- ------------------------------------------------------
-- `result_json` is the whole `MatchResult`: every component, contribution,
-- penalty, gate, confidence item and signal with its quotes, so the interface
-- can always answer "why 68?" without re-running the matcher. The scalar
-- columns beside it are a deliberate, bounded denormalisation: the list view
-- filters and sorts 18,550 rows, and doing that by deserialising 18,550 JSON
-- blobs in Python is the kind of decision that is cheap now and immovable
-- later. Every scalar is written from the same `MatchResult` in the same
-- statement, so the two cannot drift apart.
--
-- Portability, per Appendix A: TEXT ULID keys generated in Python (A1), no
-- SQLite date function anywhere (A3), enums as portable CHECK (col IN (...))
-- (A11), every JSON column NOT NULL with a default (A13), every foreign key
-- declared (A15). Additive only: no existing table is altered or rewritten.

-- ---------------------------------------------------------------------
-- THE SCORE
-- ---------------------------------------------------------------------

-- One row per (posting, configuration version). Re-scoring under the SAME
-- version updates the row in place -- the arithmetic is deterministic, so a
-- second run of the same configuration over the same text can only reproduce
-- it. A new version writes a new row and leaves the old verdict readable.
--
-- `content_hash` is stored rather than joined through `job` because a posting
-- can be re-normalised or re-fetched: the score belongs to the TEXT it was
-- computed from, not to whatever text the job points at today.
--
-- `title_class` and `seniority` carry no CHECK on purpose. Both vocabularies
-- belong to the matcher and its configuration, not to the schema, and a CHECK
-- here would turn adding a title class into a migration.
CREATE TABLE job_match (
    id                  TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL REFERENCES job(id),
    content_hash        TEXT NOT NULL REFERENCES job_raw(content_hash),

    -- Which configuration produced this, and which exact bytes of it.
    -- The version is what the UNIQUE key and every query filter on; the digest
    -- is what proves the file was not edited in place under a fixed version.
    config_id           TEXT NOT NULL,
    config_version      INTEGER NOT NULL,
    config_digest       TEXT NOT NULL,
    -- MATCH_SCHEMA_VERSION: the meaning of a stored MatchResult.
    schema_version      INTEGER NOT NULL,

    -- The three measurements, kept apart (ADR-0004). Nothing multiplies or
    -- averages them, here or anywhere above.
    match_score         INTEGER NOT NULL,
    data_confidence     INTEGER NOT NULL,
    fit_band            TEXT NOT NULL CHECK (fit_band IN (
                            'STRONG', 'GOOD', 'MODERATE', 'WEAK')),
    analysis_confidence TEXT NOT NULL CHECK (analysis_confidence IN (
                            'HIGH', 'MEDIUM', 'LOW')),
    screening_state     TEXT NOT NULL CHECK (screening_state IN (
                            'NOT_BLOCKED', 'BLOCKED')),
    -- UNRESOLVED is the common healthy answer, never an eliminated job.
    -- Absence is never permission.
    eligibility_status  TEXT NOT NULL CHECK (eligibility_status IN (
                            'VERIFIED_ELIGIBLE', 'LIKELY_ELIGIBLE',
                            'UNRESOLVED', 'VERIFIED_NOT_ELIGIBLE')),

    title_class         TEXT NOT NULL,
    seniority           TEXT NOT NULL,

    -- The whole serialised MatchResult. Written with sorted keys and no
    -- whitespace, so the same result always produces the same bytes.
    result_json         TEXT NOT NULL DEFAULT '{}',
    -- Supplied by the caller, never by a SQL clock. Hazard A3.
    computed_at         TEXT NOT NULL,
    UNIQUE (job_id, config_id, config_version)
);
-- The list view sorts by score and filters by eligibility; the re-score and
-- the count both scope by configuration version.
CREATE INDEX idx_job_match_score   ON job_match(match_score);
CREATE INDEX idx_job_match_elig    ON job_match(eligibility_status);
CREATE INDEX idx_job_match_config  ON job_match(config_id, config_version);

-- ---------------------------------------------------------------------
-- THE HUMAN'S WORKFLOW STATE
-- ---------------------------------------------------------------------

-- One row per job, created lazily on the first edit. A job with no row here
-- has not been touched by anybody, which reads as DISCOVERED at query time --
-- writing 18,550 untouched rows to say "nothing has happened yet" would be
-- storage pretending to be information.
--
-- THERE IS DELIBERATELY NO `applied` BOOLEAN COLUMN. `has_applied` is derived
-- every time from (status, applied_at) in domain/application.py, which is what
-- makes the two facts incapable of contradicting each other. A stored boolean
-- would be a third fact that can disagree with both, and the disagreement
-- would be invisible until someone asked how many jobs had been applied to.
CREATE TABLE job_application (
    id         TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL UNIQUE REFERENCES job(id),
    -- ApplicationStatus. Deliberately a different vocabulary from
    -- job.collection_status: the board can CLOSE a posting while the candidate
    -- is still at INTERVIEW, and one field could not hold both facts.
    status     TEXT NOT NULL CHECK (status IN (
                   'DISCOVERED', 'SHORTLISTED', 'TO_APPLY', 'APPLIED',
                   'INTERVIEW', 'OFFER', 'HIRED', 'REJECTED',
                   'WITHDRAWN', 'ARCHIVED')),
    -- NULL until an application was actually sent. Never invented by the
    -- schema; normalise_application_state decides it and passes it in.
    applied_at TEXT,
    -- A bookmark. Orthogonal to status: a job can be saved at any stage.
    saved      INTEGER NOT NULL DEFAULT 0 CHECK (saved IN (0, 1)),
    notes      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Append-only status history. Never updated, never deleted.
--
-- Written in the same transaction as the `job_application` row it describes,
-- so the current state and the story of how it got there cannot disagree.
-- `from_status` is NULL on the first write, which is how "this job entered the
-- workflow" is distinguished from "this job moved".
CREATE TABLE job_application_event (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES job(id),
    from_status TEXT CHECK (from_status IS NULL OR from_status IN (
                    'DISCOVERED', 'SHORTLISTED', 'TO_APPLY', 'APPLIED',
                    'INTERVIEW', 'OFFER', 'HIRED', 'REJECTED',
                    'WITHDRAWN', 'ARCHIVED')),
    to_status   TEXT NOT NULL CHECK (to_status IN (
                    'DISCOVERED', 'SHORTLISTED', 'TO_APPLY', 'APPLIED',
                    'INTERVIEW', 'OFFER', 'HIRED', 'REJECTED',
                    'WITHDRAWN', 'ARCHIVED')),
    -- The date as it stood AFTER this transition, so the history explains a
    -- cleared date as well as a set one.
    applied_at  TEXT,
    note        TEXT,
    occurred_at TEXT NOT NULL
);
-- occurred_at is ISO-8601 UTC text, so a plain string sort is correct date
-- ordering and no SQL date function is needed.
CREATE INDEX idx_job_app_event_job ON job_application_event(job_id, occurred_at);
