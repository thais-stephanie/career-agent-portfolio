# Resume Tailor v1: rollout plan

Verdict at handoff: **PARTIALLY IMPLEMENTED**. What is built is tested on
synthetic data; the full and browser suites have not run; four items need the
owner's decision.

## Built on this branch

* `resume_doc/style.py`, `resume_doc/budget.py`, `resume_doc/report.py` (new,
  read-only, nothing writes).
* `drafter.check` refuses AI wording that breaks a style rule (`STYLE`); prompt
  `resume-drafter-v2` states the rules.

## Decisions the owner needs to make

1. **Page-limit exception storage.** Proposed: a nullable
   `job_application.resume_page_limit_exception` (0/1) through a new migration,
   tested on temporary databases, written only by an explicit toggle in the job
   drawer. Alternative: a one-row-per-job table. Until decided, 2 pages always.
2. **Claim Limits and evidence context.** Proposed: a `claim_limit` table
   (claim key, kind `NEVER_WRITE` or `NOTE`, text) and a context on claims
   (`JOB`, `INDEPENDENT`, `LAB`, `SYNTHETIC`), confirmed by the person like any
   evidence. Feeds `limit_findings` (then a refusal for AI wording), the Master's
   skill grouping ("DevOps (independent lab)") and coverage.
3. **Part B template.** A new `Design.template` value breaks reading in a
   rolled-back build. Options: (a) ship it with a schema version bump and an
   upgrade function; (b) restyle `clean` (changes every existing resume's look);
   (c) keep the current templates. The measured numbers show the spec's
   typography fits Letter and A4 with one budget.
4. **Company description line, languages, working-hours line.** Each is a new
   content field (schema, editor, DOCX, checks) or, for working hours, a privacy
   choice: today availability is an eligibility fact and never printed.

Also the owner's call, trivial once decided: the export file name
(`First_Last_CV_Target_Role.pdf` vs today's `Full_Name_Target_Title_Resume.pdf`)
and the en dash in date ranges (today's DATES check warns on it).

## Integration steps (each its own PR, in order)

1. **This branch.** Before merge: run the full suite and the browser suite on a
   quiet machine (no collector running). Fix anything they find. No data
   migration is involved, so no backup is needed for this step.
2. **Report in the job panel.** Compute `report.build` for a tailored version
   (no storage; recomputed like Analyze) and show gaps, omitted evidence, risk
   flags and questions beside the preview, mapped to plain words in English and
   Portuguese (no codes on screen). Browser tests for the panel.
3. **Fit before export.** On export of a tailored version, measure with
   `pdf_pages`; when over the limit, offer the fit (hides, shown as a diff the
   person accepts) instead of cutting on its own; a `CONFLICT` is said in words.
4. **Decision 1 (storage) and the toggle**, then the 3-page budget reaches the UI.
5. **Decision 2 (Limits, context)**, then STYLE-like refusal for `NEVER_WRITE`
   limits in AI wording and labelled lab skills in new Masters (existing Masters
   unchanged; the change is reported, never applied).
6. **Decision 3 (template)**, if chosen.

## Rollback

Steps 1 to 3 add no stored data: reverting the commit is the rollback. Step 4
and 5 add migrations: one verified backup before running them on the real
databases, rehearsal on a temporary copy deleted after validation.

## Workable front

Nothing here touches collection, providers, migration 0061 or the Workable
research. No conflict found.
