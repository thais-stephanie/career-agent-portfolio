# Resume Tailor v1: design

Principle: adapt the existing Resume Workspace V2 contract to the spec, never a
parallel system and never a weaker one. "The LLM observes; code decides" holds:
everything the spec asks the model to self-check is computed by code.

## What is kept, and why the spec's model contract is not adopted

The spec asks one model call to return a whole resume plus a report, then asks
code to reject bad drafts and send them back. Tailor V2 already does better:

* the resume is built by code from the Master and confirmed evidence; a model
  may only propose rewrites of four kinds of text block (`drafter.Proposal`);
* every proposal is checked by `tailor.grounding` (numbers in context,
  qualifiers, authorship, seniority, scale), the EMPLOYER rule and evidence
  that is confirmed now, before the person sees it;
* identity, titles, employers, dates, education and certifications are out of
  the model's reach by construction.

So the spec's `resume` object is not a model output here; it is what
`ResumeDocument` already is. The spec's `report` object is computed
(`resume_doc/report.py`), and its self-check list is code (`style.py`,
`budget.py`, existing grounding and review).

## New modules (no schema change, no migration)

| Module | What it does |
|---|---|
| `resume_doc/style.py` | `findings(text, literals)`: em dash, en dash outside a range, banned words and phrases (with their forms), contrastive constructions, exclamation, question, emoji, rating adjectives. `literals` (official names) are blanked before words are read. `limit_findings(text, phrases)`: a claim limit's forbidden phrase, as whole words. |
| `resume_doc/budget.py` | `page_limit(exception: bool)` is 2 or 3 and accepts only a real bool. `WORD_BUDGET`, `word_count` (visible words from the renderer's own blocks), `estimate_pages`, `pdf_pages` (real print), and `fit(doc, supports, limit, year, measure, max_rounds)`. |
| `resume_doc/report.py` | `build(conn, doc, ad_text, page_limit, fit, limits)`: the spec's report from Tailor V2's own requirements and retrieval. |

One existing behaviour changes: `drafter.check` adds `STYLE` (fail-closed) for
proposed AI wording, exempting the tools it cites, the role's employer and
title and the job's company; the drafter prompt states the style rules and is
`resume-drafter-v2`. The UI already words any refusal as "couldn't verify this
wording", so no interface text changes.

## Report contract (computed)

```
version: resume-tailor-report-v1
job_summary: {title, company, asks: {must, nice, unstated}, eligibility}
coverage[]: {requirement_id, requirement (quote), type: must|nice|unstated,
             match: strong|partial|none, evidence_ids[], only_outside_a_job}
gaps[]: {requirement_id, requirement, type}
eligibility[]: {requirement_id, requirement}
omitted_evidence[]: {evidence_id, text, reason: ANSWERS_AN_ASK_NOT_SHOWN |
                      HIDDEN_FOR_PAGE_LIMIT | SKILL_ORIGIN_UNKNOWN}
risk_flags[]: {code, ref, text, detail?, origin?, evidence_id?}
questions_for_user[]: {code: CONFIRM_BEFORE_USE, claim_key, text, requirement_ids}
word_count, page_limit, word_budget, fit: {status, pages, rounds, cuts, protected, notes}
```

Deviations from the spec, each deliberate:

* `type` adds `unstated`: the ad did not say; absence is never "must".
* `job_summary` is structured counts, not prose a model wrote; the UI words it.
* `eligibility` is its own list (place, papers, schedule, required language),
  as everywhere in Career Agent; it is never coverage or a gap.
* Codes, never sentences, so the interface maps them to plain words in the
  person's language (the UI text rule).

Risk flag codes: `STYLE_<code>`, `NO_CONFIRMED_EVIDENCE`, `EVIDENCE_CHANGED_SINCE`,
`EVIDENCE_NOT_CONFIRMED`, `CLAIM_LIMIT`, `SKILL_ONLY_OUTSIDE_A_JOB`,
`SKILL_ORIGIN_UNKNOWN`, `AD_TEXT_ADDRESSED_TO_A_MODEL`, `PAGE_LIMIT_CONFLICT`.
`text` is the words the person reads for the flag (the line, the skill or the
ad's quote), so the panel never shows an id.

## Where each truth rule is enforced

| Spec rule | Enforced by | Strength |
|---|---|---|
| Only evidence or master facts | Strategy uses confirmed claims verbatim; `unconfirmed_lines` on every write and export | Refusal |
| Limits ("never write X") | `style.limit_findings`, report `CLAIM_LIMIT` | Risk flag (data source missing) |
| Numbers and qualifiers exact | `grounding` amounts | Refusal (AI) |
| No metric from a description | `grounding` NUMBERS, CLAIMS | Refusal (AI) |
| No merging or moving results | EMPLOYER rule, amount from its own sentence | Refusal (AI) |
| Exact authorship | `_responsibility` (RESPONSIBILITY, PARTICIPATION, AGENT) | Refusal (AI) |
| Lab apart from jobs | EMPLOYER rule for lines; `skill_origin` groups for skills; export review `OUTSIDE_A_JOB` | Refusal for lines; placement and an export stop for skills |
| Needs verification | PENDING never usable; `questions_for_user` | Refusal + question |
| Gaps never filled | Strategy adds only confirmed lines; `gaps` | By construction |
| Tenure never recomputed | Years asks at most "partly" | By construction |
| Job text is data | Snapshot quotes only; drafter prompt marks it untrusted; `retrieve` drops lines written to a model | By construction |
| Style | `style.findings` | Refusal (AI), flag (other lines) |

Verbatim and typed lines are never refused for style: a confirmed statement is
the person's own words, and a typed line is theirs to keep. They are flagged so
the person can change them before export.

## Page limit and fitting

* **Stored where?** The spec wants the flag with the application. Today the
  only per-application rows are `job_application` (status, dates, notes) and
  `application_resume` (which version was sent). `tailoring_run.options_json`
  is per run and must not be the source of truth (a second run would forget it).
  The right home is a new nullable column, for example
  `job_application.resume_page_limit_exception INTEGER CHECK (IN (0,1))`, or a
  small `application_resume_option` table keyed by job. **NEEDS DESIGN DECISION**:
  no migration was written. Until then `page_limit(False)` (2 pages) is the only
  limit any code path can reach.
* **The model never raises it.** `page_limit` refuses anything that is not a
  bool, the drafter's contract forbids extra fields (`page_limit` in an answer
  fails parsing, tested), and nothing reads a limit from model output.
* **Measure after render.** `fit` measures with a callable: `estimate_pages`
  (visible words / 500) for planning and tests, `pdf_pages` (print + pypdf) for
  the real check before export. At most `MAX_ROUNDS` (4) measurements.
* **Cut order (hide, never delete):** certifications beyond the top 5 (ones that
  answer an ask first); projects not tied to a required ask; lines of roles that
  ended more than 6 years before `year` (oldest role first, weakest first);
  skill groups naming nothing the ad asks; the weakest remaining lines (lines
  that answer nothing first, then by support strength). Company description
  lines are skipped (the field does not exist).
* **Never cut:** identity, headline, summary, the strongest shown line of each
  required ask, a role's last visible line.
* **Conflict:** when the protected content still measures over the limit,
  `status = CONFLICT` with the protected refs and a note; the report adds
  `PAGE_LIMIT_CONFLICT`. Proof is never cut to meet a length.
* **Three pages:** a note when the third page is less than half full.

## Part B renderer: measured, not built

Adding a template ("ats", say) means a new value in `Design.template`, a
`Literal` in `ResumeDocument` schema 1.0. A document saved with it could not be
read by a build rolled back to `main` (`upgrade_resume_document` validates
strictly), and the owner keeps rollback backups as a working practice. That is a
contract decision, so it is not made here. Margins are one value
(`Page.margins_mm`); the spec's 0.5 in top/bottom and 0.55 in left/right can be
reached with `margins_mm = 12.7` plus 1.27 mm inline padding, with no model change.

Measured with the spec's CSS injected into the existing print HTML (synthetic
document of five roles and 30-word bullets, headless Edge on Windows, one
print per page size):

