-- 0024_intake_conflict_resolution.sql
--
-- One decision about a role, rather than one decision per sentence about it.
--
-- WHY THIS TABLE EXISTS
-- ---------------------
-- Migration 0023 marks a claim CONFLICT when two of the candidate's own
-- documents cannot both be right about the same stretch of time at the same
-- employer. That marking is correct and it is deliberately broad: confirming
-- ANY claim about a disputed role writes the disputed dates onto a
-- `verified_claim`, so every one of them really does carry the thing in
-- dispute.
--
-- Measured on the owner's real documents: **32 of 309 claims in one group.**
-- The disagreement is a single fact -- did that role end, or is it ongoing --
-- and asking somebody to answer it thirty-two times is not a review. It is a
-- wall, and a wall is what people close the tab on.
--
-- So the disagreement gets answered ONCE, here, and the thirty-two claims
-- return to the ordinary queue carrying the period she chose.
--
-- WHAT IS STORED, AND WHAT DELIBERATELY IS NOT
-- --------------------------------------------
-- `chosen_claim_key` names WHICH CLAIM'S VERSION OF THE DATES she accepted.
-- Not the dates themselves: a period typed into this table would be a third
-- statement, belonging to neither document, and the whole contract rests on
-- every fact being traceable to a document that says it. Confirming a claim in
-- a resolved group reads the period off the chosen claim, whose `payload_json`
-- still holds the original wording the document used.
--
-- **Resolving confirms nothing.** It answers one question -- which document is
-- right about these dates -- and returns the group to UNREVIEWED. Every claim
-- in it still has to be confirmed, corrected or rejected on its own, because
-- agreeing about a date is not the same as standing behind a sentence.
--
-- WHY IT IS ITS OWN TABLE RATHER THAN A COLUMN ON `intake_claim`
-- ---------------------------------------------------------------
-- The resolution is a fact about the GROUP. Writing it onto every member would
-- store one decision N times and make "has this disagreement been settled"
-- a question you answer by hoping the N copies agree.
--
-- Reversible: `DELETE` the row and the group returns to CONFLICT. That is the
-- one place in this schema where a delete is right, because a resolution is a
-- statement about how to READ two documents rather than a claim about the
-- candidate, and unmaking it leaves nothing unsaid.

CREATE TABLE IF NOT EXISTS intake_conflict_resolution (
    package_id      TEXT NOT NULL REFERENCES intake_package (id) ON DELETE CASCADE,
    -- The group id `conflicts.reconcile` assigned, e.g. `EMPLOYMENT:acme`.
    conflict_group  TEXT NOT NULL,
    -- Whose version of the dates she accepted. A claim key from this package.
    chosen_claim_key TEXT NOT NULL,
    resolved_at     TEXT NOT NULL,
    PRIMARY KEY (package_id, conflict_group)
);
