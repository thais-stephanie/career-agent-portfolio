-- 0019_application_workspace.sql
--
-- Four tables, one question each, and every one of them holds text the
-- candidate wrote about herself rather than text an employer published.
--
-- WHY CV REVIEW NEEDS A TABLE AT ALL
-- -----------------------------------
-- `cv-import` reads a document, proposes what it appears to say, and asks
-- about each proposal in the terminal. That works because a terminal session
-- is one continuous act: the list is held in memory and either the person
-- finishes or nothing is stored.
--
-- A browser is not one continuous act. Somebody opens the review, accepts
-- eleven of forty proposals, and closes the tab. With the proposals in server
-- memory those eleven are gone, and the honest thing the interface could say
-- afterwards is nothing at all. Worse, re-importing the same CV would offer
-- the same forty again with no memory that thirty of them were already
-- answered -- and a review that keeps re-asking is a review people stop
-- reading, which is how an unread proposal becomes an accepted claim.
--
-- So the STAGE is persisted and the DECISION is persisted. A claim is still
-- only created by an acceptance, and `verified=True` is still set in exactly
-- one place (`cv.propose.to_claim`). This table changes where the pending
-- list lives, not who confirms it.
--
-- WHY A REJECTION IS RECORDED HERE AND NOWHERE ELSE
-- -------------------------------------------------
-- `cv-import --review` deliberately records nothing about a rejection: a list
-- of rejected proposals is a list of things somebody did not want kept, and
-- keeping it anyway would be the opposite of what saying no means.
--
-- That still holds for CLAIMS. It cannot hold for a resumable review, because
-- "this proposal was answered no" is the only thing that stops it reappearing
-- at the top of the list tomorrow. The distinction is where the row lives: a
-- rejection is a fact about ONE READ OF ONE DOCUMENT, it stays inside that
-- import, it never becomes a claim, it is never consulted by the matcher or by
-- preparation, and discarding the import discards it. Nothing about the person
-- is recorded -- only that this line of this file was not wanted.
--
-- WHAT IS HASHED, AND WHAT IS NOT
-- --------------------------------
-- `text_sha256` is the SHA-256 of the EXTRACTED TEXT, and it exists so that
-- re-importing the same document is recognised rather than duplicated. It is
-- not a secret and it is not reversible into the CV.
--
-- The CV FILE ITSELF is never stored, never copied, and its bytes are never
-- hashed. Hashing the file would buy nothing the text hash does not (the same
-- CV exported twice from the same editor has different bytes and identical
-- text) while creating a durable identifier for a document this product has
-- no reason to identify. `docs/PRIVACY.md` says so in words.
--
-- WHY A REQUIREMENT REVIEW IS NOT AN EDIT TO ANYTHING
-- ----------------------------------------------------
-- `match.preparation` decides, deterministically, whether a confirmed claim
-- answers a requirement the posting fired. It will sometimes be wrong in both
-- directions: a phrase can appear in a claim that is not about the work, and
-- real experience can be missing from the profile entirely.
--
-- The correction goes in its own table and touches neither side. The
-- employer's sentence is source text and is immutable (ADR-0002). The claim is
-- the candidate's own confirmed statement and has a revision chain of its own.
-- What is recorded here is a THIRD thing -- what the person thinks of the
-- relationship between them -- and collapsing it into either would destroy the
-- provenance that makes both trustworthy.
--
-- `EVIDENCE_MISSING` in particular creates NOTHING. It is a note that says
-- "I have done this and it is not in my profile", and the only action it
-- unlocks is a route back to the evidence ledger where the person can add a
-- claim herself. A verdict may not become a claim; that would be this system
-- inventing experience from a click.
--
-- Portability, per Appendix A: TEXT ULID keys generated in Python (A1), no
-- SQLite date function anywhere (A3), enums as portable CHECK (col IN (...))
-- (A11), every foreign key declared (A15). Additive only.

-- ---------------------------------------------------------------------
-- ONE READ OF ONE DOCUMENT
-- ---------------------------------------------------------------------

-- A staged CV read, awaiting review. `status` is about the REVIEW, not about
-- the document: OPEN means proposals are still waiting for an answer, CLOSED
-- means every one of them has been answered or the person discarded the rest.
--
-- `source_name` is the file's own name as the person chose it, kept so the
-- review can say which document it is showing. The PATH is not kept: an
-- absolute path is a fact about this machine's filesystem, and a backup that
-- carries one tells a reader where somebody's private documents live.
CREATE TABLE cv_import (
    id             TEXT PRIMARY KEY,
    candidate_id   TEXT NOT NULL REFERENCES candidate(id),

    source_name    TEXT NOT NULL,
    kind           TEXT NOT NULL,
    pages          INTEGER,
    characters     INTEGER NOT NULL,

    -- The extracted text, hashed. Identity for "have I read this before".
    text_sha256    TEXT NOT NULL,

    status         TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),

    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX idx_cv_import_candidate ON cv_import (candidate_id, created_at);
CREATE INDEX idx_cv_import_text ON cv_import (candidate_id, text_sha256);

