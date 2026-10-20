-- Migration 0059: what a deep reading was asked against, in two halves.
-- scope: profile
--
-- Additive: two nullable columns, nothing rewritten, nothing dropped.
--
-- `profile_digest` hashes the search intent, the preferred levels and the
-- confirmed evidence together, so a confirmed certificate made every reading
-- out of date, the work it describes included (all 823 of the owner's on
-- 2026-10-19). `intent_digest` names what the reading was asked about (the
-- intent and the levels) and `evidence_digest` what it was shown about the
-- person (sharing on or off, and the confirmed claims by key and revision).
-- `profile_digest` stays the identity of a reading and its unique key.
--
-- NULL means "not known": a row written before this migration whose halves
-- were not PROVEN by `deep_run.backfill_digests` (the evidence rebuilt as it
-- stood at the reading reproduces the stored `profile_digest`). A NULL row is
-- read as fully out of date whenever its profile digest is, never as "only
-- the evidence changed" (ADR-0035, addendum 5).
ALTER TABLE deep_review ADD COLUMN intent_digest TEXT;

ALTER TABLE deep_review ADD COLUMN evidence_digest TEXT;
