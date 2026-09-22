-- Migration 0034: rescore work proportional to what CHANGED, not to the corpus.
--
-- THE DEFECT. `rescore` walked every open posting -- 245,871 of them on
-- 2026-09-12 -- to find the few thousand that needed a score, reading every
-- description and every archived payload from disk on the way, and then
-- rebuilt the whole full-text index. Measured on the production ledger:
-- 348 postings scored in 14.7 minutes, 5,930 in 37.9, 11,524 in 50.3, and
-- a walk that scored NOTHING would still have cost the first fifteen.
--
-- FOUR PIECES, each the smallest thing that answers one question.
--
-- 1. `job_dirty`: WHICH POSTINGS MIGHT HAVE A DIFFERENT ANSWER NOW. Filled by
--    triggers rather than by the twenty-odd collectors, so a collector that
--    does not know the ledger exists still feeds it, and so a change made by
--    hand in a SQL shell is not invisible to the next rescore. A row is a
--    claim that the posting's score INPUTS may have moved: it was inserted;
--    its text, title, location, date or provider changed; it came back from
--    closed; its archived payload grew; a sighting of it was recorded or
--    refreshed; its employer was renamed (the search index carries the name).
--    Nothing here reads a preference, a candidate fact or a score.
--
--    `generation` is the race guard. A rescore reads the ledger, scores for
--    minutes, then clears what it read -- and a collector may re-mark a
--    posting in between. Clearing by `(job_id, generation)` leaves the newer
--    mark in place. It is `MAX + 1` under the trigger rather than
--    AUTOINCREMENT (rule A1: no reliance on rowid); the index below is what
--    makes that MAX a leaf read rather than a scan.
--
-- 2. `search_index_map`: WHERE EACH POSTING'S ROW IS in the FTS5 index.
--    `job_id` is UNINDEXED there, so deleting one posting's row by id is a
--    scan of the whole content table (1.5 GB of text at this size). With the
--    rowid known, replacing one posting's entry is two point operations,
--    and the index can be refreshed for the dirty set instead of rebuilt.
--    Filled by `search_index.rebuild`; empty until the next full rebuild,
--    which is how the incremental path knows it may not run yet.
--
-- 3. `idx_job_match_population` WIDENED into a covering index. "Does this
--    posting have a CURRENT score" is: same configuration version, result
--    schema at least this build's, the SAME content hash, the SAME rule
--    digest. The old three-column index answered the first and then read the
--    row for the rest -- 245,830 random row reads, 50 seconds cold, before a
--    single posting was scored. Every column the question needs is in the
--    key now, so the anti-join between open postings and their scores never
--    leaves the two indexes. Same name, because nothing names indexes and
--    every query the narrower one served is served by this one.
--
-- PORTABILITY NOTE. The trigger bodies use `strftime(..., 'now')`, which rule
-- A3 forbids in ordinary DDL. A trigger cannot call `clock.now_utc()`, and a
-- trigger body is dialect-specific by nature -- the PostgreSQL port rewrites
-- these six in PL/pgSQL with `now()`, and nothing else in this file needs to
-- change. That is the one documented exception in the schema.

CREATE TABLE job_dirty (
    job_id      TEXT PRIMARY KEY REFERENCES job(id) ON DELETE CASCADE,
    reason      TEXT NOT NULL CHECK (reason IN (
                    'NEW', 'CONTENT', 'FACTS', 'REOPENED', 'PAYLOAD',
                    'SIGHTING', 'COMPANY', 'REQUESTED')),
    generation  INTEGER NOT NULL,
    marked_at   TEXT NOT NULL
);

CREATE INDEX idx_job_dirty_generation ON job_dirty(generation);

CREATE TABLE search_index_map (
    job_id     TEXT PRIMARY KEY,
    fts_rowid  INTEGER NOT NULL UNIQUE
);

-- 4. `idx_job_source_board`: WHICH POSTINGS A BOARD HOLDS OPEN. `close_absent`
--    asks it after every board a collection reads, and the only index that
--    could help was the open-population one -- so each board's question was
--    a walk over every open posting in the corpus (245,871), 6.3 s cold and
--    0.2 s warm on the 2026-09-12 copy, times every board in the pass. With
--    `external_id` in the key the question is answered inside the index.
CREATE INDEX idx_job_source_board ON job(source_board_id, closed_at, external_id);

DROP INDEX IF EXISTS idx_job_match_population;
CREATE INDEX idx_job_match_population
    ON job_match(config_id, config_version, job_id, content_hash, schema_version, config_digest);

