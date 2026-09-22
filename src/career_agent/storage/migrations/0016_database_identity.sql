-- 0016_database_identity.sql
--
-- What kind of database is this, and when did it last collect anything.
--
-- WHY: the documented launch path served `data/demo.db`, so the product
-- opened on 19 invented postings grouped into 17 roles while the owner's real
-- corpus -- 18,549 scored postings -- sat unused. A demo database is not an
-- acceptable default for a personal tool, and "which database am I looking
-- at" was answerable only by reading the command line.
--
-- The kind lives IN the database rather than being inferred from its path,
-- because a path is a guess and a copied file takes its name with it. A demo
-- database renamed to `personal.db` is still a demo database, and personal
-- mode has to be able to refuse it. This is the same discipline as
-- `job_match.membership`: ask what the system recorded, never what a name
-- happens to look like.
--
-- `last_retrieval_at` is here and not derived from `MAX(job.first_seen_at)`
-- because those are different questions. The newest posting tells you when
-- the employer published; this tells you when WE last went and looked. A
-- retrieval that found nothing new still happened, and the interface has to
-- be able to say "checked an hour ago, nothing new" rather than showing a
-- three-week-old date and implying we have not run.

CREATE TABLE IF NOT EXISTS database_identity (
    id                TEXT NOT NULL PRIMARY KEY CHECK (id = 'singleton'),
    -- 'PERSONAL' or 'DEMO'. Deliberately not a boolean: a third kind
    -- (a shared fixture, a test corpus) must be addable without inverting
    -- the meaning of an existing column.
    kind              TEXT NOT NULL CHECK (kind IN ('PERSONAL', 'DEMO')),
    -- A short stable name for the interface to show, so a person can tell
    -- two databases apart WITHOUT the interface printing an absolute path
    -- that carries their username through a screenshot.
    label             TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    last_retrieval_at TEXT
);
