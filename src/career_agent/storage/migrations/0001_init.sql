-- Migration 0001: M0 and M1A tables.
--
-- Portability rules honoured throughout (see docs/architecture Appendix A):
--   A1  every primary key is a ULID stored as TEXT, generated in Python.
--       No AUTOINCREMENT, no reliance on rowid.
--   A3  no SQLite date function appears anywhere. All timestamps are written
--       by career_agent.clock.now_utc() as ISO-8601 UTC ending in 'Z', which
--       also sorts correctly as plain text.
--   A11 enums are enforced with portable CHECK (col IN (...)) only.
--   A13 every JSON column is NOT NULL with a '{}' or '[]' default, so the
--       eventual TEXT -> JSONB conversion cannot trip over a NULL.
--   A15 foreign keys are declared and enforced (PRAGMA set in db.py).

CREATE TABLE schema_migration (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- CANDIDATE
-- The candidate is a first-class row so career claims can hang off
-- something more durable than a preference snapshot.
-- ---------------------------------------------------------------------
CREATE TABLE candidate (
    id             TEXT PRIMARY KEY,
    candidate_key  TEXT NOT NULL UNIQUE,
    display_name   TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

-- Content-hashed snapshot of profile.local.yaml. Every evaluation records
-- which version produced it, so past decisions stay reproducible even after
-- preferences change.
CREATE TABLE search_profile_version (
    id             TEXT PRIMARY KEY,
    candidate_id   TEXT NOT NULL REFERENCES candidate(id),
    content_hash   TEXT NOT NULL UNIQUE,
    yaml_snapshot  TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX idx_spv_candidate ON search_profile_version(candidate_id);

-- Career facts. Deliberately NOT attached to a profile version: raising a
-- salary floor must not duplicate every claim the candidate owns. Edits
-- create a new revision and supersede the old one.
CREATE TABLE verified_claim (
    id                TEXT PRIMARY KEY,
    candidate_id      TEXT NOT NULL REFERENCES candidate(id),
    claim_key         TEXT NOT NULL,
    revision          INTEGER NOT NULL,
    claim_type        TEXT NOT NULL CHECK (claim_type IN (
                          'EMPLOYMENT','SKILL','TOOL','ACHIEVEMENT',
                          'METRIC','EDUCATION','CERTIFICATION','PROJECT')),
    text              TEXT NOT NULL,
    employer          TEXT,
    period_start      TEXT,
    period_end        TEXT,
    source            TEXT NOT NULL CHECK (source IN (
                          'RESUME','LINKEDIN','SELF_ATTESTED','DOCUMENT')),
    verified          INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
    evidence_ref      TEXT,
    tools_json        TEXT NOT NULL DEFAULT '[]',
    tags_json         TEXT NOT NULL DEFAULT '[]',
    valid_from        TEXT NOT NULL,
    superseded_by_id  TEXT REFERENCES verified_claim(id),
    created_at        TEXT NOT NULL,
    UNIQUE (candidate_id, claim_key, revision)
);
-- Current claims are those with superseded_by_id IS NULL.
CREATE INDEX idx_claim_current ON verified_claim(candidate_id, superseded_by_id);

-- ---------------------------------------------------------------------
-- COMPANY REGISTRY AND COLLECTION  (M1A)
-- ---------------------------------------------------------------------
CREATE TABLE company (
    id             TEXT PRIMARY KEY,
    slug           TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    website        TEXT,
    hq_country     TEXT,     -- drives the target_company_markets signal only,
                             -- never geographic eligibility
    size_estimate  TEXT,
    stage          TEXT,
    industry       TEXT,
    notes          TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE source_board (
    id                 TEXT PRIMARY KEY,
    company_id         TEXT NOT NULL REFERENCES company(id),
    provider           TEXT NOT NULL,
    board_identifier   TEXT NOT NULL,
    board_url          TEXT,
    active             INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    -- Updated ONLY after a successful collection. If a network failure
    -- advanced this, the next pass would mass-close a company's jobs.
    last_collected_at  TEXT,
    last_error         TEXT,
    UNIQUE (provider, board_identifier)
);
CREATE INDEX idx_board_company ON source_board(company_id);

-- Content-addressed raw text. Deduplicates reposts and gives every evidence
-- quote an immutable text to be verified against.
CREATE TABLE job_raw (
    content_hash      TEXT PRIMARY KEY,
    description_text  TEXT NOT NULL,
    description_html  TEXT,
    byte_length       INTEGER NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE TABLE job (
    id                 TEXT PRIMARY KEY,
    company_id         TEXT NOT NULL REFERENCES company(id),
    source_board_id    TEXT NOT NULL REFERENCES source_board(id),
    provider           TEXT NOT NULL,
    external_id        TEXT NOT NULL,
    url                TEXT NOT NULL,
    title              TEXT NOT NULL,
    department         TEXT,
    location_raw       TEXT,
    posted_at          TEXT,
    content_hash       TEXT REFERENCES job_raw(content_hash),
    first_seen_at      TEXT NOT NULL,
    last_seen_at       TEXT NOT NULL,
    closed_at          TEXT,
    collection_status  TEXT NOT NULL CHECK (collection_status IN (
                           'DISCOVERED','FETCHED','NORMALISED','PREFILTERED_OUT',
                           'FINGERPRINTED','EXTRACTION_FAILED','RESOLVED',
                           'EVALUATED','CLOSED')),
    prefilter_reason   TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE (provider, external_id)
);
CREATE INDEX idx_job_status    ON job(collection_status);
CREATE INDEX idx_job_company   ON job(company_id);
CREATE INDEX idx_job_hash      ON job(content_hash);
CREATE INDEX idx_job_last_seen ON job(last_seen_at);

-- The provider's structured payload, archived verbatim and addressable by
-- JSON path so PROVIDER_FIELD evidence is mechanically verifiable at M2.
CREATE TABLE job_provider_payload (
    id            TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL REFERENCES job(id),
    provider      TEXT NOT NULL,
    payload_hash  TEXT NOT NULL,
    payload_json  TEXT NOT NULL DEFAULT '{}',
    captured_at   TEXT NOT NULL,
    UNIQUE (job_id, payload_hash)
);

-- ---------------------------------------------------------------------
-- OPERATIONS
-- ---------------------------------------------------------------------
CREATE TABLE pipeline_run (
    id           TEXT PRIMARY KEY,
    stage        TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL CHECK (status IN ('RUNNING','OK','FAILED')),
    stats_json   TEXT NOT NULL DEFAULT '{}',
    error        TEXT
);
CREATE INDEX idx_run_stage ON pipeline_run(stage, started_at);
