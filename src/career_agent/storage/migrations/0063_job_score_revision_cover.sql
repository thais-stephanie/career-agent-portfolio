-- Migration 0063: each score's receipt, answerable from an index.
-- scope: profile
--
-- Additive only: one index, no data moves. "Does this posting's score hold a
-- receipt for its current input revision" was answered by the primary key
-- `(job_id, config_id, config_version)` and then a read of the receipt's row
-- for `revision`: one more random page per open posting, the largest share of
-- the outdated count on a cold disk. With the revision in the index the
-- probe reads the index alone (see 0062, the other half). The receipt table
-- holds one row per stored score, so the index grows with `job_match`.
-- `rescore._missing_or_stale` names it with INDEXED BY.
CREATE INDEX IF NOT EXISTS idx_job_score_revision_cover
    ON job_score_revision (job_id, config_id, config_version, revision);
