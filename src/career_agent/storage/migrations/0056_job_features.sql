-- Migration 0056: the shared facts of a posting, one row per component.
-- scope: catalogue
--
-- Matching Architecture v2 (ADR-0037). Additive: an empty table, no data
-- moves, nothing in production reads or writes it yet. One row per
-- (posting, component) so a change to one component (language, pay,
-- location ...) replaces only its rows. `stamp` is the digest of the
-- versions of the component and everything it depends on, plus the inputs
-- it read (`matching_v2.components.stamp`): a row whose stamp differs from
-- the one this build would write is stale, whatever its age.
CREATE TABLE IF NOT EXISTS job_features (
    job_id        TEXT NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    component     TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    stamp         TEXT NOT NULL,
    payload_json  TEXT NOT NULL DEFAULT '{}',
    computed_at   TEXT NOT NULL,
    PRIMARY KEY (job_id, component)
);
