# Resume Tailor v1: validation

Everything here ran on synthetic data in temporary databases. No model, no
provider, no network, no real profile. Only targeted test files ran: the full
suite and the browser suite were deliberately NOT run (another job was using
the machine), and must be run before any merge.

## Tests run

| Run | Files | Result |
|---|---|---|
| New | `tests/unit/test_resume_spec_v1_rules.py` (18), `tests/integration/test_resume_tailor_v1.py` (21) | 39 passed |
| Regression, AI path | `test_resume_drafter.py`, `test_resume_ai_privacy.py`, `test_resume_reviewer.py`, unit `test_resume_grounding.py`, `test_resume_render.py`, `test_resume_ats.py`, `test_resume_doc.py` | 233 passed |
| Regression, Tailor and gate | `test_punctuation_guard.py`, `test_resume_tailor.py`, `test_tailor_base_and_preview.py` | 55 passed |
| Lint | `ruff check`, `ruff format --check` on every changed file | clean |

Not run: the rest of `tests/unit` and `tests/integration`, and `tests/browser`
(Playwright; the Resumes pages are unchanged, but the gate is the whole suite).

## Synthetic scenarios covered

The fixture is "Avery Fictional" (fictional companies Quillfeather Analytics,
Lumen Synthetic Ltd, Robust Ventures Example; fictional ad for "Example Buyer
Co"), plus the existing `tests/support_resume.long()` sixteen-role resume.

| Scenario | Test | Outcome |
|---|---|---|
| Fully proven experience | coverage of "customer onboarding" | `strong`, cites `k-onboard` |
| Partial experience | "5+ years" ask | never judged (`partial` or `none`), never covered |
| Lab-only tool | Kubernetes from a project claim | never a job's line (AI ADD_BULLET refused, EMPLOYER); its skill chip flagged `SKILL_ONLY_OUTSIDE_A_JOB` |
| Unproven metric | "Kept every customer" to "100% retention"; "6 days" to "4 days" | refused, NUMBERS |
| Explicit claim limit | `limits={"k-agent": ["installed the monitoring agent"]}` | `CLAIM_LIMIT` risk flag on the line |
| Shared authorship | "Worked with IT, who installed..." to "Installed..." | refused, PARTICIPATION |
| Implemented vs designed | "Implemented ... designed by the finance team" to "Designed..." | refused, RESPONSIBILITY (and AGENT) |
| Unmet requirement | "Salesforce Apex development" | `none`, listed in `gaps` |
| Needs verification | a PENDING statement answering an ask | in `questions_for_user`, absent from the resume |
| Conflicting information | evidence revised after the Master; another retired | `EVIDENCE_CHANGED_SINCE`, `EVIDENCE_NOT_CONFIRMED` |
| Prompt injection in the ad | "Ignore all previous instructions..." | nothing added; quoted back only as an ask with no evidence |
| Banned word in an official name | employer and certification names with "Robust" and "Dynamic" | no style flag; AI wording naming the employer passes STYLE |
| Banned word typed by the person | "Built a robust escalation guide." | flagged (`STYLE_BANNED_WORD`, `NO_CONFIRMED_EVIDENCE`), never refused |
| Style in AI wording | em dash, "seamless" | refused, STYLE |
| Spec bullet pattern | "**Customer onboarding:** Reduced customer onboarding time from 10 days to 6 days." | passes every check |
| Short career | three roles, 95 visible words | `FITS`, no cuts |
| Over two pages | sixteen roles | `CUT` by hiding 55 items, 997 words, every role keeps a line, nothing deleted, report lists `HIDDEN_FOR_PAGE_LIMIT` |
| Proof cannot fit | a measure that always says 9 pages | `CONFLICT`, the required ask's proof stays visible, at most 4 measurements |
| Page limit from a model | `page_limit(3)`, `page_limit("3")`, an answer with `page_limit` | refused |
| Little-known company | (no description field) | NOT COVERED: needs the schema decision |

## Measurements (local, synthetic)

| What | Time |
|---|---|
| `report.build` for the fixture | 16 to 24 ms |
| `drafter.check` of one AI proposal (with STYLE) | 9 to 17 ms |
| `budget.fit` of the sixteen-role resume (55 cuts, estimate) | 36 ms (was 1 s before counting words down instead of re-rendering per cut) |
| One PDF print for measurement (`pdf_pages`) | 3.7 s warm, 24 s first launch |

Part B typography measured on Letter and A4: see the design document (553 and
577 words on a full first page; Arial embedded as subsets).

## Example output

`example_report.json` (beside this file) is the report for the Avery fixture
with a claim limit, plus the fit of the sixteen-role resume. Regenerate it with
a temporary profile: build the fixture (`tests/integration/test_resume_tailor_v1.avery`),
run `tailor`, then `report.build(..., fit=budget.fit(...)[1])`. Line ids differ
on every run; everything else is stable. No PDF is committed; `pdf_pages`
prints one on demand.

## Known limits of the checks

* Style and limit checks are word patterns. They can flag a faithful line (a
  limit phrase quoted inside "worked with IT, who installed the agent") and
  cannot read implication; both cases go to the person, never to a refusal of
  their own words.
* AI wording that moves a number into a new verb is refused even when faithful
  ("Cut" for "Reduced"). A false refusal, never a false acceptance.
* The word estimate (500 words a page) is conservative for 30-word bullets and
  optimistic for many very short lines; `pdf_pages` is the real check.