-- A posting that did not exist has no score.
CREATE TRIGGER trg_job_dirty_insert AFTER INSERT ON job
BEGIN
    INSERT INTO job_dirty (job_id, reason, generation, marked_at)
    VALUES (NEW.id, 'NEW',
            COALESCE((SELECT MAX(generation) FROM job_dirty), 0) + 1,
            strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    ON CONFLICT (job_id) DO UPDATE SET
        reason = excluded.reason,
        generation = excluded.generation,
        marked_at = excluded.marked_at;
END;

-- Only the columns a score is computed from, and only when one of them
-- actually moved. `upsert_seen` writes every one of these on every pass a
-- board returns the posting, so a trigger on the UPDATE alone would mark
-- the whole board dirty every morning; the WHEN clause compares values.
-- `IS NOT` is null-safe. Closing a posting marks nothing (a closed posting
-- is not scored); coming back from closed does.
CREATE TRIGGER trg_job_dirty_update AFTER UPDATE OF
    content_hash, title, location_raw, posted_at, provider, closed_at ON job
WHEN NEW.content_hash IS NOT OLD.content_hash
  OR NEW.title IS NOT OLD.title
  OR NEW.location_raw IS NOT OLD.location_raw
  OR NEW.posted_at IS NOT OLD.posted_at
  OR NEW.provider IS NOT OLD.provider
  OR (OLD.closed_at IS NOT NULL AND NEW.closed_at IS NULL)
BEGIN
    INSERT INTO job_dirty (job_id, reason, generation, marked_at)
    VALUES (NEW.id,
            CASE
                WHEN NEW.content_hash IS NOT OLD.content_hash THEN 'CONTENT'
                WHEN OLD.closed_at IS NOT NULL AND NEW.closed_at IS NULL THEN 'REOPENED'
                ELSE 'FACTS'
            END,
            COALESCE((SELECT MAX(generation) FROM job_dirty), 0) + 1,
            strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    ON CONFLICT (job_id) DO UPDATE SET
        reason = excluded.reason,
        generation = excluded.generation,
        marked_at = excluded.marked_at;
END;

-- A newer archived payload is what `facts.py` reads for salary, workplace
-- type and engagement; the newest wins, so a new row can change the score.
-- `ProviderPayloadRepo.put` is ON CONFLICT DO NOTHING on the payload hash,
-- so an unchanged payload inserts nothing and marks nothing.
CREATE TRIGGER trg_job_dirty_payload AFTER INSERT ON job_provider_payload
BEGIN
    INSERT INTO job_dirty (job_id, reason, generation, marked_at)
    VALUES (NEW.job_id, 'PAYLOAD',
            COALESCE((SELECT MAX(generation) FROM job_dirty), 0) + 1,
            strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    ON CONFLICT (job_id) DO UPDATE SET
        reason = excluded.reason,
        generation = excluded.generation,
        marked_at = excluded.marked_at;
END;

-- A sighting carries the hiring scope a canonical board never states
-- (ADR-0013 addendum), and the newest sighting with a scope is the one
-- read -- so a refresh that only moved `last_seen_at` can reorder two
-- sightings and change the answer. Every insert and every update marks the
-- posting; when a sighting is re-pointed at another posting, both are marked.
CREATE TRIGGER trg_job_dirty_sighting_insert AFTER INSERT ON job_discovery_source
BEGIN
    INSERT INTO job_dirty (job_id, reason, generation, marked_at)
    VALUES (NEW.job_id, 'SIGHTING',
            COALESCE((SELECT MAX(generation) FROM job_dirty), 0) + 1,
            strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    ON CONFLICT (job_id) DO UPDATE SET
        reason = excluded.reason,
        generation = excluded.generation,
        marked_at = excluded.marked_at;
END;

CREATE TRIGGER trg_job_dirty_sighting_update AFTER UPDATE ON job_discovery_source
BEGIN
    INSERT INTO job_dirty (job_id, reason, generation, marked_at)
    SELECT id, 'SIGHTING',
           COALESCE((SELECT MAX(generation) FROM job_dirty), 0) + 1,
           strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    FROM job WHERE id IN (OLD.job_id, NEW.job_id)
    ON CONFLICT (job_id) DO UPDATE SET
        reason = excluded.reason,
        generation = excluded.generation,
        marked_at = excluded.marked_at;
END;

-- The search index carries the employer's name beside each posting. A
-- rename is rare and touches every posting of that employer; the score
-- itself never reads the name.
CREATE TRIGGER trg_job_dirty_company AFTER UPDATE OF name ON company
WHEN NEW.name IS NOT OLD.name
BEGIN
    INSERT INTO job_dirty (job_id, reason, generation, marked_at)
    SELECT id, 'COMPANY',
           COALESCE((SELECT MAX(generation) FROM job_dirty), 0) + 1,
           strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    FROM job WHERE company_id = NEW.id
    ON CONFLICT (job_id) DO UPDATE SET
        reason = excluded.reason,
        generation = excluded.generation,
        marked_at = excluded.marked_at;
END;

-- Postings touched since the search index was last built. `updated_at` moves
-- on every sighting of a posting, so this over-approximates the set whose
-- text may have changed between the last rebuild and this ledger existing --
-- which is the safe direction. On a database whose index was never built
-- the subquery is NULL and nothing is seeded; the first rescore rebuilds.
INSERT INTO job_dirty (job_id, reason, generation, marked_at)
SELECT j.id, 'FACTS', 1, j.updated_at
FROM job j
WHERE j.updated_at > (SELECT built_at FROM search_index_state WHERE id = 'singleton')
ON CONFLICT (job_id) DO NOTHING;
