-- Migration 0057: what a posting is for this profile, one compact row each.
-- scope: profile
--
-- Matching Architecture v2 (ADR-0037). Additive: empty tables beside
-- `job_match`, which stays the source of every screen; nothing in
-- production reads or writes them yet. Only what depends on this person
-- lives here; the facts of the posting are in the shared catalogue
-- (`job_features`), and evidence is a span of the posting's text.
--
-- `profile_inputs` names a row of `profile_match_inputs` (the digest of each
-- preference section, the same for every row of one configuration), and
-- `versions` a row of `profile_match_versions`. A row is stale when a
-- version, a preference section or the posting moved.
CREATE TABLE IF NOT EXISTS profile_match_versions (
    id             TEXT NOT NULL PRIMARY KEY,
    versions_json  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS profile_match_inputs (
    id           TEXT NOT NULL PRIMARY KEY,
    inputs_json  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS profile_job_match (
    job_id              TEXT NOT NULL PRIMARY KEY,
    content_hash        TEXT NOT NULL,
    job_inputs          TEXT NOT NULL,
    profile_inputs      TEXT NOT NULL,
    versions            TEXT NOT NULL,
    match_score         INTEGER NOT NULL,
    fit_band            TEXT NOT NULL,
    eligibility_status  TEXT NOT NULL,
    screening_state     TEXT NOT NULL,
    result_json         TEXT NOT NULL DEFAULT '{}',
    computed_at         TEXT NOT NULL
);
