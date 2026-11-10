# Resume Tailor v1: rollout plan

Branch `claude/resume-tailor-v1`. Not merged, not pushed. The owner reviews the
architecture decisions and remaining risks before any merge.

## Built on this branch

* `resume_doc/style.py`, `resume_doc/budget.py`, `resume_doc/report.py`.
* `drafter.check` refuses AI wording that breaks a style rule (`STYLE`); prompt
  `resume-drafter-v2` states the rules.
* Skill origin (`master.skill_origin`, `regroup_skills`): Master, Tailor and the
  "add this skill" suggestion place a lab's or a project's skill in a labelled
  group; the export review stops a lab or project skill shown as a job's
  (`OUTSIDE_A_JOB`, never acknowledged). A skill nothing places stays on the
  Master in "Other skills" and Analyze asks about it; a version for a job
  hides it, says so in the report, and never suggests it.
* The two-page gate: `export_revision` gives no file for a version for a job
  over two printed pages (any format); `POST .../fit` proposes the cuts on the
  real PDF, `POST .../fit/cuts` hides only those the person accepted (the
  copy before kept as a revision).
* Ad lines written to a model are dropped in `tailor.retrieve` (every consumer).
* The report is in the job panel (`GET .../job` carries `report`), worded in
  English and Portuguese.

## Owner decisions (2026-11-08) and what is left

| Decision | State |
|---|---|
| Lab skills never shown as a job's | Applied (see the design document). Standalone skill claims: kept on the Master (2026-11-09), left out of versions for a job. |
| Number grounding stays conservative | Applied: unchanged; false refusals and must-refuse cases pinned by tests; `grounding_v2` proposal in the design document. |
| Two pages default, third only per job later | Applied and enforced at export (2026-11-09); storage proposal for the third page (a new table, not a column) documented, not built. |
| Limits and evidence class | Proposal (`career_evidence_annotation`, versioned) documented, not built. |
| Report and approval | Applied: report beside the version; no new approval; before a file, the existing review and the measured page count. |
| Template, language, privacy | Proposal only; no `Design.template` value persisted; file name and en dash unchanged until chosen. |

## Integration steps (each its own PR, in order)

1. **This branch** (after the owner's review): suites green apart from the
   known flaky tests listed in the validation document. No stored data is
   added, so reverting the commits is the rollback. Behaviour the owner sees
   on the first day: a version for a job already over two pages no longer
   downloads until it is fitted (the proposal shows what to hide).
2. *(Done on this branch: the fit before export.)*
3. **Three-page storage** (`application_resume_option`, migration rehearsed on a
   temporary copy, one verified backup), then the per-job toggle.
4. **Evidence annotation** (`career_evidence_annotation`), then `NEVER_WRITE`
   refusals, `needs_check` questions and `setting` replacing the lab-word read.
5. **`grounding_v2`** behind a flag, shadow first, owner-reviewed.
6. **Template**, if chosen, with a schema version bump and an upgrade function.

## Rollback

Step 1 adds no table, column or stored field: revert the commits. A version
made while this branch ran holds ordinary skill groups ("Lab", "Independent
projects", "Other skills", a hidden "Origin not confirmed"), ordinary hidden
lines and `MOVE_SKILL` change records, and its fit left a `MANUAL_CHECKPOINT`
revision: `main` reads all of these as it reads any group, line, change and
revision. Steps 3 and 4 add
migrations: one verified backup before the real databases, rehearsal on a
temporary copy deleted after validation.

## Not touched

Collection, providers, Workable (observation off), Retrieval Wave 2, LifeOS,
the real databases and the install. No model was called.

## Resume Tailor v1.1 (branch `claude/resume-summary`, not merged)

On top of the composed summary (`3cd0f89`). Design in
`RESUME_TAILOR_V1_1_ADAPTIVE.md`. No table, column, stored field or schema
version is added: revert the commits to roll back.

What the owner sees on the first day:

* A new Master places academic and personal experiences under Projects.
  Existing Masters are never rewritten.
* A version of a person with no shown role puts Education and Projects
  first. This only happens when the Master keeps the default order.
* A composed summary has 2 to 4 sentences, at most 80 words, and repeats at
  most one bullet. "Short summary" is no longer reported.
* "No email" is reported only when there is no phone either.
* DOCX links are real hyperlinks. The export checks list `LINKS`.

Compatibility with `main`:

* A version made on this branch holds an ordinary `section_order` and a
  `REORDER_SECTIONS` change record. `main` shows the order and ignores the
  record's unknown op in the panel.
* Older versions and Masters load unchanged.

Waiting on owner decisions: Languages, a Volunteer section, the company
description, education from evidence, and project claims outside an
experience. Each one is a schema or product decision, listed in the design
note.
