-- 0027_experience_requirement.sql
--
-- WHAT A POSTING ASKS FOR IN THE WAY OF PREVIOUS EXPERIENCE, where a query can
-- reach it. Additive: three columns on `job_match`, no table altered, no row
-- rewritten, no score touched.
--
-- THE FACT THIS RECORDS
-- ---------------------
-- The product could say how SENIOR a role reads. It could not say whether the
-- employer had asked for experience at all, and it could not tell three
-- different sentences apart:
--
--     "3+ years of experience required"      a wall
--     "3 years of experience preferred"      a wish
--     "No previous experience necessary"     an invitation
--
-- All three produced the same thing downstream: a requirement the candidate
-- either matched or did not. Somebody entering a profession, returning to work
-- or changing career then read every posting as a list of gaps, including the
-- postings that had explicitly said they would consider them. That is the
-- product manufacturing a refusal out of a sentence that was the opposite --
-- and it is the same shape of defect as reading silence as permission,
-- pointed the other way.
--
-- THREE COLUMNS BECAUSE THEY ARE THREE FACTS
-- -------------------------------------------
-- `experience_requirement` is HOW HARD the ask is, from the closed vocabulary
-- in `domain/enums.py::ExperienceRequirement`.
--
-- `experience_min_years` is the FIGURE, and only when the posting stated an
-- unambiguous minimum. It is nullable and 0 is a real value: 0 means the
-- posting said none is needed, NULL means no figure was read. Folding the two
-- into one column would make "we could not tell" and "they said none" the same
-- row, which is precisely the confusion this migration exists to end.
--
-- `entry_signals` is a pipe-fenced SET of the invitations the posting
-- extended: `|ENTRY_LEVEL|RECENT_GRADUATE|`. Fenced like `membership`,
-- `countries` and `regions`, so `|ENTRY_LEVEL|` cannot match a longer token,
-- and empty string rather than `||` for none -- both match nothing, which is
-- the correct answer to both "this posting extended none" and "this row has
-- not been rescored yet".
--
-- WHY COLUMNS AND NOT A SENTENCE IN `result_json`
-- ------------------------------------------------
-- Because a filter must be able to ask, and a filter may never LIKE over
-- `result_json`: that blob holds evidence quotes copied verbatim out of job
-- descriptions, so a posting whose body happened to contain the string
-- `ENTRY_LEVEL` would satisfy the filter using words the employer wrote.
-- Migration 0013 exists because a filter pointed at that blob once already,
-- and 0020 and 0022 followed the same rule. This is the fourth, and the rule
-- has not changed: OUR identifiers, in their own column, never prose.
--
-- NOTHING SCORES IT
-- -----------------
-- No scoring component reads any of the three, and no gate does. Discounting a
-- compatibility number because a posting asks for five years would blend two
-- of the three measurements ADR-0004 keeps apart -- and it would do it in the
-- direction that hides work from the people who most need to see it. These
-- columns exist so a PERSON can narrow their own list, which is a view
-- decision and theirs to make.
--
-- NULL MEANS "WRITTEN BEFORE THIS EXISTED"
-- -----------------------------------------
-- Nullable with no default, like 0020 and 0022 and for the same reason. A row
-- scored under `MATCH_SCHEMA_VERSION` 6 has no reading in it, and defaulting
-- `experience_requirement` to `NOT_STATED` would make a hundred thousand
-- historical rows assert a reading nobody computed. The version is bumped to 7
-- so an ordinary rescore refreshes those rows rather than somebody having to
-- remember `--force`.
--
-- Portability, per Appendix A: no SQLite date function (A3), enums as portable
-- TEXT (A11), additive ALTER only. SQLite adds a nullable column in constant
-- time and rewrites nothing.

ALTER TABLE job_match ADD COLUMN experience_requirement TEXT;
ALTER TABLE job_match ADD COLUMN experience_min_years INTEGER;
ALTER TABLE job_match ADD COLUMN entry_signals TEXT;

-- Filtering within one configuration version. The version leads because every
-- query in this product is scoped to it: a score computed under older
-- semantics is visibly stale rather than silently reinterpreted, and an index
-- that ignored the version would serve rows from several answers at once.
CREATE INDEX idx_job_match_experience
    ON job_match (config_id, config_version, experience_requirement);

-- The figure gets its own index because "at most two years" is a RANGE scan
-- and the vocabulary index above cannot serve one.
CREATE INDEX idx_job_match_experience_years
    ON job_match (config_id, config_version, experience_min_years);
