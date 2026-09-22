-- 0025_intake_package_lifecycle.sql
--
-- Which reading of her documents is the one in force, and why the others
-- are not.
--
-- WHAT WENT WRONG WITHOUT THIS
-- ----------------------------
-- `intake_package.status` had two values, OPEN and DISCARDED, and nothing
-- said which OPEN package was the one being reviewed. On 2026-09-07 the
-- owner's database held two, sixty-nine seconds apart, built from the same
-- CV and the same LinkedIn export by two versions of the same parser:
--
--     07:23:13  334 claims  <- the first read
--     07:24:22  309 claims  <- the same documents, read better
--     07:24:30            the first was DISCARDED, by hand
--
-- It came out right, and it came out right because a person noticed. Nothing
-- in the schema would have stopped both staying open, and a review spread
-- across two packages of the same career is a review that cannot be finished:
-- answering a claim in one leaves its twin in the other unanswered forever.
--
-- FOUR STATES, ANSWERING TWO DIFFERENT QUESTIONS
-- -----------------------------------------------
--   ACTIVE       the reading in force. AT MOST ONE, and the store enforces it
--                inside the transaction that sets it.
--   SUPERSEDED   complete, valid, and not in force: a later package built
--                from the same documents took over. THE MACHINE'S DOING.
--   DISCARDED    she put it away. HER doing. Kept for exactly the reason
--                migration 0023 gives -- its REJECTED rows are what stop
--                those lines being proposed again.
--   INCOMPLETE   the import produced no claims at all. It can never become
--                ACTIVE and it can never supersede anything.
--
-- SUPERSEDED and DISCARDED are deliberately NOT one value. This codebase draws
-- that line everywhere it matters -- `include_user_hidden` has no tracking
-- exemption precisely because SHE hid it, while the automatic narrowings do
-- because the MACHINE did -- and a screen that says "you put this away" about
-- something the product retired on her behalf is lying about who decided.
--
-- WHY `superseded_by` IS A COLUMN AND NOT AN INFERENCE
-- ----------------------------------------------------
-- The interface has to answer "why is this one not in force", and "because a
-- newer one exists" is not the answer -- WHICH newer one is. Inferring it from
-- timestamps would guess, and would guess wrong the moment she selects an
-- older package on purpose, which this lifecycle explicitly permits.
--
-- NULL is meaningful here and the column is therefore nullable: nothing
-- superseded this package. That is not the `_json` rule from 0023, which
-- exists so a later JSONB conversion has something to convert.
--
-- NOTHING HERE CONFIRMS A CLAIM, AND NOTHING HERE DELETES ONE
-- -----------------------------------------------------------
-- Every statement below touches `intake_package` and no statement touches
-- `intake_claim`. Package status is PRODUCT MACHINERY -- which reading is on
-- screen -- and never a fact about the candidate. Superseding, discarding,
-- restoring and selecting all leave every answer she has given exactly where
-- she gave it, attached to the package she gave it in.
--
-- THE BACKFILL, AND WHY IT IS DETERMINISTIC
-- ------------------------------------------
-- OPEN becomes ACTIVE. Where a database holds more than one OPEN package --
-- which the old schema permitted and the owner's briefly did -- the NEWEST by
-- `created_at` wins and the rest become SUPERSEDED pointing at it. Newest
-- rather than largest: a package is not better for holding more claims, and
-- the 2026-09-07 pair is the proof. The smaller one was the better read.
--
-- `superseded_by` is written BEFORE any status moves, so the subquery that
-- picks the winner sees a stable population. Reversing those two statements
-- would let the winner change under the loop.
--
-- DISCARDED is left exactly as it is. Rewriting it to SUPERSEDED would be this
-- migration inventing a reason for something a person did.

ALTER TABLE intake_package ADD COLUMN superseded_by TEXT;

UPDATE intake_package
   SET superseded_by = (
       SELECT winner.id FROM intake_package winner
        WHERE winner.status = 'OPEN'
        ORDER BY winner.created_at DESC, winner.id DESC
        LIMIT 1)
 WHERE status = 'OPEN'
   AND id <> (
       SELECT winner.id FROM intake_package winner
        WHERE winner.status = 'OPEN'
        ORDER BY winner.created_at DESC, winner.id DESC
        LIMIT 1);

UPDATE intake_package
   SET status = 'SUPERSEDED'
 WHERE status = 'OPEN' AND superseded_by IS NOT NULL;

UPDATE intake_package
   SET status = 'INCOMPLETE'
 WHERE status = 'OPEN' AND claim_count = 0;

UPDATE intake_package
   SET status = 'ACTIVE'
 WHERE status = 'OPEN';

CREATE INDEX IF NOT EXISTS idx_intake_package_status ON intake_package (status);
