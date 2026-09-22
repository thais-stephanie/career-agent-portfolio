-- 0026_hidden_reason.sql
--
-- WHY SHE HID IT, when she says. Additive: one nullable column beside
-- `hidden_at`, no table altered, no row rewritten, no score touched, and
-- nothing that reads it may change a preference.
--
-- WHAT THIS IS FOR
-- ----------------
-- The recommendation audit run on 2026-09-08 answered "are these jobs useful
-- to her" with numbers: of 19,469 scored postings, 6,503 fire at least one of
-- her core specialty signals, 137 of those have an employer who may hire her,
-- and 101 are at a level she accepts. 92 of her top 100 sit in the WEAK band.
--
-- Those are MY measurements of the corpus. They cannot tell the difference
-- between a posting that is wrong because it is in the wrong country and one
-- that is wrong because the work is not what she does -- and those two want
-- completely different corrections. Only she knows which, and she knows it
-- one posting at a time, at the moment she decides not to look at it again.
--
-- So the hide control asks, optionally, and the answer is stored beside the
-- hide it belongs to.
--
-- WHY A COLUMN AND NOT A TABLE
-- -----------------------------
-- One reason per hide, written at the same moment, deleted when the hide is
-- undone. It has no life of its own: a reason for a posting that is no longer
-- hidden is a note about a decision that was reversed, and keeping it would
-- invite a later query to treat "she once said wrong country about this" as a
-- signal. It is not one. NULL means she hid it without saying why, which must
-- stay an ordinary and unremarkable thing to do.
--
-- WHY IT MAY NEVER BECOME A RULE BY ITSELF
-- -----------------------------------------
-- A product that learned "she rejects Germany" from one hidden posting would
-- be inventing a candidate fact, which invariant 2 forbids and which
-- `config/ownership.py` places squarely with her. This column is EVIDENCE FOR
-- A CONVERSATION, never an input to one: nothing in `career_agent.match` may
-- read it, and a test asserts that.
--
-- If a pattern in these reasons is ever turned into a preference, it goes
-- through the same editor every other preference goes through -- shown as a
-- proposal, with the count of postings it would affect, and confirmed by her.
--
-- WHY A CLOSED VOCABULARY AND NOT FREE TEXT
-- ------------------------------------------
-- Free text cannot be counted, and counting is the entire point: "eleven of
-- the last twenty were the wrong seniority" is actionable and "eleven notes"
-- is not. The vocabulary lives in `domain/enums.py` next to every other closed
-- vocabulary this schema constrains, and the CHECK below is the second
-- spelling of it -- `tests/unit/test_enum_vocabularies.py` exists because
-- those two spellings drifting apart once cost a live call.
--
-- `notes` is already there for anything that does not fit, and it is hers to
-- write in whatever words she likes.
--
-- Portability, per Appendix A: additive ALTER only, no SQLite-specific
-- function, and a CHECK constraint every supported engine accepts. SQLite adds
-- a nullable column in constant time and rewrites nothing.

ALTER TABLE job_application ADD COLUMN hidden_reason TEXT
    CHECK (hidden_reason IS NULL OR hidden_reason IN (
        'WRONG_PLACE',
        'WRONG_LEVEL',
        'WRONG_WORK',
        'TITLE_MISLEADING',
        'PAY',
        'EMPLOYER',
        'STALE',
        'OTHER'
    ));
