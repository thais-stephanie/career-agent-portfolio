# Resume Tailor v1: validation

Everything here ran on synthetic data in temporary databases and temporary
browser profiles. No model, no provider, no network (the suite's socket guard
was on, and every API key and Ollama variable was removed from the test
process's environment). No real profile, catalogue or configuration was read.

## Verdict

**READY FOR INTEGRATION REVIEW.** The two items left open on 2026-11-08 are
closed and connected to the application, with no migration:

* **Two pages.** A tailored version that prints past two pages does not
  download (PDF, DOCX or JSON), from any path: the check is in
  `export.export_revision`, the only export path, after the existing review
  and before anything is written. The person is shown the lines a fit would
  hide, where each one is and why, and the lines kept as proof; nothing
  changes until they accept, on the copy they saw. The Master is not limited.
* **Skills of unknown origin.** They stay visible in the Master, in their own
  group ("Other skills"), with a finding asking where they come from; the
  Tailor never suggests them, and a tailored version leaves them out and
  lists why.

Both suites pass apart from one test this branch broke and then fixed (below).
Still blocked by storage decisions, as before: the per-job third page, claim
limits and evidence class, a template value, privacy fields. Not merged, not
pushed.

## Suites (2026-11-09, Windows 11, 16 GB, one suite at a time)

Commands, from the worktree root, with the install's interpreter and
`PYTHONPATH=<worktree>/src`, every API key unset:

```
python -m pytest tests/unit tests/integration -rfEs --durations=25 -p no:cacheprovider
python -m pytest tests/browser -rfEs --durations=15 -p no:cacheprovider
```

| Suite | Collected | Passed | Failed | Skipped | Time |
|---|---|---|---|---|---|
| unit + integration | 13,438 (`main`: 13,265) | 13,429 | 1 | 8 | 56 min 41 s |
| browser | 599 (`main`: 595) | 599 | 0 | 0 | 33 min 10 s |

The one failure was a regression of this work, not a flaky test:
`test_web_boundaries.py::test_there_is_no_route_that_submits_an_application`
forbids the word "apply" in any route, and the fit route was first named
`.../fit/apply`. The route is now `POST .../fit/cuts`; the test is unchanged.
After the rename, the route guard, `test_resume_page_gate.py`,
`test_resume_tailor_v1_completion.py`, the four documentation guards and
`tests/browser/test_resume_tailor.py` were run again and pass. The rest of
both suites was not run again for a one-word path change.

The extra tests are this branch's own: 18 in `test_resume_page_gate.py`, the
earlier v1 files, and four browser tests (the report in the job panel; a lab
skill shown as a job's stops the export; the fit in English; the fit in
Portuguese with "Editar manualmente"). Nothing was removed.

Skipped (8), all by design on a public checkout: no programme ledger (3), no
private corpus or golden set (3), no operational data in a fresh checkout (1),
a persona overlap with the worked example (1).

### Known flaky tests (neither regressions nor passes)

Seen in earlier runs of this branch and on `main` (`git archive 0e0751f`) at
the same rate; none touches code this branch changes. All three passed in
these runs.

| Test | Seen on `main` | Cause |
|---|---|---|
| `test_release_personas.py::test_complete_persona_journey[retail-br]` | 1 of 6 | timing |
| `test_resume_export_api.py::test_printing_fetches_nothing_and_leaves_no_browser_profile` (and `test_resume_pdf_cleanup`) | 1 of 2 in its file | Edge holds its temporary profile past the cleanup under load |
| `test_onboarding_v2.py::test_each_card_costs_a_bounded_number_of_requests` | 1 of 3 | timing of a background request |

No assertion was relaxed, no timeout raised, no test removed or skipped.

Also run on the final tree: `ruff check src tests`, `ruff format --check`,
`mypy src` (the same 5 errors as `main`, none in this slice),
`node scripts/frontend_check.mjs` (clean).

## Named risks

| Risk | Check | Result |
|---|---|---|
| Over two pages exported | tailored version printing 3 pages, exported as PDF, DOCX and JSON | refused `over_page_limit` (HTTP 409 in words, pages and limit), no revision, no export row, no file; 1 and 2 pages export; the Master prints any length |
| Cut without consent | proposal, then nothing accepted | the proposal changes nothing; the document, its sha and its revisions are unchanged and the gate stays |
| Cut accepted | apply exactly the proposed lines | only those lines hidden, nothing deleted; one `MANUAL_CHECKPOINT` revision of the earlier copy; every role keeps a line; the strongest line of each required ask kept; the Master unchanged; export then passes with the real PDF at two pages or fewer |
| Stale approval or silent replacement | apply with the sha of an earlier copy; a reused approval after a change | HTTP 409, nothing applied; the browser refuses when the copy on screen is not the one proposed |
| Lines nobody proposed | apply refs outside the plan, duplicates, empty | refused, nothing changes |
| Proof cannot fit | protected lines alone exceed the limit | `CONFLICT`: nothing can be applied, the protected lines are listed, "Edit it myself" offered |
| Second approval or bypass | the existing review before the page check; every export format; copy and Master paths | the review stays the only approval and runs first; a copy of a tailored version stays tailored; no other export path exists |
| Content changed after approval | statement edited after acknowledgement | the review asks again before export |
| Measurement loops | fit with a counting printer | at most `MAX_ROUNDS` (4) measures |
| Skill of unknown origin | new Master, older Master with it in "Skills", Tailor suggestions | kept visible under "Other skills" with `SKILL_ORIGIN_UNKNOWN`; the older Master unchanged and flagged; never suggested; left out of a tailored version and listed |
| Lab skill shown as professional experience | AI rewrites; skill chips; an older version | as on 2026-11-08: refused, grouped, stopped at the review |
| Instructions in the ad | five model-directed lines, twelve ordinary ones | as on 2026-11-08 |
| Profile isolation | the fit and the apply from another profile | not found; only its own evidence |

## Fixed, reproduced, unresolved

Fixed on this branch after `54b4427`:

1. The two-page gate in the export, with a fit the person reviews line by
   line (`budget.propose`, `budget.apply_cuts`, `POST .../fit`,
   `POST .../fit/cuts`), in the export box and in the preview's checks.
2. Skills of unknown origin kept in the Master and flagged, never suggested,
   left out of tailored versions.

Reproduced, not changed (design decision or outside this slice):

1. Number grounding refuses faithful rewrites (`tailor.grounding`).
2. The three flaky tests above, also on `main`.
3. `main` carries 5 `mypy` errors (`workspace_api.py`, `ranking.py`,
   `web/export.py`).

Unresolved:

1. The per-job third page: storage proposal only (`application_resume_option`);
   there is no exception to two pages.
2. Claim limits and evidence class: proposal only (`career_evidence_annotation`).
3. Lab origin is read from the claim's own words; a lab claim not worded so,
   with no experience, is treated as of unknown origin (kept in the Master,
   flagged), never as a job's.
4. Template value, file name, company descriptions, languages and time zone:
   proposals only.
5. Behaviour change for the owner: an existing tailored version over two pages
   no longer downloads until it is fitted or edited. Earlier exported files
   stay downloadable.

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
| Unknown origin | kept in the Master under "Other skills" and flagged; never suggested; left out of a tailored version and listed |
| Unproven metric | refused, `NUMBERS` |
| Explicit claim limit | `CLAIM_LIMIT` risk with the line text |
| Shared authorship, implemented vs designed | refused, `PARTICIPATION`, `RESPONSIBILITY` |
| Unmet requirement | in `gaps` |
| Needs verification | a question for the person, absent from the resume |
| Conflicting information | `EVIDENCE_CHANGED_SINCE`, `EVIDENCE_NOT_CONFIRMED` |
| Instructions in the ad | dropped, quoted back as a risk |
| Ordinary AI and security roles | read as ordinary asks |
| Style in AI wording | refused, `STYLE`; in the person's own words flagged only |
| Over two pages | export refused; `CUT` proposed by hiding, applied only when accepted |
| Proof cannot fit | `CONFLICT`, the proof stays visible, edit by hand offered |
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
  their own words.
* AI wording that moves a number into a new verb or order is refused even when
  faithful. A false refusal, never a false acceptance.
* The word estimate (500 words a page) is conservative for 30-word bullets and
  optimistic for many very short lines; `pdf_pages` is the real check.