-- ---------------------------------------------------------------------
-- ONE THING THE DOCUMENT APPEARS TO SAY
-- ---------------------------------------------------------------------

-- `text` is what the reader PROPOSED. `evidence` is the line it came from,
-- verbatim, and the two are separate columns because that separation is the
-- entire mechanism by which an edit stays honest: accepting a corrected
-- sentence changes `decided_text` and never `evidence`.
--
-- `decision` has four values and EDITED is not a decoration on ACCEPTED. A
-- reader of this table should be able to see, without joining anything, that
-- the stored claim says something the CV did not quite say.
--
-- `claim_id` points at what an acceptance produced, so the ledger and the
-- review can be read against each other. It is NULL for everything not
-- accepted, and it stays pointing at the claim it created even after that
-- claim has been superseded -- the question it answers is "what did this
-- acceptance make", which does not change later.
CREATE TABLE cv_proposal (
    id             TEXT PRIMARY KEY,
    import_id      TEXT NOT NULL REFERENCES cv_import(id),

    claim_key      TEXT NOT NULL,
    claim_type     TEXT NOT NULL CHECK (claim_type IN (
                       'EMPLOYMENT','SKILL','TOOL','ACHIEVEMENT',
                       'METRIC','EDUCATION','CERTIFICATION','PROJECT')),
    section        TEXT NOT NULL,

    -- What the reader proposed, and the line it was read from.
    text           TEXT NOT NULL,
    evidence       TEXT NOT NULL,

    -- Whether the line carries a figure. A figure is flagged for a person to
    -- check; it is never lifted out of its sentence.
    has_measurement INTEGER NOT NULL DEFAULT 0 CHECK (has_measurement IN (0, 1)),

    decision       TEXT NOT NULL CHECK (decision IN (
                       'PENDING', 'ACCEPTED', 'EDITED', 'REJECTED')),
    -- The text actually confirmed. Equal to `text` for ACCEPTED, the person's
    -- own wording for EDITED, NULL for everything undecided or refused.
    decided_text   TEXT,
    decided_at     TEXT,

    claim_id       TEXT REFERENCES verified_claim(id),

    ordinal        INTEGER NOT NULL,
    created_at     TEXT NOT NULL,

    UNIQUE (import_id, claim_key)
);

CREATE INDEX idx_cv_proposal_import ON cv_proposal (import_id, ordinal);
CREATE INDEX idx_cv_proposal_pending ON cv_proposal (import_id, decision);

-- ---------------------------------------------------------------------
-- WHAT THE PERSON THINKS OF ONE REQUIREMENT
-- ---------------------------------------------------------------------

-- One row per (candidate, job, requirement). The requirement is identified by
-- the SIGNAL the posting fired, which is the same identifier
-- `match.preparation` uses, so a review survives a rescore: the score moves,
-- the signal id does not.
--
-- There is no foreign key to a signal, deliberately. Signals are configuration
-- and a CHECK or a reference here would turn adding one into a migration --
-- the same reasoning that keeps `title_class` unconstrained in 0011.
CREATE TABLE requirement_review (
    id             TEXT PRIMARY KEY,
    candidate_id   TEXT NOT NULL REFERENCES candidate(id),
    job_id         TEXT NOT NULL REFERENCES job(id),
    signal_id      TEXT NOT NULL,

    -- SUPPORTS / PARTIALLY_SUPPORTS / DOES_NOT_SUPPORT are verdicts on the
    -- evidence this system proposed. EVIDENCE_MISSING is the fourth answer and
    -- the only one that is not about the proposal at all: it says the person
    -- has relevant experience that is not in her profile yet. It creates no
    -- claim. Nothing here does.
    verdict        TEXT NOT NULL CHECK (verdict IN (
                       'SUPPORTS', 'PARTIALLY_SUPPORTS',
                       'DOES_NOT_SUPPORT', 'EVIDENCE_MISSING')),
    note           TEXT,

    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,

    UNIQUE (candidate_id, job_id, signal_id)
);

CREATE INDEX idx_requirement_review_job ON requirement_review (candidate_id, job_id);

-- ---------------------------------------------------------------------
-- WHERE THE PERSON GOT TO
-- ---------------------------------------------------------------------

-- A tiny key/value store for local reading state, and the only table in this
-- file with no opinion about its own contents.
--
-- It exists for one value today -- when the person last reviewed the list --
-- so that "new" can mean "new since I looked" rather than "posted in the last
-- N days", which is a guess about a calendar rather than a fact about a
-- reader. A column on `candidate` would have done the same job and would have
-- needed a migration for the second such value.
--
-- THIS IS NOT THE EMPLOYER'S DATE. `job.posted_at` is what a board published
-- and `job.first_seen_at` is when this machine first held it; neither moves
-- when somebody reads a screen, and none of the three may be substituted for
-- another.
CREATE TABLE candidate_state (
    candidate_id   TEXT NOT NULL REFERENCES candidate(id),
    key            TEXT NOT NULL,
    value          TEXT NOT NULL,
    updated_at     TEXT NOT NULL,

    PRIMARY KEY (candidate_id, key)
);
