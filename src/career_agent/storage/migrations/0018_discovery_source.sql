-- 0018_discovery_source.sql
--
-- Where else we have seen a posting we already hold.
--
-- THE QUESTION THIS ANSWERS, AND WHY `job` CANNOT
-- -----------------------------------------------
-- `job` records ONE origin: `provider`, `external_id`, `source_board_id`, all
-- NOT NULL, with `UNIQUE (provider, external_id)`. That is correct and it does
-- not move. A posting belongs to the system the employer published it in, and
-- `source_board` belongs to one company (ADR-0008).
--
-- V3 introduced a source that is not an employer's system. The Speedrun feed
-- republishes postings that originate in Greenhouse, Lever and Ashby, so the
-- same posting can now arrive twice: once from the employer's own board, once
-- from an aggregator. The old model had exactly two ways to record that, and
-- both are wrong:
--
--   * insert a second `job` row. The corpus then double-counts, and every
--     per-company and per-source number becomes untrustworthy. This is the
--     failure `docs/research/source-expansion-speedrun.md` section 4 names.
--   * overwrite the existing row's provenance with the aggregator's. The
--     employer's own record is the better one, and this throws it away.
--
-- So the second sighting goes here instead. `job` keeps its single authoritative
-- origin; this table records that another source also carries it, with the
-- aggregator's own identifiers and its own URL, and nothing about the job row
-- changes at all.
--
-- WHY THIS IS NOT A `job_source` JOIN TABLE
-- ------------------------------------------
-- A general many-to-many between jobs and sources would say that every origin
-- is equally authoritative, and they are not. The employer's ATS record is the
-- text the employer wrote; an aggregator's copy may be complete, truncated,
-- reformatted or rewritten, and `providers.base.ProviderKind` exists to keep
-- that difference visible. One authoritative origin on `job`, plus additional
-- sightings here, is the shape that keeps the asymmetry rather than flattening
-- it.
--
-- MATCHED_BY IS THE AUDIT TRAIL, AND IT IS DELIBERATELY NOT A CONFIDENCE
-- ----------------------------------------------------------------------
-- Every value in it is a DETERMINISTIC rule that either held or did not. There
-- is no similarity score anywhere in this mechanism, because ADR-0008 closes by
-- warning that establishing two boards belong to one company does not make two
-- of their postings the same posting, and no fuzzy job merging exists here.
--
--   ORIGIN_URL      the aggregator published the employer's apply link, and a
--                   provider adapter recognised it as its own posting. This is
--                   the strong one: it yields an exact (provider, external_id).
--   EXTERNAL_ID     the aggregator's own id is byte-identical to an id we
--                   already hold. Observed because this aggregator mints its
--                   job id from the source posting's id.
--   CANONICAL_URL   the two URLs are the same string once scheme and host are
--                   lowercased and the query and fragment are dropped.
--   CONTENT_HASH    the description text is byte-identical after normalisation.
--                   The weakest of the four and still exact: it is equality,
--                   not resemblance.
--
-- Recording WHICH rule matched is what makes the deduplication reviewable. A
-- number of duplicates with no rule beside it cannot be checked by anyone.
--
-- Additive. No existing table is altered, no existing row is rewritten, and
-- every statement is valid PostgreSQL unchanged (ADR-0003).

CREATE TABLE IF NOT EXISTS job_discovery_source (
    id             TEXT PRIMARY KEY,
    job_id         TEXT NOT NULL REFERENCES job(id),
    -- The provider that ALSO carries this posting. Never the job's own
    -- provider: a row saying "we found it where we found it" carries nothing.
    source         TEXT NOT NULL,
    -- That source's own identifier and canonical link for this posting, kept
    -- so the sighting can be re-checked without guessing at a URL shape.
    external_id    TEXT NOT NULL,
    url            TEXT,
    -- The employer's application link as this source published it, when it
    -- published one. This is the field that made the match possible, and it is
    -- stored so a later reader can verify the match rather than trust it.
    origin_url     TEXT,
    -- Which deterministic rule matched. See the note above.
    matched_by     TEXT NOT NULL CHECK (matched_by IN (
                       'ORIGIN_URL', 'EXTERNAL_ID', 'CANONICAL_URL', 'CONTENT_HASH')),
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    -- The sighting exactly as the source described it, archived verbatim, for
    -- the same reason `job_provider_payload` archives the origin payload: a
    -- later reader must be able to ask what this source actually returned.
    payload_json   TEXT NOT NULL DEFAULT '{}',
    UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_discovery_job ON job_discovery_source(job_id);
CREATE INDEX IF NOT EXISTS idx_discovery_source ON job_discovery_source(source);
