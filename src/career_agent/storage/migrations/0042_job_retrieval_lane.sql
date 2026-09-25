-- Migration 0042: which retrieval lane found a posting, and with which query.
--
-- The TARGETED lane (searches built from the person's role anchors, their
-- aliases and their work phrases, in each market scope) and the EXPLORATORY
-- lane (every broad collector) land in one corpus. This table records which
-- postings a targeted search returned, so that the two lanes can be measured
-- against each other and so that employer board discovery can start from the
-- employers the person's own searches surfaced.
--
-- PROVENANCE ONLY. Nothing that decides eligibility or computes Search Fit
-- reads it: a posting found by a search is neither more eligible nor a better
-- fit because of how it was found. Additive; no existing row changes.

CREATE TABLE job_retrieval_lane (
    job_id        TEXT NOT NULL REFERENCES job(id),
    lane          TEXT NOT NULL CHECK (lane IN ('targeted', 'exploratory')),
    source        TEXT NOT NULL,
    query_key     TEXT NOT NULL DEFAULT '',
    term_origin   TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    PRIMARY KEY (job_id, lane, source, query_key)
);

CREATE INDEX idx_job_retrieval_lane_lane ON job_retrieval_lane (lane, source);
