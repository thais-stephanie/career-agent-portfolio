-- Migration 0057: what a posting is for this profile, one row per component.
-- scope: profile
--
-- Matching Architecture v2 (ADR-0037). Additive: an empty table beside
-- `job_match`, which stays the source of every screen; nothing in production
-- reads or writes this one yet. Only results that depend on this person live
-- here (work and tools matches, eligibility, compensation, seniority, the
-- score); the facts of the posting are in the shared catalogue
-- (`job_features`). `stamp` folds the job components' versions, the posting's
-- inputs and the digest of exactly the preference sections the component reads.
CREATE TABLE IF NOT EXISTS profile_job_match (
    job_id        TEXT NOT NULL,
    component     TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    stamp         TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    computed_at   TEXT NOT NULL,
    PRIMARY KEY (job_id, component)
);
