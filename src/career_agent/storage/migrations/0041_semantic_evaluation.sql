-- Migration 0041: semantic evaluations and semantic runs.
--
-- A semantic evaluation is what ONE provider said about ONE posting's text
-- against ONE Search Intent under ONE semantic contract, AFTER the
-- deterministic publication gate. Only published findings are stored as
-- evidence; the provider's raw answer is kept beside them for audit, capped.
--
-- The cache identity is the unique key: the posting's content hash, the
-- intent digest, the contract identity (label plus a digest of the
-- instructions and schema), the provider and the model. A change to any of
-- them asks a different question, so an old answer is never read as a new one.
--
-- A provider failure is never stored as an evaluation: it produced no answer,
-- and an absent row is exactly "no semantic evidence". Failures are counted on
-- the run.
--
-- No credential is ever written here. Nothing existing changes meaning: with
-- no rows, every score is the deterministic score.

CREATE TABLE semantic_evaluation (
    id                  TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    intent_digest       TEXT NOT NULL,
    contract            TEXT NOT NULL,
    provider            TEXT NOT NULL,
    model               TEXT NOT NULL,
    requested_provider  TEXT,
    fallback_reason     TEXT,
    evidence_json       TEXT NOT NULL DEFAULT '{}',
    gate_json           TEXT NOT NULL DEFAULT '{}',
    role_core           TEXT,
    provider_confidence REAL,
    raw_response        TEXT,
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    cost_usd            REAL,
    latency_ms          INTEGER,
    run_id              TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE (content_hash, intent_digest, contract, provider, model)
);

CREATE INDEX idx_semantic_evaluation_lookup
    ON semantic_evaluation (content_hash, intent_digest, contract, created_at);

CREATE INDEX idx_semantic_evaluation_job ON semantic_evaluation (job_id);

CREATE TABLE semantic_run (
    id              TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    mode            TEXT NOT NULL,
    provider        TEXT,
    status          TEXT NOT NULL,
    stop_reason     TEXT,
    candidates      INTEGER NOT NULL DEFAULT 0,
    budget_usd      REAL,
    estimated_usd   REAL,
    spent_usd       REAL,
    calls           INTEGER NOT NULL DEFAULT 0,
    cached          INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    rejected        INTEGER NOT NULL DEFAULT 0,
    published       INTEGER NOT NULL DEFAULT 0,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    fallback_json   TEXT NOT NULL DEFAULT '[]'
);
