-- Migration 0053: which reader made each score, answerable from an index;
-- and the engagement the Contract filter reads.
-- scope: profile
--
-- Additive only: one index, no data moves. "Which open scores were read by a
-- reader this build no longer is" is asked by the recalculation plan, by Home
-- (how many scores a Recalculate would redo) and by the notice that offers it.
-- `input_digest` is the LAST column of `job_match`, after the large
-- `result_json`, so reading it walked every score's overflow pages: 16 s on a
-- 172,000-posting synthetic corpus and 2 s on the owner's for every page
-- open. With the identity in the index the count reads the index alone and
-- the plan fetches only the rows it lists (`job_id` sits at the start of the
-- row, before any overflow). `job_id` is left OUT of the index on purpose:
-- with it the index covered every "scores of this configuration" probe and
-- the planner moved queries off `idx_job_match_population`.
CREATE INDEX IF NOT EXISTS idx_job_match_reading
    ON job_match (config_id, config_version, input_digest);

-- THE ENGAGEMENT A POSTING OFFERS, as the Contract filter reads it: the one
-- the advert or its board STATED, or the contractor default for an American
-- employer hiring in Latin America that stated none (`match.engagement`).
-- NULL when neither. Kept apart from `employment_type`, which holds what the
-- board's own job-type field said and is often only the working pattern
-- (full-time, part-time): the two answer different questions. Additive; rows
-- scored before readers-11 stay NULL until their next scoring.
ALTER TABLE job_match ADD COLUMN engagement TEXT;
CREATE INDEX IF NOT EXISTS idx_job_match_engagement
    ON job_match (config_id, config_version, engagement);
