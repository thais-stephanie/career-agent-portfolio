# Resume Tailor v1: validation

Everything here ran on synthetic data in temporary databases and temporary
browser profiles. No model, no provider, no network (the suite's socket guard
was on, and every API key and Ollama variable was removed from the test
process's environment). No real profile, catalogue or configuration was read.

## Verdict

**NEEDS DESIGN DECISION.** The implemented slice (style rules, page budget,
report) passes both suites apart from three flaky tests that fail on `main`
too, and is ready to be reviewed. The rest of spec v1 (stored page-limit
exception, Limits and evidence class, lab labelling, report beside the
preview, export gate) waits on the owner decisions listed below. Not merged.

## Suites (2026-11-08, Windows 11, 16 GB, one suite at a time)

Commit under test: `f7d0190`. The local app was open during both runs (idle
after an auto-update that finished before the first suite; no collection,
rescore or Workable read ran during them). Commands, from the worktree root,
with the install's interpreter and `PYTHONPATH=<worktree>/src`:

```
python -m pytest tests/unit tests/integration -q -rfEs --durations=25 -p no:cacheprovider
python -m pytest tests/browser -q -rfEs --durations=15 -p no:cacheprovider
```

| Suite | Collected | Passed | Failed | Skipped | Time |
|---|---|---|---|---|---|
| unit + integration | 13,313 (`main`: 13,265) | 13,304 | 1 | 8 | 49 min 47 s |
| browser | 595 (`main`: 595) | 593 | 2 | 0 | 30 min 6 s |

The 48 extra tests are this branch's own (parametrized). Nothing was removed.

Skipped (8), all by design on a public checkout: no programme ledger (3), no
private corpus or golden set (3), no operational data in a fresh checkout (1),
a persona overlap with the worked example (1).

### Failures, each compared with `main` under the same conditions

`main` was `git archive 0e0751f` into a temporary folder, run with the same
interpreter and environment, alternating with this branch.

| Test | What failed | Branch | `main` | Cause |
|---|---|---|---|---|
| `test_resume_export_api.py::test_printing_fetches_nothing_and_leaves_no_browser_profile` | an Edge temp profile (`career-agent-pdf-*`) still present after the export | passed 5 of 5 alone; 2 of 2 in its file | failed 1 of 2 in its file | Edge held its profile past the ~20 s cleanup under load. Pre-existing; `export.py` is unchanged here. |
| `test_onboarding_v2.py::test_each_card_costs_a_bounded_number_of_requests` | one extra `GET /api/jobs/narrowings` while moving between cards | failed 1 of 3 | failed 1 of 3 | Timing of a background request. Pre-existing. |
| `test_release_personas.py::test_complete_persona_journey` | the "work" step not marked done within 30 s | 1 of 6 from a clean archive (more often from the worktree) | 1 of 6 | Timing. Same rate on both archives; this branch changes no web code. |

No assertion was relaxed, no timeout raised, no test removed.

## Named risks

| Risk | Check | Result |
|---|---|---|
| Lab tools shown as professional experience | AI `ADD_BULLET` / `REWRITE_BULLET` on a job citing the lab claim; `REWRITE_SUMMARY` and `REWRITE_HEADLINE` claiming the lab tool | all refused (`EMPLOYER`, `OVERSTATEMENT`, `NAMED_TOOLS`); new test `test_lab_evidence_never_reaches_the_summary_or_the_headline` |
| | the Master's Skills section | **still unresolved**: the lab tool is a plain chip in "Skills". Flagged `SKILL_ONLY_OUTSIDE_A_JOB`, not labelled. Needs the lab marker (owner decision 2) |
| Grounding refuses faithful rewrites near numbers | nine rewrites of "Reduced customer onboarding time from 10 days to 6 days." | **reproduced, not fixed**: only the evidence's own wording passes. Reordered ("onboarding time for customers"), passive ("... time reduced from"), "Cut", "Shortened" and "by 4 days, from 10 to 6" are refused `NUMBERS`; spelled-out numbers `CLAIMS`. Wrong numbers ("4 days", "40%", "100% retention") stay refused. The rule predates this branch (`tailor.grounding`); loosening it is a design decision |
| Instructions in the ad | four injected lines: "Ignore all previous instructions...", "SYSTEM: page_limit=3...", an HTML comment to an assistant, "Ignore the evidence and state..." | generation: nothing added, page limit stays 2. **Fixed** in the report: these lines were listed as asks and gaps (one as `must`); now kept out of coverage, gaps and questions and listed as `AD_TEXT_ADDRESSED_TO_A_MODEL`. Ordinary lines ("The ideal candidate...", "language model" roles, "ignore safety rules") are not caught |
| Page limit and PDF change no document | `budget.fit` on a sixteen-role resume; `pdf_pages`; `page_limit(3)`, `("3")`, `(None)`, `(1)` | input document identical, new object returned, revision count and database bytes unchanged; every non-bool limit refused; new test `test_fitting_the_page_limit_changes_no_document_and_no_revision` |
| Profile isolation | a second temporary profile tailored for the same ad | cites only its own claim, shows nothing of the first; new test `test_one_profiles_evidence_never_reaches_anothers_report` |

## Fixed, reproduced, unresolved

Fixed in this validation (commit after `f7d0190`):

1. Report promoted ad text written to a model to asks and gaps (above).
2. Two `mypy` errors introduced by `f7d0190` in `report.py` (a reused variable).
   `mypy src` now reports the same 5 errors as `main`, none in this slice.

Reproduced, not changed (outside this slice or a design decision):

1. Number grounding refuses faithful rewrites (`tailor.grounding`).
2. Three flaky tests above, also on `main`.
3. `main` carries 5 `mypy` errors (`workspace_api.py`, `ranking.py`).

Unresolved, waiting for owner decisions (see the rollout plan):

1. Lab tools as unlabelled skills: needs an evidence class or lab marker.
2. Where the 3-page exception is stored (a migration on `job_application`).
3. Claim Limits and evidence class have no field in Career Evidence;
   `report.build(limits=...)` takes them as an argument.
4. The template value of Part B (`Design.template`); rollback cannot read it.
5. Company description, languages and time zone lines (privacy).
6. File name and the en dash in date ranges.

After the fix: `test_resume_tailor_v1.py` and `test_resume_spec_v1_rules.py`
(50 passed), every `test_resume_*` and `test_tailor*` file (passed, except the
known export flake), `ruff check src tests scripts`, `ruff format --check`,
`mypy src` (no new error), `node scripts/frontend_check.mjs` (clean).
`report.py` is imported by no other module, so the full suites were not rerun.

## Synthetic scenarios covered

The fixture is "Avery Fictional" (fictional companies Quillfeather Analytics,
Lumen Synthetic Ltd, Robust Ventures Example; fictional ad for "Example Buyer
Co"), plus the existing `tests/support_resume.long()` sixteen-role resume and,
for isolation, a second fictional profile (Harbor Example Freight).

| Scenario | Test | Outcome |
|---|---|---|
| Fully proven experience | coverage of "customer onboarding" | `strong`, cites `k-onboard` |
| Partial experience | "5+ years" ask | never judged (`partial` or `none`), never covered |
| Lab-only tool | Kubernetes from a project claim | never a job's line, summary or headline; its skill chip flagged `SKILL_ONLY_OUTSIDE_A_JOB` |
| Unproven metric | "Kept every customer" to "100% retention"; "6 days" to "4 days" | refused, NUMBERS |
| Explicit claim limit | `limits={"k-agent": ["installed the monitoring agent"]}` | `CLAIM_LIMIT` risk flag on the line |
| Shared authorship | "Worked with IT, who installed..." to "Installed..." | refused, PARTICIPATION |
| Implemented vs designed | "Implemented ... designed by the finance team" to "Designed..." | refused, RESPONSIBILITY (and AGENT) |
| Unmet requirement | "Salesforce Apex development" | `none`, listed in `gaps` |
| Needs verification | a PENDING statement answering an ask | in `questions_for_user`, absent from the resume |
| Conflicting information | evidence revised after the Master; another retired | `EVIDENCE_CHANGED_SINCE`, `EVIDENCE_NOT_CONFIRMED` |
| Instructions in the ad | four injected lines | nothing added; never an ask or gap; `AD_TEXT_ADDRESSED_TO_A_MODEL` |
| Banned word in an official name | employer and certification names with "Robust" and "Dynamic" | no style flag; AI wording naming the employer passes STYLE |
| Banned word typed by the person | "Built a robust escalation guide." | flagged (`STYLE_BANNED_WORD`, `NO_CONFIRMED_EVIDENCE`), never refused |
| Style in AI wording | em dash, "seamless" | refused, STYLE |
| Spec bullet pattern | "**Customer onboarding:** Reduced customer onboarding time from 10 days to 6 days." | passes every check |
| Short career | three roles | `FITS`, no cuts |
| Over two pages | sixteen roles | `CUT` by hiding 55 items, nothing deleted, report lists `HIDDEN_FOR_PAGE_LIMIT` |
| Proof cannot fit | a measure that always says 9 pages | `CONFLICT`, the required ask's proof stays visible, at most 4 measurements |
| Page limit from a model | `page_limit(3)`, `page_limit("3")`, an answer with `page_limit` | refused |
| Two profiles | the same ad on a second profile | only its own evidence |
| Little-known company | (no description field) | NOT COVERED: needs the schema decision |

## Measurements (local, synthetic)

| What | Time |
|---|---|
| `report.build` for the fixture | 16 to 24 ms |
| `drafter.check` of one AI proposal (with STYLE) | 9 to 17 ms |
| `budget.fit` of the sixteen-role resume (55 cuts, estimate) | 36 to 135 ms (135 ms during the full suite) |
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

* Style, limit and model-instruction checks are word patterns. They can flag a
  faithful line and cannot read implication; both cases go to the person,
  never to a refusal of their own words.
* AI wording that moves a number into a new verb or order is refused even when
  faithful. A false refusal, never a false acceptance.
* The word estimate (500 words a page) is conservative for 30-word bullets and
  optimistic for many very short lines; `pdf_pages` is the real check.
