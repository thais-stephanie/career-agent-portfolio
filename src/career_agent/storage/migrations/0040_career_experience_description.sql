-- Migration 0040: a line of description on each professional experience.
--
-- The Career Profile reads an experience as a person would write it: a role, a
-- company, a period, a sentence about what the job was, then its highlights.
-- The highlights are confirmed statements, linked through
-- `career_evidence_link`, and keep every revision and source line. The
-- sentence has had nowhere to live.
--
-- It is PROFILE PROSE, not a claim. Nothing that prepares an application
-- reads it, it is never confirmed or quoted, and it changes no score. NULL is
-- the ordinary state and every existing row reads exactly as it did.
--
-- Experience dates also accept a year alone ("2015") from now on. That is a
-- validation change in `domain/experience.py`, not a column: the column was
-- always text.

ALTER TABLE career_experience ADD COLUMN description TEXT;
