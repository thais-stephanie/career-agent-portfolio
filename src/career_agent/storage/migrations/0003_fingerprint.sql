-- M2: Evidence-Grounded Job Fingerprint
--
-- Numbered 0003 because the repository is authoritative: the M0 architecture
-- called this migration 0002, and M1D took that number for company identity.
-- M3 and M5 shift to 0004 and 0005 accordingly.
--
-- Everything here is additive. No M0/M1 table is altered, so a database that
-- has collected 18,550 postings gains extraction storage without touching a
-- single row of what it already holds.
--
-- Postgres portability, per ADR-0003: TEXT for ids and timestamps, INTEGER for
-- booleans, no AUTOINCREMENT, no SQLite-only functions, and every foreign key
-- named explicitly.

-- ---------------------------------------------------------------------
-- THE DOCUMENT
-- ---------------------------------------------------------------------

-- One row per (source content, source payload, interpretation). The UNIQUE
-- constraint IS the cache: an identical input under identical versions can only
-- ever produce one row, so re-running extraction is free by construction rather
-- than by a lookup someone has to remember to write.
--
-- payload_hash participates because the same description under different
-- provider metadata is a different observation. Two companies posting identical
-- text with identical metadata still cost one extraction.
CREATE TABLE fingerprint (
    id                    TEXT PRIMARY KEY,
    job_id                TEXT NOT NULL REFERENCES job(id),
    content_hash          TEXT NOT NULL REFERENCES job_raw(content_hash),
    payload_hash          TEXT,
    schema_version        INTEGER NOT NULL,
    transport_version     INTEGER NOT NULL,
    prompt_version        TEXT NOT NULL,
    responsibility_vocabulary_version INTEGER NOT NULL,
    metadata_vocabulary_version       INTEGER NOT NULL,
    model                 TEXT NOT NULL,
    data_json             TEXT NOT NULL DEFAULT '{}',
    partial               INTEGER NOT NULL DEFAULT 0 CHECK (partial IN (0, 1)),
    truncated             INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0, 1)),
    input_tokens          INTEGER,
    output_tokens         INTEGER,
    cost_usd              REAL,
    created_at            TEXT NOT NULL,
    UNIQUE (content_hash, payload_hash, schema_version, prompt_version, model)
);
CREATE INDEX idx_fingerprint_job   ON fingerprint(job_id);
CREATE INDEX idx_fingerprint_model ON fingerprint(model);


-- ---------------------------------------------------------------------
-- EVIDENCE
-- ---------------------------------------------------------------------

-- Two source kinds share one table because they answer the same question --
-- "where does this claim come from?" -- and every downstream consumer wants
-- them together. The columns for each kind are mutually exclusive, enforced by
-- the domain model rather than by a CHECK, because the rule is about which
-- combination is meaningful and that is easier to read in Python.
--
-- `verified` and `match_kind` are written by domain/verify.py after the model
-- has spoken. They default to unverified: a citation is guilty until checked.
CREATE TABLE evidence (
    id              TEXT PRIMARY KEY,
    fingerprint_id  TEXT NOT NULL REFERENCES fingerprint(id) ON DELETE CASCADE,
    source_kind     TEXT NOT NULL CHECK (source_kind IN (
                        'JOB_DESCRIPTION', 'PROVIDER_FIELD', 'CAREER_PAGE',
                        'COMPANY_HISTORY', 'CANDIDATE_CONFIRMED')),

    -- JOB_DESCRIPTION
    content_hash    TEXT REFERENCES job_raw(content_hash),
    quote           TEXT,
    char_start      INTEGER,
    char_end        INTEGER,

    -- PROVIDER_FIELD
    provider        TEXT,
    payload_hash    TEXT,
    source_field    TEXT,
    source_value    TEXT,

    verified        INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    -- EXACT, NORMALISED and FIELD_MATCH set verified = 1; NOT_FOUND does not.
    -- There is no similarity tier: one differing token can be 'not', a country
    -- or an amount, and those are the tokens the later gates depend on.
    match_kind      TEXT CHECK (match_kind IN (
                        'EXACT', 'NORMALISED', 'FIELD_MATCH', 'NOT_FOUND')),
    match_score     REAL,
    created_at      TEXT NOT NULL,
    UNIQUE (fingerprint_id, id)
);
CREATE INDEX idx_evidence_fp       ON evidence(fingerprint_id);
CREATE INDEX idx_evidence_kind     ON evidence(source_kind);
CREATE INDEX idx_evidence_verified ON evidence(verified);


-- ---------------------------------------------------------------------
-- OBSERVATIONS
--
-- The document is also stored whole in fingerprint.data_json. These tables
-- exist because M3, M4 and M5 need to ask questions across jobs -- "which
-- postings have Salesforce at CORE?", "how many state a hard language
-- requirement?" -- and answering those by parsing 18,550 JSON blobs is the
-- kind of decision that is cheap now and immovable later.
-- ---------------------------------------------------------------------

CREATE TABLE fp_responsibility (
    id              TEXT PRIMARY KEY,
    fingerprint_id  TEXT NOT NULL REFERENCES fingerprint(id) ON DELETE CASCADE,
    category        TEXT NOT NULL,
    prominence      TEXT NOT NULL CHECK (prominence IN ('PRIMARY', 'SECONDARY', 'INCIDENTAL')),
    status          TEXT NOT NULL,
    confidence      REAL,
    -- Kept whenever the category is the escape hatch. What accumulates here is
    -- how the vocabulary grows from evidence rather than from guesswork.
    raw_phrase      TEXT,
    evidence_id     TEXT REFERENCES evidence(id)
);
CREATE INDEX idx_fp_resp_fp       ON fp_responsibility(fingerprint_id);
CREATE INDEX idx_fp_resp_category ON fp_responsibility(category);

