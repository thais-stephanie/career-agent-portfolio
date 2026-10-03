-- Migration 0048: one current MASTER resume per profile.
-- scope: profile
--
-- Imported and tailored documents may be many; the canonical Master is one.
-- An archived Master does not count, so it can be replaced on purpose.

CREATE UNIQUE INDEX resume_document_one_master
    ON resume_document (kind) WHERE kind = 'MASTER' AND archived_at IS NULL;
