# Resume Tailor v1: rollout plan

Branch `claude/resume-tailor-v1`. Not merged, not pushed. The owner reviews the
architecture decisions and remaining risks before any merge.

## Built on this branch

* `resume_doc/style.py`, `resume_doc/budget.py`, `resume_doc/report.py`.
* `drafter.check` refuses AI wording that breaks a style rule (`STYLE`); prompt
  `resume-drafter-v2` states the rules.
* Skill origin (`master.skill_origin`, `regroup_skills`): Master, Tailor and the
  "add this skill" suggestion place a lab's or a project's skill in a labelled
  group and hide one nothing places; the export review stops a lab or project
  skill shown as a job's (`OUTSIDE_A_JOB`, never acknowledged).
* Ad lines written to a model are dropped in `tailor.retrieve` (every consumer).
* The report is in the job panel (`GET .../job` carries `report`), worded in
  English and Portuguese.

## Owner decisions (2026-11-08) and what is left

| Decision | State |
|---|---|
| Lab skills never shown as a job's | Applied (see the design document). Review the effect on standalone skill claims (hidden in new Masters and versions). |
| Number grounding stays conservative | Applied: unchanged; false refusals and must-refuse cases pinned by tests; `grounding_v2` proposal in the design document. |
| Two pages default, third only per job later | Applied: `page_limit(False)` everywhere; storage proposal (a new table, not a column) documented, not built. |
| Limits and evidence class | Proposal (`career_evidence_annotation`, versioned) documented, not built. |
| Report and approval | Applied: report beside the version; no new approval; the existing review is the only export gate. |
| Template, language, privacy | Proposal only; no `Design.template` value persisted; file name and en dash unchanged until chosen. |

## Integration steps (each its own PR, in order)

1. **This branch** (after the owner's review): suites green apart from the
   three known flaky tests listed in the validation document. No stored data
   is added, so reverting the commits is the rollback.
2. **Fit before export.** On export of a tailored version, measure with
   `pdf_pages`; when over the limit, offer the fit (hides, shown as a diff the
   person accepts) instead of cutting on its own; a `CONFLICT` is said in words.
3. **Three-page storage** (`application_resume_option`, migration rehearsed on a
   temporary copy, one verified backup), then the per-job toggle.
4. **Evidence annotation** (`career_evidence_annotation`), then `NEVER_WRITE`
   refusals, `needs_check` questions and `setting` replacing the lab-word read.
5. **`grounding_v2`** behind a flag, shadow first, owner-reviewed.
6. **Template**, if chosen, with a schema version bump and an upgrade function.

## Rollback

Step 1 adds no table, column or stored field: revert the commits. A version
made while this branch ran holds ordinary skill groups ("Lab", "Independent
projects", a hidden "Origin not confirmed") and `MOVE_SKILL` change records,
which `main` reads as it reads any group and any change. Steps 3 and 4 add
migrations: one verified backup before the real databases, rehearsal on a
temporary copy deleted after validation.

## Not touched

Collection, providers, Workable (observation off), Retrieval Wave 2, LifeOS,
the real databases and the install. No model was called.
