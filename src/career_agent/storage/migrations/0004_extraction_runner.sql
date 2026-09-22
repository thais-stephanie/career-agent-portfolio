-- M2: how an extraction was executed, as distinct from who produced it
--
-- A separate migration rather than an edit to 0003. 0003 is committed, and a
-- migration that has been released is a fact about every database that ran it;
-- editing one in place makes two databases claiming the same version hold
-- different schemas, which is the kind of drift that surfaces months later as
-- an unexplainable error. The requirement genuinely arrived afterwards, and
-- this is what migrations are for.
--
-- WHY `runner` IS NOT `provider`
-- -----------------------------
-- `provider` and `model` say who answered. `runner` says how the answer was
-- obtained, and the two are independent: the same model identifier can be
-- reached through a paid API, replayed from a recorded fixture, or -- during
-- development -- produced by an assisting agent working from the exact
-- production prompt.
--
-- The distinction has to be a column rather than a convention because the cost
-- report depends on it. Three buckets must never blend:
--
--   COWORK_ASSISTED   development extraction, zero paid calls
--   REPLAY            recorded fixture, zero paid calls
--   PRODUCTION_API    a real, billable vendor call
--
-- A development fingerprint that could be mistaken for a benchmark result
-- would let an accuracy claim be made about a model that never ran, which is
-- the specific failure this column exists to make impossible.
--
-- UNRECORDED is the default because a row whose runner nobody set must not
-- default into the bucket that means "we paid for this". Every writer sets the
-- value explicitly; the default exists only because SQLite requires a non-null
-- one when adding a NOT NULL column.

ALTER TABLE llm_call ADD COLUMN runner TEXT NOT NULL DEFAULT 'UNRECORDED'
    CHECK (runner IN ('PRODUCTION_API', 'REPLAY', 'COWORK_ASSISTED', 'FAKE', 'UNRECORDED'));

CREATE INDEX idx_llm_call_runner ON llm_call(runner);
