-- Migration 0046: what the person thought of a Search Fit score.
-- scope: profile
--
-- Observation only. Nothing reads this table to score, rank, filter or
-- retrieve; it exists so a person's judgements can be exported and read
-- later, by a human, before anyone changes Search Fit again.
--
-- One row per posting per SCORE the person judged: the score, band and
-- versions stored here are the ones on screen when they answered. Answering
-- again about the same score updates the row; any rescore that produces a
-- different number, or the same number under another configuration or schema
-- version, gets a new row, and the old one keeps describing the old score.

CREATE TABLE search_fit_feedback (
    job_id          TEXT    NOT NULL,
    config_id       TEXT    NOT NULL,
    config_version  INTEGER NOT NULL,
    schema_version  INTEGER NOT NULL,
    match_score     REAL    NOT NULL,
    fit_band        TEXT    NOT NULL,
    verdict         TEXT    NOT NULL CHECK (verdict IN (
                        'ACCURATE', 'TOO_HIGH', 'TOO_LOW', 'NOT_ENOUGH_INFORMATION')),
    reason          TEXT,
    note            TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    PRIMARY KEY (job_id, config_id, config_version, schema_version, match_score),
    CHECK (reason IS NULL OR verdict IN ('TOO_HIGH', 'TOO_LOW'))
);
