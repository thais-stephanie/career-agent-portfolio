-- Durable input identity survives deletion of work acknowledgements (ADR-0026).
-- Legacy scores have implicit input revision 0. Existing outstanding marks
-- are copied before new trigger generations start above every old value.
CREATE TABLE compute_revision (
    id TEXT PRIMARY KEY CHECK (id IN ('singleton')),
    revision INTEGER NOT NULL,
    population_revision INTEGER NOT NULL
);
INSERT INTO compute_revision
SELECT 'singleton', COALESCE(MAX(generation), 0), 0 FROM job_dirty;
CREATE TABLE job_input_revision (
    job_id TEXT PRIMARY KEY REFERENCES job(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL
);
INSERT INTO job_input_revision SELECT job_id, generation FROM job_dirty;
CREATE TABLE job_score_revision (
    job_id TEXT NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    config_id TEXT NOT NULL,
    config_version INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (job_id, config_id, config_version)
);
CREATE TRIGGER trg_input_revision_insert AFTER INSERT ON job_dirty
BEGIN
    INSERT INTO job_input_revision VALUES (NEW.job_id, NEW.generation)
    ON CONFLICT (job_id) DO UPDATE SET revision = excluded.revision;
END;
CREATE TRIGGER trg_input_revision_update AFTER UPDATE OF generation ON job_dirty
BEGIN
    INSERT INTO job_input_revision VALUES (NEW.job_id, NEW.generation)
    ON CONFLICT (job_id) DO UPDATE SET revision = excluded.revision;
END;
DROP TRIGGER trg_job_dirty_insert;
CREATE TRIGGER trg_job_dirty_insert AFTER INSERT ON job
BEGIN
    UPDATE compute_revision SET revision = revision + 1 WHERE id = 'singleton';
    INSERT INTO job_dirty (job_id, reason, generation, marked_at)
    VALUES (NEW.id, 'NEW',
            (SELECT revision FROM compute_revision WHERE id = 'singleton'),
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
-- `IS NOT` is null-safe. Closing invalidates an in-flight scoring snapshot.
-- Its mark remains pending until the posting can be processed (or is deleted).
DROP TRIGGER trg_job_dirty_update;
CREATE TRIGGER trg_job_dirty_update AFTER UPDATE OF
    content_hash, title, location_raw, posted_at, provider, closed_at, company_id ON job
WHEN NEW.content_hash IS NOT OLD.content_hash
  OR NEW.title IS NOT OLD.title
  OR NEW.location_raw IS NOT OLD.location_raw
  OR NEW.posted_at IS NOT OLD.posted_at
  OR NEW.provider IS NOT OLD.provider
  OR NEW.company_id IS NOT OLD.company_id
  OR NEW.closed_at IS NOT OLD.closed_at
BEGIN
    UPDATE compute_revision SET revision = revision + 1 WHERE id = 'singleton';
    INSERT INTO job_dirty (job_id, reason, generation, marked_at)
    VALUES (NEW.id,
            CASE
                WHEN NEW.content_hash IS NOT OLD.content_hash THEN 'CONTENT'
                WHEN OLD.closed_at IS NOT NULL AND NEW.closed_at IS NULL THEN 'REOPENED'
                ELSE 'FACTS'
            END,
            (SELECT revision FROM compute_revision WHERE id = 'singleton'),
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
DROP TRIGGER trg_job_dirty_payload;
CREATE TRIGGER trg_job_dirty_payload AFTER INSERT ON job_provider_payload
BEGIN
    UPDATE compute_revision SET revision = revision + 1 WHERE id = 'singleton';
    INSERT INTO job_dirty (job_id, reason, generation, marked_at)
    VALUES (NEW.job_id, 'PAYLOAD',
            (SELECT revision FROM compute_revision WHERE id = 'singleton'),
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
DROP TRIGGER trg_job_dirty_sighting_insert;
CREATE TRIGGER trg_job_dirty_sighting_insert AFTER INSERT ON job_discovery_source
BEGIN
    UPDATE compute_revision SET revision = revision + 1 WHERE id = 'singleton';
    INSERT INTO job_dirty (job_id, reason, generation, marked_at)
    VALUES (NEW.job_id, 'SIGHTING',
            (SELECT revision FROM compute_revision WHERE id = 'singleton'),
            strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    ON CONFLICT (job_id) DO UPDATE SET
        reason = excluded.reason,
        generation = excluded.generation,
        marked_at = excluded.marked_at;
END;

DROP TRIGGER trg_job_dirty_sighting_update;
CREATE TRIGGER trg_job_dirty_sighting_update AFTER UPDATE ON job_discovery_source
BEGIN
    UPDATE compute_revision SET revision = revision + 1 WHERE id = 'singleton';
    INSERT INTO job_dirty (job_id, reason, generation, marked_at)
    SELECT id, 'SIGHTING',
           (SELECT revision FROM compute_revision WHERE id = 'singleton'),
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
DROP TRIGGER trg_job_dirty_company;
CREATE TRIGGER trg_job_dirty_company AFTER UPDATE OF name ON company
WHEN NEW.name IS NOT OLD.name
BEGIN
    UPDATE compute_revision SET revision = revision + 1 WHERE id = 'singleton';
    INSERT INTO job_dirty (job_id, reason, generation, marked_at)
    SELECT id, 'COMPANY',
           (SELECT revision FROM compute_revision WHERE id = 'singleton'),
           strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    FROM job WHERE company_id = NEW.id
    ON CONFLICT (job_id) DO UPDATE SET
        reason = excluded.reason,
        generation = excluded.generation,
        marked_at = excluded.marked_at;
END;


CREATE TRIGGER trg_population_job_insert AFTER INSERT ON job
BEGIN
    UPDATE compute_revision SET population_revision = population_revision + 1
    WHERE id = 'singleton';
END;

CREATE TRIGGER trg_population_job_update AFTER UPDATE ON job
BEGIN
    UPDATE compute_revision SET population_revision = population_revision + 1
    WHERE id = 'singleton';
END;

CREATE TRIGGER trg_population_job_delete AFTER DELETE ON job
BEGIN
    UPDATE compute_revision SET population_revision = population_revision + 1
    WHERE id = 'singleton';
END;

CREATE TRIGGER trg_population_job_match_insert AFTER INSERT ON job_match
BEGIN
    DELETE FROM job_score_revision WHERE job_id=NEW.job_id
    AND config_id=NEW.config_id AND config_version=NEW.config_version;
    UPDATE compute_revision SET population_revision = population_revision + 1
    WHERE id = 'singleton';
END;

CREATE TRIGGER trg_population_job_match_update AFTER UPDATE ON job_match
BEGIN
    DELETE FROM job_score_revision WHERE job_id=OLD.job_id
    AND config_id=OLD.config_id AND config_version=OLD.config_version;
    DELETE FROM job_score_revision WHERE job_id=NEW.job_id
    AND config_id=NEW.config_id AND config_version=NEW.config_version;
    UPDATE compute_revision SET population_revision = population_revision + 1
    WHERE id = 'singleton';
END;

CREATE TRIGGER trg_population_job_match_delete AFTER DELETE ON job_match
BEGIN
    DELETE FROM job_score_revision WHERE job_id=OLD.job_id
    AND config_id=OLD.config_id AND config_version=OLD.config_version;
    UPDATE compute_revision SET population_revision = population_revision + 1
    WHERE id = 'singleton';
END;
