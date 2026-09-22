-- 0020_employment_context_facet.sql
--
-- Three readings the matcher already makes, lifted where a query can reach
-- them. Additive: two columns on `job_match`, no table altered, no row
-- rewritten.
--
-- WHY A COLUMN AND NOT A LIKE OVER `result_json`
-- -----------------------------------------------
-- `domestic.context` has been computed on every posting since V1.2 and has
-- been unreachable from SQL the entire time. The digest could not offer a
-- "probably United States employment" section, the filter rail could not offer
-- the control, and the only place the reading appeared was a sentence inside
-- one drawer.
--
-- The tempting shortcut is a LIKE over `result_json`, and it is exactly the
-- defect migration 0013 exists to prevent. That blob holds EVIDENCE QUOTES
-- copied verbatim out of job descriptions, so a posting whose body happened to
-- contain the string `LIKELY_US_DOMESTIC` -- or, more realistically, any
-- fragment a future predicate matched -- would satisfy the filter using words
-- the employer wrote. Third-party text must never steer a deterministic
-- filter. `membership` was created for that reason and these columns follow
-- the same rule: OUR identifiers, in their own column, never prose.
--
-- WHY NOT INSIDE `membership`
-- ----------------------------
-- `membership` answers set questions -- did this signal fire, was this
-- confidence item awarded -- and its pipe-fenced LIKE has no index behind it.
-- These two are SINGLE-VALUED per posting, exactly like `work_model` and
-- `employment_type` beside them, and a single-valued fact belongs in a column
-- that can be indexed and grouped rather than in a string that must be
-- scanned. The eleven single-valued facets already total with `count()`; these
-- join them.
--
-- THIS IS NOT AN ELIGIBILITY COLUMN, AND THE NAMES SAY SO
-- -------------------------------------------------------
-- `employment_context` holds `LIKELY_US_DOMESTIC`, `INTERNATIONAL_STATED` or
-- `UNRESOLVED`. None of the three is a verdict about whether the candidate may
-- take the job. A posting that offers a 401(k) and never mentions hiring
-- anywhere else is probably structured as United States domestic employment;
-- that is worth telling somebody abroad, and it is NOT the employer having
-- said no. `eligibility_status` remains the only column that records a
-- refusal, and only from stated text.
--
-- The two are deliberately in separate columns so that no query can produce
-- one by asking for the other, and `tests/integration/test_employment_context.py`
-- asserts the populations are not the same set.
--
-- `contract_regime` is the Brazilian half of the same question: CLT, PJ or
-- UNRESOLVED, as read by `match/employment.py`. It is here because the
-- Brazilian coverage work needs to be able to ask "how many of these state
-- CLT" without deserialising eighteen thousand JSON blobs to find out.
--
-- NULL MEANS "WRITTEN BEFORE THIS EXISTED"
-- -----------------------------------------
-- Both columns are nullable with no default, and that is the honest shape. A
-- row scored before this migration has no reading in it, and defaulting it to
-- `UNRESOLVED` would make forty thousand historical rows claim a conclusion
-- nobody computed. A rescore fills them; until then a NULL is visibly absent,
-- which is what `integrity` reports and what the filter treats as unknown.
--
-- Portability, per Appendix A: no SQLite date function (A3), enums as portable
-- CHECK (A11), additive ALTER only. SQLite adds a column in constant time and
-- rewrites nothing.

ALTER TABLE job_match ADD COLUMN employment_context TEXT;

ALTER TABLE job_match ADD COLUMN contract_regime TEXT;

-- Filtering the corpus by context, within one configuration version. The
-- version leads because every query in this product is scoped to it: a score
-- computed under older semantics is visibly stale rather than silently
-- reinterpreted, and an index that ignored the version would serve rows from
-- three answers at once.
CREATE INDEX idx_job_match_employment_context
    ON job_match (config_id, config_version, employment_context);

CREATE INDEX idx_job_match_contract_regime
    ON job_match (config_id, config_version, contract_regime);