| Page | Text width | Text height | Lines at 9.5 pt x 1.24 | Words on a full first page | Fonts in the PDF |
|---|---|---|---|---|---|
| Letter | 187.96 mm | 254.0 mm | about 61 | 553 | ArialMT, Arial-BoldMT, embedded subsets |
| A4 | 182.06 mm | 271.6 mm | about 65 | 577 | same |

A4 is 3% narrower and 7% taller and holds about 4% more. The spec's 500 words
per page (1,000 in 2) fits both sizes with margin, so one budget serves both.
Liberation Sans is not installed on Windows, so Arial is what prints; the font
stack is right as written. The gradient divider needs print backgrounds, which
the stylesheet already sets (`-webkit-print-color-adjust: exact`).

## Incompatibilities with the attachment

1. The model does not write the resume (only typed patches); the spec's
   `resume` JSON is the existing `ResumeDocument`.
2. Over-limit drafts are cut by code (hidden, in the spec's order), never sent
   back to a model.
3. Lines the person wrote may stand without evidence (reviewed and
   acknowledged before export); the spec would reject them.
4. Style problems in confirmed or typed wording are flagged, not refused.
5. Coverage `type` has a third value, `unstated`; `job_summary` is counts.
6. Date ranges keep the ASCII hyphen (the DATES export check), not the en dash.
7. The file name stays `Full_Name_Target_Title_Resume.pdf` until the owner
   chooses.
8. No time zone or working-hours line, company description line or languages
   section yet (no typed place; decisions listed in the rollout plan).

## Owner decisions of 2026-11-08 and how they are applied

### Lab skills (approved: never shown as a job's skill)

No stored field says "lab": a claim has a type, a text, tools and an
optional experience. What the data does say, reliably, is whether a confirmed
claim sits in a job (`experience_id`). So one function, `master.skill_origin`,
reads a skill's confirmed evidence:

| Origin | When | Where the skill goes |
|---|---|---|
| `JOB` | at least one cited claim is in a (non-archived) job | the plain skills group, as before |
| `LAB` | no claim in a job, and a claim's own confirmed words say lab ("home lab", "laboratório") | a group named "Lab" / "Laboratório" |
| `PROJECT` | no claim in a job, and a cited claim is a PROJECT | "Independent projects" / "Projetos independentes" |
| `UNKNOWN` | nothing says where (a standalone skill or tool claim, or evidence retired since) | in the Master: a shown group "Other skills" / "Outras competências", apart from a job's, and Analyze asks where it comes from (`SKILL_ORIGIN_UNKNOWN`). In a version for a job: a **hidden** group "Origin not confirmed", kept in the document, listed in the report as left out |

The lab word is read from the person's own confirmed text (a `ponytail:`
comment marks it): it only decides between the two labelled groups, never
whether a skill counts as a job's. Applied in three places that make skills:
the Master builder (new Masters), Tailor's draft (every new version, also from
an older Master that still has the chip in "Skills") and the "add this skill"
suggestion. Each move is a recorded `MOVE_SKILL` change, shown in the job
panel under "Placed apart from your jobs". Nothing is deleted.

