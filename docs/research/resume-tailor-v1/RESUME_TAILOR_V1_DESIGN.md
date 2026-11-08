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
omitted_evidence[]: {evidence_id, reason: ANSWERS_AN_ASK_NOT_SHOWN | HIDDEN_FOR_PAGE_LIMIT}
risk_flags[]: {code, ref, detail?, origin?, evidence_id?}
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
`PAGE_LIMIT_CONFLICT`.

## Where each truth rule is enforced

| Spec rule | Enforced by | Strength |
|---|---|---|
| Only evidence or master facts | Strategy uses confirmed claims verbatim; `unconfirmed_lines` on every write and export | Refusal |
| Limits ("never write X") | `style.limit_findings`, report `CLAIM_LIMIT` | Risk flag (data source missing) |
| Numbers and qualifiers exact | `grounding` amounts | Refusal (AI) |
| No metric from a description | `grounding` NUMBERS, CLAIMS | Refusal (AI) |
| No merging or moving results | EMPLOYER rule, amount from its own sentence | Refusal (AI) |
| Exact authorship | `_responsibility` (RESPONSIBILITY, PARTICIPATION, AGENT) | Refusal (AI) |
| Lab apart from jobs | EMPLOYER rule for lines; report flags for skills and coverage | Refusal for lines, flag for skills |
| Needs verification | PENDING never usable; `questions_for_user` | Refusal + question |
| Gaps never filled | Strategy adds only confirmed lines; `gaps` | By construction |
| Tenure never recomputed | Years asks at most "partly" | By construction |
| Job text is data | Snapshot quotes only; drafter prompt marks it untrusted | By construction |
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

## Not done, by design

* No new migration, no schema change, no change to the Master builder, no UI.
* No model call of any kind; tests use fakes or no provider at all.
