-- Migration 0056: the shared facts of a posting, one compact row each.
-- scope: catalogue
--
-- Matching Architecture v2 (ADR-0037). Additive: empty tables, no data
-- moves, nothing in production reads or writes them yet.
--
-- One row per posting (measured 2026-10-14: a row per component cost a
-- content hash, a stamp and a primary-key entry nine times over). Tokens are
-- NOT stored: they are rebuilt from `job_raw` in 2 ms, and storing them was
-- 91% of the first prototype. Evidence is a span of `job_raw`'s text, never
-- a copy of it.
--
-- `inputs` holds the digests of the raw inputs the components read (the
-- text, the board's metadata, the configured headings); `versions` names a
-- row of `job_feature_versions`, the version of every component when the
-- row was written. A component is stale when a version in its lineage or
-- one of its inputs moved (`matching_v2.components.stale`), so "what does
-- this change make stale?" is answered without reading a payload.
CREATE TABLE IF NOT EXISTS job_feature_versions (
    id             TEXT NOT NULL PRIMARY KEY,
    versions_json  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS job_features (
    job_id        TEXT NOT NULL PRIMARY KEY REFERENCES job(id) ON DELETE CASCADE,
    content_hash  TEXT NOT NULL,
    inputs        TEXT NOT NULL,
    versions      TEXT NOT NULL,
    -- One item per sentence: context letter, gap, length (`R0.85 Q2.40`).
    sentences     TEXT NOT NULL DEFAULT '',
    facts_json    TEXT NOT NULL DEFAULT '{}',
    computed_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS job_features_by_versions ON job_features (versions);
