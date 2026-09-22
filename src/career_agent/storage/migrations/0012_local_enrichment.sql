-- Local enrichment -- what a model running on this machine observed.
--
-- WHY THIS IS NOT `fingerprint`, AND NOT `llm_call`
-- --------------------------------------------------
-- `fingerprint` is the durable 33-dimension document produced by the hosted
-- extraction contract, and `llm_call` is the ledger that proves what a hosted
-- request cost and whether its answer may be served again. Both belong to a
-- programme this milestone deliberately paused (ADR-0010).
--
-- A local answer is a different kind of thing and must not be mistaken for
-- either. It costs nothing, so it has no budget row. It comes from a 4B model
-- on the candidate's own laptop against a compact seven-field contract, so it
-- is not comparable to a `gemini-3.1-flash-lite` fingerprint and must never be
-- served in place of one. Sharing a table would make that substitution one
-- careless JOIN away; separate tables make it impossible.
--
-- The isolation is structural, not conventional: `career_agent.local_ai`
-- imports nothing from `career_agent.llm`, and nothing in this file references
-- `fingerprint`, `evidence` or `llm_call`. A local call cannot touch the
-- hosted ledger because there is no path from one to the other.
--
-- WHAT IS STORED, AND WHY THE REJECTIONS ARE STORED TOO
-- -----------------------------------------------------
-- `payload_json` holds the VERIFIED enrichment: every quote in it was checked
-- as a contiguous substring of the posting, and the items whose quotes could
-- not be found were dropped (ADR-0002). `rejected_count` records how many were
-- dropped, because "the model produced eleven observations and eight cited
-- sentences that do not exist" is the single most useful fact about a local
-- model, and it is invisible if the drops are silent.
--
-- `cache_key` covers the description digest, the profile digest, the model,
-- the prompt version, the prompt BYTES and the generation settings. It is the
-- same lesson `cache.static_digest` taught the hosted stack: editing a prompt
-- in place must never serve the answer to the previous one.
--
-- Portability, per Appendix A: TEXT ULID key generated in Python (A1), no
-- SQLite date function (A3), every JSON column NOT NULL with a default (A13),
-- every foreign key declared (A15). Additive only.

CREATE TABLE job_enrichment (
    id              TEXT PRIMARY KEY,

    -- One accepted enrichment per posting. A newer one replaces it, because
    -- there is no version of this worth keeping history of: the cache files
    -- under out/local_ai/ are the archive.
    job_id          TEXT NOT NULL UNIQUE REFERENCES job(id),

    -- The enrichment belongs to the TEXT it was computed from, not to whatever
    -- text the job points at after a re-normalisation.
    content_hash    TEXT NOT NULL REFERENCES job_raw(content_hash),

    -- Identity of the answer. Any change to any of these produces a different
    -- key and therefore a different answer, never a silent reuse.
    cache_key       TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    prompt_digest   TEXT NOT NULL,
    schema_version  INTEGER NOT NULL,

    -- Proof of where it ran. A row whose endpoint is not loopback is a bug
    -- worth being able to find with a query.
    endpoint        TEXT NOT NULL,

    verified_count  INTEGER NOT NULL,
    rejected_count  INTEGER NOT NULL,

    payload_json    TEXT NOT NULL DEFAULT '{}',
    stats_json      TEXT NOT NULL DEFAULT '{}',

    created_at      TEXT NOT NULL
);

CREATE INDEX idx_job_enrichment_job ON job_enrichment (job_id);