CREATE TABLE fp_software (
    id                TEXT PRIMARY KEY,
    fingerprint_id    TEXT NOT NULL REFERENCES fingerprint(id) ON DELETE CASCADE,
    raw_mention       TEXT NOT NULL,
    -- NULL means unknown and queued for review. M2 suggests; it never creates
    -- a permanent alias.
    canonical_tool    TEXT,
    centrality        TEXT NOT NULL CHECK (centrality IN (
                          'CORE', 'REQUIRED', 'PREFERRED', 'MENTIONED', 'ALTERNATIVE')),
    -- Ties together tools offered as substitutes for each other, so a refused
    -- tool listed as one option among several is not later read as a conflict.
    alternative_group TEXT,
    status            TEXT NOT NULL,
    confidence        REAL,
    evidence_id       TEXT REFERENCES evidence(id)
);
CREATE INDEX idx_fp_software_fp   ON fp_software(fingerprint_id);
CREATE INDEX idx_fp_software_tool ON fp_software(canonical_tool);


CREATE TABLE fp_language (
    id                TEXT PRIMARY KEY,
    fingerprint_id    TEXT NOT NULL REFERENCES fingerprint(id) ON DELETE CASCADE,
    language_code     TEXT NOT NULL,
    requirement_level TEXT NOT NULL CHECK (requirement_level IN (
                          'HARD_REQUIREMENT', 'STRONG_PREFERENCE', 'NICE_TO_HAVE', 'UNCLEAR')),
    status            TEXT NOT NULL,
    confidence        REAL,
    evidence_id       TEXT REFERENCES evidence(id),
    UNIQUE (fingerprint_id, language_code)
);
CREATE INDEX idx_fp_language_fp ON fp_language(fingerprint_id);

-- Observations derived from the JOB DESCRIPTION only.
CREATE TABLE fp_eligibility (
    id                     TEXT PRIMARY KEY,
    fingerprint_id         TEXT NOT NULL REFERENCES fingerprint(id) ON DELETE CASCADE,
    dimension              TEXT NOT NULL,
    -- 'null' rather than NULL: NOT_STATED and NOT_APPLICABLE both carry no
    -- value, and a SQL NULL here would break the later JSONB conversion.
    value_json             TEXT NOT NULL DEFAULT 'null',
    status                 TEXT NOT NULL CHECK (status IN (
                               'EXPLICIT', 'INFERRED', 'NOT_STATED', 'NOT_APPLICABLE')),
    -- Required when status is NOT_APPLICABLE: it names the dimension that
    -- proves inapplicability. Silence is NOT_STATED and carries nothing here.
    not_applicable_because TEXT,
    confidence             REAL,
    evidence_id            TEXT REFERENCES evidence(id),
    UNIQUE (fingerprint_id, dimension)
);
CREATE INDEX idx_fp_elig_fp        ON fp_eligibility(fingerprint_id);
CREATE INDEX idx_fp_elig_dimension ON fp_eligibility(dimension, status);

-- Observations derived from PROVIDER METADATA, kept in their own table so the
-- two provenance channels cannot blend even by an accidental join. M3 decides
-- how they interact; neither wins here.
CREATE TABLE provider_observation (
    id              TEXT PRIMARY KEY,
    fingerprint_id  TEXT NOT NULL REFERENCES fingerprint(id) ON DELETE CASCADE,
    dimension       TEXT NOT NULL,
    value_json      TEXT NOT NULL DEFAULT 'null',
    status          TEXT NOT NULL,
    confidence      REAL,
    evidence_id     TEXT NOT NULL REFERENCES evidence(id),
    UNIQUE (fingerprint_id, dimension)
);
CREATE INDEX idx_provider_obs_fp ON provider_observation(fingerprint_id);


-- ---------------------------------------------------------------------
-- WHAT THE MODEL ACTUALLY RETURNED
-- ---------------------------------------------------------------------

-- Written BEFORE parsing, one row per attempt, never overwritten. If attempt 1
-- fails and attempt 2 succeeds, both survive: the fingerprint references the
-- validated result, and this table answers "what did the model return before
-- our code corrected it?" -- which is the only way to tell a prompt problem
-- from a model problem months later.
--
-- `family` distinguishes the description call from the provider call, so a
-- retry can re-run one without re-paying for the other.
CREATE TABLE llm_call (
    id             TEXT PRIMARY KEY,
    fingerprint_id TEXT REFERENCES fingerprint(id) ON DELETE SET NULL,
    job_id         TEXT REFERENCES job(id),
    cache_key      TEXT NOT NULL,
    family         TEXT NOT NULL,
    purpose        TEXT NOT NULL,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    -- Recorded per call because a model benchmarked at maximum reasoning and
    -- one benchmarked at minimum are different candidates wearing one name.
    reasoning_config TEXT,
    prompt_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    input_hash     TEXT NOT NULL,
    raw_output     TEXT NOT NULL,
    parsed_ok      INTEGER NOT NULL CHECK (parsed_ok IN (0, 1)),
    validated_ok   INTEGER NOT NULL DEFAULT 0 CHECK (validated_ok IN (0, 1)),
    error          TEXT,
    attempt        INTEGER NOT NULL DEFAULT 1,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    cost_usd       REAL,
    created_at     TEXT NOT NULL,
    UNIQUE (cache_key, attempt)
);
CREATE INDEX idx_llm_call_cache  ON llm_call(cache_key);
CREATE INDEX idx_llm_call_job    ON llm_call(job_id);
CREATE INDEX idx_llm_call_model  ON llm_call(model, family);
