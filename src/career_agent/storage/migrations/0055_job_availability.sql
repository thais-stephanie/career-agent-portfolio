-- Migration 0055: whether a posting's own page was still up when last asked.
-- scope: catalogue
--
-- A public fact about a public posting, the same for every profile, so it
-- lives beside the posting. Independent of matching and of the posting's age:
-- a removed advert keeps its score, its history and its place in anybody's
-- saved list; only the default search sets it aside, and only when the page
-- itself said it is gone (`career_agent.availability`).
--
-- One row per checked posting, replaced by the next check. No row means the
-- posting was never checked, which is a state of its own and never "removed".
CREATE TABLE IF NOT EXISTS job_availability (
    job_id        TEXT PRIMARY KEY REFERENCES job(id) ON DELETE CASCADE,
    state         TEXT NOT NULL CHECK (state IN ('AVAILABLE', 'REMOVED', 'INCONCLUSIVE', 'EXCLUDED')),
    checked_at    TEXT NOT NULL,
    url           TEXT,
    final_url     TEXT,
    http_status   INTEGER,
    evidence      TEXT,
    method        TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_availability_state ON job_availability (state, checked_at);
