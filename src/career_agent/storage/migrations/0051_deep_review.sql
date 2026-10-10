-- Migration 0051: the deep reading of a posting.
-- scope: profile
--
-- Additive only. A deep review is what ONE provider observed about ONE
-- posting's text for ONE profile, AFTER verification: quotes the posting does
-- not contain and evidence ids that were not sent are gone before a row is
-- written (semantic/deep.py). It never changes `job_match`: the local score
-- stays what the deterministic reader computed, and the review sits beside it.
--
-- `profile_digest` names what the review was asked against: the search
-- intent, the preferred levels and the confirmed evidence (keys and
-- revisions), so a later edit marks the review as written for an earlier
-- profile instead of silently passing it off as current. `sent_json` records
-- which evidence went out for THIS posting, as local ids and revisions, never
-- the text, so a cited id can be resolved and audited later.
--
-- Runs are recorded in `semantic_run` with mode 'deep'.
CREATE TABLE deep_review (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    profile_digest  TEXT NOT NULL,
    contract        TEXT NOT NULL,
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    stratum         TEXT NOT NULL,
    review_json     TEXT NOT NULL DEFAULT '{}',
    gate_json       TEXT NOT NULL DEFAULT '{}',
    sent_json       TEXT NOT NULL DEFAULT '{}',
    raw_response    TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    latency_ms      INTEGER,
    run_id          TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE (content_hash, profile_digest, contract, provider, model)
);

CREATE INDEX idx_deep_review_hash ON deep_review (content_hash, contract, provider);

CREATE INDEX idx_deep_review_job ON deep_review (job_id);
