# Resume Tailor v1: validation

Everything here ran on synthetic data in temporary databases and temporary
browser profiles. No model, no provider, no network (the suite's socket guard
was on, and every API key and Ollama variable was removed from the test
process's environment). No real profile, catalogue or configuration was read.

## Verdict

**PARTIALLY IMPLEMENTED.** The owner decisions of 2026-11-08 that need no
stored data are applied and connected to the application: a lab's or a
project's skill is never shown as a job's (Master, Tailor, "add this skill",
and the export review, which cannot be waved through); ad lines written to a
model are dropped for every consumer; the report is in the job panel, in
English and Portuguese, with no second approval. Both suites pass apart from
one known flaky test. Not connected yet: fitting the page limit before export
(`budget.fit` is still called only by tests; the export measures the real PDF
and records its page count, but nothing holds a tailored version to two
pages). Blocked by storage decisions: the per-job third page, claim limits and
evidence class, a template value, privacy fields. Not merged, not pushed.

## Suites (2026-11-09, Windows 11, 16 GB, one suite at a time)

Commit under test: the commit after `7f867e2` on `claude/resume-tailor-v1`
(the working tree as committed; no change after the runs). The owner's app was
open and idle; no collection, rescore or Workable read ran during the runs.
Commands, from the worktree root, with the install's interpreter and
`PYTHONPATH=<worktree>/src`:

```
python -m pytest tests/unit tests/integration -rfEs --durations=25 -p no:cacheprovider
python -m pytest tests/browser -rfEs --durations=15 -p no:cacheprovider
```

| Suite | Collected | Passed | Failed | Skipped | Time |
|---|---|---|---|---|---|
| unit + integration | 13,393 (`main`: 13,265) | 13,385 | 0 | 8 | 42 min 52 s |
| browser | 597 (`main`: 595) | 596 | 1 | 0 | 28 min 52 s |

The extra tests are this branch's own: 38 in
`test_resume_tailor_v1_completion.py`, the earlier v1 files, and two browser
tests (the report in the job panel; a lab skill shown as a job's stops the
export). Nothing was removed.

Skipped (8), all by design on a public checkout: no programme ledger (3), no
private corpus or golden set (3), no operational data in a fresh checkout (1),
a persona overlap with the worked example (1).

### Known flaky tests (neither regressions nor passes)

Each was compared with `main` (`git archive 0e0751f`, same interpreter and
environment) in the previous validation and fails there at the same rate.
None touches code this branch changes.

| Test | This run | Seen on `main` | Cause |
|---|---|---|---|
| `test_release_personas.py::test_complete_persona_journey[retail-br]` | failed: the "work" step not marked done within 30 s | 1 of 6 | timing |
| `test_resume_export_api.py::test_printing_fetches_nothing_and_leaves_no_browser_profile` (and the same check in `test_resume_pdf_cleanup`) | passed in the full suite; failed once in a targeted rerun of the resume files during this work | 1 of 2 in its file | Edge holds its temporary profile past the cleanup under load; `export.py` is unchanged |
| `test_onboarding_v2.py::test_each_card_costs_a_bounded_number_of_requests` | passed | 1 of 3 | timing of a background request |

No assertion was relaxed, no timeout raised, no test removed or skipped.

Also run on the final tree: `ruff check src tests scripts`, `ruff format
--check`, `mypy src` (the same 5 errors as `main`, none in this slice),
`node scripts/frontend_check.mjs` (clean).

## Named risks

| Risk | Check | Result |
|---|---|---|
| Lab skill shown as professional experience | AI `ADD_BULLET` / `REWRITE_BULLET` on a job citing a lab claim; summary and headline claiming the lab tool | refused (`EMPLOYER`, `OVERSTATEMENT`, `NAMED_TOOLS`), unchanged |
| | the Master's and a tailored version's skill chips | **fixed**: a lab's skill goes to "Lab", a project's to "Independent projects" (Portuguese and Spanish names too), an unplaced one to a hidden "Origin not confirmed" group listed as left out; a job's stays in "Skills" |
| | an older version with a lab chip in "Skills" | Tailor moves it (`MOVE_SKILL`, nothing deleted); exporting it as it is stops at the review (`OUTSIDE_A_JOB`, cannot be acknowledged, HTTP 409 in words); renaming the group to say where it comes from clears it |
| Approval flow broken or doubled | export with an open review item; the report in the job panel | the existing review is the only gate; the report blocks nothing and adds no approval; no route skips the review |
| Grounding refuses faithful rewrites near numbers | reordered, passive, "Cut" | **reproduced, unchanged** (owner decision): each pinned as a known false refusal (`NUMBERS`); seven must-refuse cases (number, invented metric, period, tool, authorship, participation, lost qualifier) pinned; `grounding_v2` proposal in the design document |
| Instructions in the ad | five lines written to a model; twelve ordinary AI, language model, AI safety, security policy and prompt-writing lines | the five are dropped in `tailor.retrieve` (coverage, suggestions, the AI context, the report) and quoted back as a risk; none of the twelve is caught; `jd.analyse` unchanged |
| Page limit | `page_limit(False)` everywhere, `budget.fit` on sixteen roles | two pages; the fit changes no document; **not connected to export** |
| Profile isolation | a second temporary profile on the same ad | only its own evidence, unchanged |

## Fixed, reproduced, unresolved

Fixed on this branch after `7f867e2`:

1. Lab and project skills placed in a labelled group, unplaced ones hidden and
   listed; the export review stops a lab or project skill shown as a job's.
2. Ad lines written to a model dropped at the shared `tailor.retrieve`, so the
   panel, Make it better and the AI context never treat them as asks.
3. The report connected to the job panel (`GET .../job` carries `report`),
   every code worded in English and Portuguese, line text included in flags.

Reproduced, not changed (design decision or outside this slice):

1. Number grounding refuses faithful rewrites (`tailor.grounding`).
2. The three flaky tests above, also on `main`.
3. `main` carries 5 `mypy` errors (`workspace_api.py`, `ranking.py`).

Unresolved:

1. Fit before export (`budget.fit`): implemented and tested, not integrated.
2. The per-job third page: storage proposal only (`application_resume_option`).
3. Claim limits and evidence class: proposal only (`career_evidence_annotation`);
   `report.build(limits=...)` takes them as an argument nobody passes yet.
4. Lab origin is read from the claim's own words ("lab", "home lab",
   "laboratório"); a lab claim not worded so, with no experience, is hidden as
   "origin not confirmed", never shown as a job's.
