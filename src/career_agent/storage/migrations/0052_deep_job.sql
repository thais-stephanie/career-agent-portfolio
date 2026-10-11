-- Migration 0052: an approved deep-reading job, and every call it paid for.
-- scope: profile
--
-- Additive only. A JOB is what the person approved in the preview: which
-- provider and exact model, how many postings, in which order (the plan),
-- and, for a metered API, the most it may spend. It runs in batches without
-- asking again, pauses (by request, on a limit, on the budget, on a usage
-- report it cannot account for) and resumes from where it stopped, never
-- re-asking a posting it already has an answer for.
--
-- An ATTEMPT is one call that reached the provider and came back with an
-- answer, published or not, with the usage the provider reported and the
-- cost accounted from it. The budget is enforced against the SUM of a job's
-- attempts, so a resume spends only what is left. `cost_usd` is an upper
-- bound (the peak rate) when the provider reported usage, and the whole
-- reservation when it did not.
--
-- Runs are still recorded in `semantic_run` with mode 'deep'; reviews in
-- `deep_review`. Nothing here touches `job_match` or any evidence.
CREATE TABLE deep_job (
    id                  TEXT PRIMARY KEY,
    provider            TEXT NOT NULL,
    model               TEXT NOT NULL,
    billing             TEXT NOT NULL,
    contract            TEXT NOT NULL,
    profile_digest      TEXT NOT NULL,
    share_evidence      INTEGER NOT NULL,
    include_unresolved  INTEGER NOT NULL,
    requested           INTEGER NOT NULL,
    budget_usd          REAL,
    plan_json           TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL,
    stop_reason         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX idx_deep_job_created ON deep_job (created_at);

CREATE TABLE deep_attempt (
    id                   TEXT PRIMARY KEY,
    deep_job_id          TEXT NOT NULL REFERENCES deep_job (id),
    job_id               TEXT NOT NULL,
    content_hash         TEXT NOT NULL,
    provider             TEXT NOT NULL,
    model                TEXT,
    contract             TEXT NOT NULL,
    profile_digest       TEXT NOT NULL,
    outcome              TEXT NOT NULL,
    input_tokens         INTEGER,
    cached_input_tokens  INTEGER,
    output_tokens        INTEGER,
    cost_usd             REAL NOT NULL DEFAULT 0,
    run_id               TEXT,
    created_at           TEXT NOT NULL
);

CREATE INDEX idx_deep_attempt_job ON deep_attempt (deep_job_id);

CREATE INDEX idx_deep_attempt_hash ON deep_attempt (content_hash, provider, contract);