For documents that already exist or that the person edits, the existing
statement review (the one gate before export) lists a `LAB` or `PROJECT`
skill shown in a group whose name does not say so as `OUTSIDE_A_JOB`. This is
the one review item that cannot be acknowledged: the person moves it, renames
the group so it says where the skill comes from, or hides it. An `UNKNOWN`
skill in an old document is not proof of a lab, so it does not stop the
export; the report flags it (`SKILL_ORIGIN_UNKNOWN`).

Owner decision of 2026-11-09: the Master keeps what the person entered and
confirmed. A skill nothing places stays on the Master's page in its own group
("Other skills"), never in the job skills group, and Analyze lists it as a
warning to classify (`SKILL_ORIGIN_UNKNOWN`, with the skill's name). It is
never read as a job's: the absence of a lab word never makes a skill a job's
(only a claim in a job does). A version for a job leaves it out (hidden, listed
in the report with its name), and "add this skill" never offers it for a job.
An older Master that holds it in "Skills" is not changed: Analyze asks about it
the same way, and versions made from it leave it out.

### Number grounding (approved: stay conservative; proposal only)

Production keeps `tailor.grounding` unchanged. Tests now pin both sides
(`test_resume_tailor_v1_completion.py`): three faithful rewrites that are
refused today (reordered words, passive voice, "Cut" for "Reduced") and seven
that must stay refused (changed number, invented metric, changed year, changed
tool, added leadership, lost participation, lost "about").

Proposal for a later, separately validated change (`grounding_v2`, behind a
flag, shadow first):

1. Parse each amount into a frame: number, unit, counted phrase head, the
   verb's direction class (reduce/cut/shorten/lower = DOWN; grow/raise = UP),
   its subject (the person, a team, "we"), qualifiers (about, up to, together
   with, helped), period and the named tools of the sentence.
2. Accept AI wording only when its frame equals the evidence frame on every
   field: same numbers and units, same counted head, same direction class,
   same subject and participation, the same qualifiers, the same period and
   the same tools; and its sentence cites one claim (no transfer between
   projects or roles; the EMPLOYER rule still applies).
3. Anything the parser cannot read stays on today's rule (fail closed).
4. Validation before turning it on: the current 50 frozen pairs plus the
   synthetic false-refusal set must pass with zero false acceptances on the
   must-refuse set; one shadow week logging both readers' verdicts on the
   person's own AI proposals, reviewed by the owner.

### Two or three pages (approved: 2 by default; no column now)

Proposed storage, not built:

* **Where:** a new table `application_resume_option (job_id TEXT PRIMARY KEY
  REFERENCES job(id), page_limit INTEGER NOT NULL CHECK (page_limit IN (2,3)),
  decided_at TEXT NOT NULL)`, one row only when the person chose 3 pages for
  that job. A table (not a `job_application` column) keeps `job_application`
  untouched and means "no row" is the default for every existing job.
* **Link to the application:** keyed by job, read when tailoring or exporting a
  version whose `target.job_id` is that job. A pasted ad (no job id) is always 2.
* **Old documents:** nothing in `ResumeDocument` changes; a version made before
  the table exists reads as 2 pages.
* **Migration:** additive (`CREATE TABLE`), rehearsed on a temporary copy;
  one verified backup before the real databases.
* **Rollback:** an older build ignores the table; dropping it loses only the
  choices made since.
* **UI:** a checkbox in the job's resume area, "Allow a third page for this
  job", off by default, explained in plain words; the export says which limit
  it held the file to. The model can never set it (`budget.page_limit` takes a
  bool only; drafter answers with a `page_limit` field fail parsing).

### Two-page gate (connected 2026-11-09, no migration)

Where it sits: in `export.export_revision`, the one function every download
goes through (the route `POST .../exports`; no other code makes a file). After
the statement review has passed and the file is made, a version for a job
(`DocumentKind.TAILORED`, which includes manual versions for a job) is
measured: the PDF's own page count, or for DOCX and JSON the PDF the same
print HTML makes. Over `page_limit(False)` = 2 raises `PageLimitExceeded`:
no file is kept, no revision, no export row (HTTP 409 `over_page_limit` with
the page count). The Master and other resumes are not held to two pages.

The person's side, with no second approval:

1. The preview shows "N pages: a version for a job must fit on two" as a
   warning; clicking it, or a refused download, opens the proposal.
2. `POST .../fit` (`budget.propose`) runs `budget.fit` on the saved copy with
   the real PDF as the measure (at most `MAX_ROUNDS` = 4 prints), against the
   version's own ad. It changes nothing and returns every cut with the line's
   text, its role, and the reason (shown in words: "A line from a role that
   ended more than six years ago", and so on), plus the pages before and after.
3. "Hide these N items" sends exactly those refs, the hash of the copy the
   person saw and the proposal's seal to `POST .../fit/cuts`
   (`budget.apply_cuts`). The seal (HMAC-SHA256, key in the running
   process's memory) covers the profile's database, the document, its newest
   revision, that hash and the exact list of cuts in order, so the server
   applies the proposal whole or nothing: one ref more, one fewer, a repeat,
   another order, another revision, document or profile is refused. A
   `CONFLICT` has no seal. The server also accepts a ref only when the spec's cut order allows it on that copy now
   (never the strongest line of a required ask, never a role's last line, no
   duplicates, nothing unknown); a changed copy is refused (`StaleDocument`).
   The copy before is first kept as a `MANUAL_CHECKPOINT` revision (the reason
   Analyze already uses for its own milestones; a new reason would need a
   migration), so it can be restored; then the hides are saved as the working
   copy. Nothing is deleted, and the Master is not touched.
4. The download is measured again; the export makes the `EXPORTED` revision.
   "Edit it myself" closes the proposal and hides nothing.
5. `CONFLICT` (what proves the required asks does not fit even with every
   allowed cut): the proposal says so, lists the protected lines, and offers
   only editing by hand.

Unchanged: the statement review and its acknowledgements (an acknowledgement
is for the words as saved; an edit asks again), `ats` PAGES (preview vs PDF).
Files exported before this gate stay downloadable as they were; they are
history, not a new export.

### Limits and evidence class (proposal, versioned, not built)

`career_evidence_annotation` (schema `evidence-annotation-v1`), one row per
claim key and revision, written only by the person in Career Evidence:

```
claim_key, claim_revision      -- the confirmed revision it annotates
setting: JOB | INDEPENDENT_PROJECT | LAB | COURSE | SYNTHETIC | UNKNOWN
limits: [{kind: NEVER_WRITE | NOTE, text}]   -- NEVER_WRITE is checkable
needs_check: bool                -- "verify before use": the claim is not
                                 -- usable until the person clears it
annotated_at, schema_version
```

* **Provenance:** an annotation cites the claim revision; a newer revision of
  the claim makes it stale (asked again), as acknowledgements already do.
* **No conflict with existing data:** nothing in `verified_claim` changes;
  claims without an annotation read as today (`setting` from `experience_id`,
  the rule above). `setting = LAB` replaces the lab-word reading.
* **Use:** `NEVER_WRITE` limits become a refusal for AI wording (like STYLE)
  and a review item for other lines; `needs_check` joins PENDING in
  `questions_for_user`; `setting` feeds `skill_origin` and coverage.

### Report beside the version, and the approval

`GET /api/resume/documents/<id>/job` now carries `report` (computed, never
stored) for a version made for a job ad, and the existing job panel beside the
preview shows: coverage (met, partly met, not found), what the version leaves
out ("Left out of this version"), risks ("Check before you send it"), and
statements waiting for confirmation. Every code is mapped to plain words in
English and Portuguese; ids are never shown. No second approval: before a
file, the statement review (now with `OUTSIDE_A_JOB`) and, for a version for a
job, the measured page count (a fact, not an approval); the report itself
blocks nothing.

### Job text written to a model

The pattern moved from the report into `tailor.retrieve`, the one place every
consumer reads asks from (Tailor, its preview, the job panel, Analyze, the AI
drafter's context, the report). `jd.analyse` is unchanged; the line is still
in the snapshot and in the run's audit record. Patterns were narrowed so that
AI-engineering, LLM, AI-safety, security-policy and prompt-writing asks stay
asks (twelve such lines tested).

### Template, language and privacy (proposal)

* Keep `clean`/`modern`/`compact`; no new `Design.template` value is persisted
  (a rolled-back build could not read it).
* Two pages by default (`budget.page_limit(False)`).
* File name, proposed: `<Given>_<Family>_<Target_Role>.pdf`, ASCII-folded,
  only letters, digits and underscores, at most 80 characters (today's
  `Full_Name_Target_Title_Resume.pdf` until the owner chooses).
* En dash allowed between two dates; em dash never in generated wording
  (STYLE refusal for AI, flag elsewhere). The ATS DATES check would need to
  accept the en dash first.
* Languages, availability and time zone: printed only from a confirmed field
  the person filled for the resume, and availability only when the ad asks;
  none exists yet. Company descriptions: only from confirmed evidence, and only
  for a little-known employer.

## Not done, by design

* No new migration, no schema change, no change to `jd.analyse`.
* No third page: `page_limit(True)` exists, nothing reaches it.
* No model call of any kind; tests use fakes or no provider at all.
