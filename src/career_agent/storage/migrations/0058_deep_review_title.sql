-- Migration 0058: a deep reading is of an advert UNDER A TITLE.
-- scope: profile
--
-- NOT additive, and on purpose: the unique key of `deep_review` is rebuilt.
-- The reader is shown the posting's title and its advert, never the
-- employer, so a reading answers exactly (advert, title, profile, contract,
-- provider, model). The key left the title out: one advert published under
-- two titles by the same employer ("Business Process Consultant" and "AI
-- Workflow Analyst") was read once and the other posting counted as read
-- without anybody reading it, while the list showed it unread. 210 postings
-- of the owner's list on 2026-10-19. Reading the second one would have
-- REPLACED the first one's reading (ON CONFLICT DO UPDATE), so the key
-- itself has to change; SQLite changes a UNIQUE constraint only by rebuilding
-- the table.
--
-- The copy is one to one: same ids, same columns, same values, and `title`
-- filled from the posting each reading was asked for, which is the title the
-- reader was sent (the run reads `job.title` when it asks). The new key is
-- the old key plus one column, so rows unique before cannot collide after.
-- A real repost (same advert, same title) still shares one reading.
--
-- `deep_attempt` gains the same column, additively, so "answered and
-- rejected" and "already answered in this job" are asked about the same pair.
--
-- `job` is `main.job` in a one-file database and the attached catalogue's in
-- a split profile; SQLite resolves the unqualified name in that order.
CREATE TABLE deep_review_titled (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
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
    UNIQUE (content_hash, title, profile_digest, contract, provider, model)
);

INSERT INTO deep_review_titled (
    id, job_id, content_hash, title, profile_digest, contract, provider, model,
    stratum, review_json, gate_json, sent_json, raw_response, input_tokens,
    output_tokens, latency_ms, run_id, created_at
)
SELECT
    r.id, r.job_id, r.content_hash,
    coalesce((SELECT j.title FROM job j WHERE j.id = r.job_id), ''),
    r.profile_digest, r.contract, r.provider, r.model, r.stratum, r.review_json,
    r.gate_json, r.sent_json, r.raw_response, r.input_tokens, r.output_tokens,
    r.latency_ms, r.run_id, r.created_at
FROM deep_review r;

DROP TABLE deep_review;

ALTER TABLE deep_review_titled RENAME TO deep_review;

CREATE INDEX idx_deep_review_hash ON deep_review (content_hash, contract, provider);

CREATE INDEX idx_deep_review_job ON deep_review (job_id);

ALTER TABLE deep_attempt ADD COLUMN title TEXT NOT NULL DEFAULT '';

UPDATE deep_attempt
   SET title = coalesce((SELECT j.title FROM job j WHERE j.id = deep_attempt.job_id), '');
