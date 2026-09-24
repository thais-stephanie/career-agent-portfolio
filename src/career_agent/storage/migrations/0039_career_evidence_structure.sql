-- Migration 0039: a CV read as jobs, and an import that can be put away or removed.
--
-- WHAT WENT WRONG WITHOUT THIS
-- ----------------------------
-- A CV was read as a flat list of lines. A Markdown heading became a claim
-- with its hashes in it, a date line became a claim, and a bullet reached
-- review with no idea whose job it described: a realistic CV produced a wall
-- of 142 disconnected statements. And an import could not be put down
-- honestly: archiving an intake package left its claims counted as "waiting"
-- on the first screen, while discarding a CV read hard-deleted it, including
-- the provenance rows of every suggestion already confirmed from it.
--
-- WHAT THIS ADDS
-- --------------
-- `cv_entry`: one row per JOB the document laid out -- company, role, dates --
-- with the source lines that stated them, verbatim. STRUCTURE, never a claim:
-- nothing here is verified and nothing here reaches `verified_claim`. The
-- person corrects an entry in review; the source lines never change.
--
-- `cv_proposal.entry_key`: which job a suggestion sits under. Moving a
-- suggestion changes this and nothing else. `source_line` and `source_text`
-- are the raw provenance -- the line number and the line exactly as the
-- document had it, Markdown syntax and all -- and they are never rewritten.
--
-- `cv_import.archived_at` / `deleted_at`, `intake_package.deleted_at`:
--
--   ARCHIVED   reversible. Every row stays; the import leaves every active
--              queue and counter; restoring brings it back exactly.
--   DELETED    permanent. Every UNCONFIRMED suggestion is removed. Where some
--              were confirmed, the import row and those confirmed rows stay as
--              the provenance their claims cite, and the import is hidden
--              everywhere else. Where none were, nothing stays.
--
-- Columns rather than new status values, because `cv_import.status` and
-- `intake_package.status` carry CHECK constraints that SQLite can only change
-- by rebuilding the table, and a rebuild of tables holding somebody's review
-- is a risk this change does not need. NULL is the ordinary state and every
-- existing row reads exactly as it did.
--
-- EXISTING WORKSPACES
-- -------------------
-- Nothing is backfilled. A CV read before this migration has no entries, and
-- its suggestions have no entry_key: they are shown as they always were, as
-- suggestions not yet placed in a job, and every answer already given to them
-- stands. Re-reading the file produces the structure.

ALTER TABLE cv_import ADD COLUMN archived_at TEXT;
ALTER TABLE cv_import ADD COLUMN deleted_at TEXT;

ALTER TABLE cv_proposal ADD COLUMN entry_key TEXT;
ALTER TABLE cv_proposal ADD COLUMN source_line INTEGER;
ALTER TABLE cv_proposal ADD COLUMN source_text TEXT;

ALTER TABLE intake_package ADD COLUMN deleted_at TEXT;

CREATE TABLE cv_entry (
    id              TEXT PRIMARY KEY,
    import_id       TEXT NOT NULL REFERENCES cv_import(id),
    entry_key       TEXT NOT NULL,
    section         TEXT NOT NULL,

    -- What the document says, or what the person corrected it to. NULL is
    -- "not stated": the reader never fills a field it could not read.
    company         TEXT,
    role_title      TEXT,
    -- The period as WRITTEN, and the months it normalises to when a month was
    -- stated. A year-only span keeps its years for ordering.
    period_text     TEXT,
    period_start    TEXT,
    period_end      TEXT,
    current_role    INTEGER NOT NULL DEFAULT 0 CHECK (current_role IN (0, 1)),
    start_year      INTEGER,
    end_year        INTEGER,
    location        TEXT,
    -- A name the reader could not place as company or role.
    label           TEXT,

    -- The heading and date lines that stated this job, verbatim, with their
    -- line numbers. Provenance; never edited.
    source_json     TEXT NOT NULL DEFAULT '[]',
    -- What the reader could not work out: company, role, dates, structure.
    unresolved_json TEXT NOT NULL DEFAULT '[]',
    -- Whether a person corrected the structure. The source lines still say
    -- what the document said.
    edited          INTEGER NOT NULL DEFAULT 0 CHECK (edited IN (0, 1)),

    ordinal         INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,

    UNIQUE (import_id, entry_key)
);

CREATE INDEX idx_cv_entry_import ON cv_entry (import_id, ordinal);
CREATE INDEX idx_cv_proposal_entry ON cv_proposal (import_id, entry_key);
