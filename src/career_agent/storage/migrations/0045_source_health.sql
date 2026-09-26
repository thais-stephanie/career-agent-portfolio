-- Migration 0045: public, candidate-independent source health.
-- scope: catalogue
--
-- How each collector last went, in the SHARED catalogue, so every local
-- profile reads the same answer about the same public job data. Before this,
-- health was read from `pipeline_run`, which is private to one profile: a
-- second profile saw a source as never refreshed although the catalogue it
-- reads had been refreshed that morning.
--
-- WHAT IS NEVER HERE: which searches were made. A query-driven source
-- (LinkedIn through JobSpy) is asked for one profile's roles; its row says
-- only when it ran and how it ended (COMPLETE, PARTIAL, RATE_LIMITED,
-- FAILED), with counts and a reason CODE. The queries and their failures
-- stay in that profile's own `pipeline_run` and `job_retrieval_lane`.
--
-- `source_key` is the collector: a feed's stage (`collect-gupy`), or one
-- employer-board family inside the shared pass (`collect:greenhouse`).

CREATE TABLE source_health (
    source_key        TEXT PRIMARY KEY,
    last_attempt_at   TEXT NOT NULL,
    last_finished_at  TEXT,
    last_success_at   TEXT,
    last_useful_at    TEXT,
    last_outcome      TEXT NOT NULL CHECK (last_outcome IN (
                          'COMPLETE', 'PARTIAL', 'RATE_LIMITED', 'FAILED')),
    last_reason       TEXT,
    jobs_seen         INTEGER,
    jobs_new          INTEGER,
    updated_at        TEXT NOT NULL
);
