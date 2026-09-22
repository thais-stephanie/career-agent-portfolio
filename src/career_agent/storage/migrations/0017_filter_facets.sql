-- The columns the twelve missing filters query.
--
-- WHY COLUMNS RATHER THAN A LIKE OVER `result_json`
-- -------------------------------------------------
-- Every one of these facts already exists inside the stored `MatchResult`:
-- `posting_facts` carries salary, employment type and work model, and
-- `places.resolve_place` derives countries and regions from `location_raw`.
-- They are lifted out into columns for the same two reasons migration 0011
-- gives for the scalar columns it added beside `result_json`:
--
--   1. A blob cannot be compared. `min_salary` is an inequality, and no LIKE
--      over serialised JSON expresses ">= 120000".
--   2. `result_json` holds EVIDENCE -- sentences copied verbatim out of job
--      descriptions. Migration 0013 moved the membership index out of it after
--      a posting containing `|award:compensation_stated|` satisfied the salary
--      filter with no salary in it. Pointing eleven more filters back at that
--      blob would re-open exactly that hole. These columns, like `membership`,
--      contain only values this system produced.
--
-- WHY ON `job_match` AND NOT ON `job`
-- ----------------------------------
-- Because they are the matcher's READING of the posting, not the posting. The
-- salary here is the salary the compensation component was scored from; the
-- work model is the one `_work_model` derived under this configuration version.
-- Storing them on `job` would let the card show one number and the score
-- explain a different one, which is the drift `posting_facts` exists to stop.
-- A re-score under a new `config_version` writes a new row, so an old reading
-- stays attached to the score it produced.
--
-- COUNTRIES AND REGIONS ARE NOT ELIGIBILITY
-- ----------------------------------------
-- `countries` says what the BOARD PRINTED, resolved through `config/places.yaml`.
-- It is not `hiring_scope` and must never be read as one -- CLAUDE.md invariant
-- 3. A posting in San Francisco is not a posting that will hire in Brazil, and
-- `eligibility_status` continues to answer that question alone.
--
-- Pipe-fenced, like `membership`, and for the same reason: `|BR|` cannot match
-- a hypothetical `BRX`, which a bare substring search would.
--
-- ANNUALISED SALARY, AND WHAT IT DELIBERATELY DOES NOT DO
-- ------------------------------------------------------
-- `salary_annual_min/max` multiply the stated band by its period -- hour x2080,
-- day x260, week x52, month x12 -- so "at least 120,000 a year" can be asked of
-- a posting quoting a monthly rate. It does NOT convert between currencies.
-- There is no offline FX rate that is true on the day a posting was collected,
-- and a wrong conversion is worse than no filter: it would silently reorder
-- every salary comparison. `salary_currency` is therefore required alongside
-- `min_salary`, and the API refuses the pair without it.
--
-- Backfilled to NULL / '' rather than recomputed. A row with no resolved
-- country matches no country filter, which is the correct reading of "we have
-- not looked at this row yet"; `career-agent rescore --force` fills them, and
-- the config-digest drift check already reports when a re-score is due.
--
-- Portability: additive ALTERs with constant defaults (A13-compatible), no
-- table rewrite, no existing migration touched.

ALTER TABLE job_match ADD COLUMN work_model        TEXT;
ALTER TABLE job_match ADD COLUMN employment_type   TEXT;
ALTER TABLE job_match ADD COLUMN salary_min        REAL;
ALTER TABLE job_match ADD COLUMN salary_max        REAL;
ALTER TABLE job_match ADD COLUMN salary_currency   TEXT;
ALTER TABLE job_match ADD COLUMN salary_period     TEXT;
ALTER TABLE job_match ADD COLUMN salary_annual_min REAL;
ALTER TABLE job_match ADD COLUMN salary_annual_max REAL;
ALTER TABLE job_match ADD COLUMN countries         TEXT NOT NULL DEFAULT '';
ALTER TABLE job_match ADD COLUMN regions           TEXT NOT NULL DEFAULT '';

-- The three that are equality filters over a small vocabulary, so a B-tree
-- earns its keep. `countries` and `regions` are substring matches and no index
-- serves those -- the same accepted cost `membership` documents.
CREATE INDEX idx_job_match_work_model  ON job_match(work_model);
CREATE INDEX idx_job_match_employment  ON job_match(employment_type);
CREATE INDEX idx_job_match_salary_year ON job_match(salary_annual_max);
