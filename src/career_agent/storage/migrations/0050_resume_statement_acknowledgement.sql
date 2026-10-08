-- Migration 0050: statements a person acknowledged as their own before an export.
-- scope: profile
--
-- Additive only. Before a resume leaves this computer, every line its
-- evidence does not hold as worded, and every line, skill or entry written
-- or imported by the person, is shown for a decision: edit it, put back the
-- evidence's wording, or say "this is my own statement". The last is
-- recorded HERE, apart from the document: the line keeps its origin, its
-- evidence ids and its history, and nothing about it is called supported.
--
-- `statement_sha256` names the statement as acknowledged: its words, folded
-- (case, accents and spacing do not count), and what it rests on (origin and
-- evidence ids). Any change of a word, a number or the evidence it cites
-- leaves it unacknowledged. It is the person's statement wherever it
-- appears in this profile, so an unchanged line is not asked about again on
-- every export or in every version.
--
-- A row is written once and never updated except to withdraw it
-- (`withdrawn_at`); a new acknowledgement after a withdrawal is a new row.
CREATE TABLE resume_statement_acknowledgement (
    id                TEXT PRIMARY KEY,
    statement_sha256  TEXT NOT NULL,
    statement_text    TEXT NOT NULL,
    document_id       TEXT NOT NULL REFERENCES resume_document (id),
    item_ref          TEXT NOT NULL,
    acknowledged_at   TEXT NOT NULL,
    withdrawn_at      TEXT
);

CREATE INDEX resume_statement_acknowledgement_statement
    ON resume_statement_acknowledgement (statement_sha256);
