-- A fifth identity rule for a sighting, EMPLOYER_TITLE: the same employer row
-- and the same title, normalised, with exactly one such posting open. It is
-- what lets a metadata-only lead (Jobgether publishes no origin pointer and
-- no advert) fold into its employer's own full posting. Deterministic and
-- verifiable, like the four before it, and weaker than each, which is why it
-- is used only when the other four have nothing to work with and only when
-- the match is unique (2026-09-11).
--
-- SQLite cannot alter a CHECK constraint in place, so the table is rebuilt.
-- 114 rows at the time; every row and every index is carried across.
CREATE TABLE job_discovery_source_new (
    id             TEXT PRIMARY KEY,
    job_id         TEXT NOT NULL REFERENCES job(id),
    source         TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    url            TEXT,
    origin_url     TEXT,
    matched_by     TEXT NOT NULL CHECK (matched_by IN (
                       'ORIGIN_URL', 'EXTERNAL_ID', 'CANONICAL_URL', 'CONTENT_HASH',
                       'EMPLOYER_TITLE')),
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    UNIQUE (source, external_id)
);
INSERT INTO job_discovery_source_new
    SELECT id, job_id, source, external_id, url, origin_url, matched_by,
           first_seen_at, last_seen_at, payload_json
    FROM job_discovery_source;
DROP TABLE job_discovery_source;
ALTER TABLE job_discovery_source_new RENAME TO job_discovery_source;
CREATE INDEX IF NOT EXISTS idx_discovery_job ON job_discovery_source(job_id);
CREATE INDEX IF NOT EXISTS idx_discovery_source ON job_discovery_source(source);