5. Standalone skill claims linked to no experience are now hidden in new
   Masters and versions (left out, listed); the owner should review this effect.
6. Template value, file name, company descriptions, languages and time zone:
   proposals only.

## Synthetic scenarios covered

Fixtures: "Avery Fictional" (Quillfeather Analytics, Lumen Synthetic Ltd,
Robust Ventures Example; ad for "Example Buyer Co"); a mixed profile at
"Harbor Example Freight" with a job claim (SQL), a home lab claim (Kubernetes),
an independent project (Django planner) and a tool with no experience
(Terraform); the sixteen-role `tests/support_resume.long()`; a second profile
for isolation.

| Scenario | Outcome |
|---|---|
| Fully proven experience | `strong`, cites its claim |
| Partial experience ("5+ years") | never judged, never covered |
| Lab-only skill | in "Lab", never a job's line, summary, headline or "Skills" chip |
| Independent project skill | in "Independent projects" |
| Real job skill | stays in "Skills" |
| Unknown origin | hidden, listed as left out; flagged if shown again |
| Unproven metric | refused, `NUMBERS` |
| Explicit claim limit | `CLAIM_LIMIT` risk with the line text |
| Shared authorship, implemented vs designed | refused, `PARTICIPATION`, `RESPONSIBILITY` |
| Unmet requirement | in `gaps` |
| Needs verification | a question for the person, absent from the resume |
| Conflicting information | `EVIDENCE_CHANGED_SINCE`, `EVIDENCE_NOT_CONFIRMED` |
| Instructions in the ad | dropped, quoted back as a risk |
| Ordinary AI and security roles | read as ordinary asks |
| Style in AI wording | refused, `STYLE`; in the person's own words flagged only |
| Over two pages | `CUT` by hiding, nothing deleted (by `budget.fit`, tests only) |
| Proof cannot fit | `CONFLICT`, the proof stays visible |
| Page limit from a model | refused |
| Two profiles | only its own evidence |

## Measurements (local, synthetic)

| What | Time |
|---|---|
| `report.build` for the fixture | 16 to 31 ms |
| `drafter.check` of one AI proposal (with STYLE) | 9 to 17 ms |
| `budget.fit` of the sixteen-role resume (55 cuts, estimate) | 36 to 135 ms |
| One PDF print for measurement (`pdf_pages`) | 3.7 s warm, 24 s first launch |

## Example output

`example_report.json` (beside this file) is the report for the Avery fixture
with a claim limit, plus the fit of the sixteen-role resume. Line ids differ on
every run. No PDF is committed.

## Known limits of the checks

* Style, limit, lab and model-instruction checks are word patterns. They can
  miss implication; a miss goes to the person as a flag, never to a refusal of
  their own words, and a skill they cannot place is hidden, not shown.
* AI wording that moves a number into a new verb or order is refused even when
  faithful. A false refusal, never a false acceptance.
* The word estimate (500 words a page) is conservative for 30-word bullets and
  optimistic for many very short lines; `pdf_pages` is the real check.
