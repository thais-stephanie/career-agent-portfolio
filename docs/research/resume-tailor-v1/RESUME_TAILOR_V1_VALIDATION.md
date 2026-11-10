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

Accepting a fit applies only the proposal the person saw, whole: the server
checks the cuts against the proposal's seal (fixed below, item 3).

The browser suite passes; the full suite's two failures are the known flaky
Edge cleanup pair (below), which passed alone.
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

Final run, consent boundary (the tree of `61ed7d5` plus the sealed proposal):

| Suite | Collected | Passed | Failed | Skipped | Time | Exit code |
|---|---|---|---|---|---|---|
| unit + integration | 13,467 (`main`: 13,265) | 13,457 | 2 | 8 | 57 min 20 s | 1 |
| browser | 599 (`main`: 595) | 599 | 0 | 0 | 33 min 20 s | 0 |

The two failures are the known flaky Edge cleanup pair below
(`test_resume_pdf_cleanup.py::test_a_real_print_leaves_no_resume_no_profile_and_no_browser`,
`test_resume_export_api.py::test_printing_fetches_nothing_and_leaves_no_browser_profile`):
a `career-agent-pdf-*` profile still on disk at the check. Neither touches
the fit or the seal. Rerun alone, the two files passed twice (38 of 38, exit
code 0 each time). The suite is not counted as fully passed.
`tests/integration/test_resume_page_gate.py` alone: 52 of 52, exit code 0.

Pre-merge review run (the tree of `477341a` plus the review's tests):

| Suite | Collected | Passed | Failed | Skipped | Time | Exit code |
|---|---|---|---|---|---|---|
| unit + integration | 13,452 | 13,444 | 0 | 8 | 50 min 10 s | 0 |
| browser | 599 | 599 | 0 | 0 | 30 min 0 s | 0 |

The run before `477341a` had one failure, a regression of this work:
`test_web_boundaries.py::test_there_is_no_route_that_submits_an_application`
forbids the word "apply" in any route, and the fit route was first named
`.../fit/apply`. The route is now `POST .../fit/cuts`; the test is unchanged.

The pre-merge review added tests for gaps it found: a failure after the
history checkpoint leaves no revision and no change; a file exported before
the gate stays downloadable while a new one is refused; the routes refuse
malformed bodies, a Master and an unknown document; an accepted fit changes
nothing in the document but `hidden` flags.

The consent boundary added: only the exact sealed list applies (one more
allowed cut, one fewer, a repeat, another order, another allowed cut alone);
no seal or a made-up one; a newer revision; another version from the same
Master; a byte copy of the profile; a `CONFLICT` has no seal; the plan check
still refuses protected lines and unknown refs even when sealed; the route
requires the seal and refuses a partial or padded list with nothing changed.

The extra tests are this branch's own: 28 in `test_resume_page_gate.py` (52 cases), the
earlier v1 files, and four browser tests (the report in the job panel; a lab
skill shown as a job's stops the export; the fit in English; the fit in
Portuguese with "Editar manualmente"). Nothing was removed.

Skipped (8), all by design on a public checkout: no programme ledger (3), no
private corpus or golden set (3), no operational data in a fresh checkout (1),
a persona overlap with the worked example (1).

### Known flaky tests (neither regressions nor passes)

Seen in earlier runs of this branch and on `main` (`git archive 0e0751f`) at
the same rate; none touches code this branch changes. All three passed in
the pre-merge review run; the Edge cleanup pair failed in the consent
boundary's full run and passed alone twice.

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
| Cuts the proposal did not make | the proposal plus one cut the plan allows, one fewer, a repeat, another order, another allowed cut alone, no seal or a made-up one | refused (HTTP 409), nothing changes; only the sealed list, whole and in order, applies |
| A proposal moved elsewhere | the same proposal on a newer revision with the same words, on another version made from the same Master (same line ids), in a byte copy of the profile's database (same document, sha and revisions) | refused, nothing changes |
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
3. Consent boundary (2026-11-09): `POST .../fit/cuts` applies only the
   proposal the person accepted. `budget.propose` returns a `proposal` seal
   (HMAC-SHA256 with a key held in memory by the running process) over the
   profile's database file, the document, its newest revision, the copy's
   sha and the exact list of cuts, in order; a `CONFLICT` has no seal.
   `budget.apply_cuts` requires it and checks it with `hmac.compare_digest`
   inside the same `BEGIN IMMEDIATE` transaction as the sha check, the plan
   check (protected lines, a role's last line), the history checkpoint and
   the save. No migration, nothing stored. A restart of the app invalidates
   open proposals: the person proposes again.

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

## Professional summary (2026-11-09, branch `claude/resume-summary`)

Rule S3 of the original spec: a 3 to 5 sentence summary, about 70 to 110
words, from verified evidence only.

**Root cause.** A Master built from the career profile never had a summary,
and the Tailor copied the Master's (none). There was no summary claim type, so
nothing could fill it. A normal tailored version (the Avery fixture) had no
Summary section at all; the Morgan example had one only because the person
typed it.

**Master and Tailor now.**

* A Master is never given a summary. One the person wrote is kept.
* A version of a Master with no summary gets one from `resume_doc.summary`:
  the current role with up to three employers, two or three skills the job
  asks for, then statements copied word for word from confirmed claims of the
  roles shown (shared work last, then relevance to the job). It is a
  `RULE_REWRITE` line with the claims as `evidence_ids`; the `ADD_SUMMARY`
  change records each sentence's sources. It stops at 5 sentences, 110 words
  or 700 characters and is never padded.
* Fewer than three sentences, or no statement: no summary, and the report
  says `SUMMARY_MISSING`. Under 70 words: kept, and the report says
  `SUMMARY_SHORT`.
* When the Master's summary is kept, the job panel offers the composed one.
  Applying it saves the old text in the history and leaves the Master as is.
* Review checks a composed summary in strict mode (each token in the sources,
  numbers exact). The page fit never cuts it or treats it as a requirement
  line; a hidden summary stays hidden.

**Tests.** `tests/integration/test_resume_summary.py` (new) and a browser test
in `tests/browser/test_resume_tailor.py`. Full suite: 13,481 passed,
8 skipped, 1 failed. The failure was
`test_the_senior_dogfood_adds_real_support_reports_gaps_and_invents_nothing`,
which pinned the old contract (version summary equals the Master's). It now
checks that each statement is a confirmed claim. That file and the summary
file were rerun and pass. Browser suite: 600 passed.

**Before and after (synthetic).**

* Avery (sixteen invented roles), before: no summary. After, 42 words and
  flagged short: "Customer Success Engineer with experience at Quillfeather
  Analytics, Lumen Synthetic Ltd and Robust Ventures Example. Skilled in SQL.
  Reduced customer onboarding time from 10 days to 6 days. …"
* Harper (test fixture), after: 89 words in 5 sentences. The PDF is one page
  with SUMMARY under the name and before EXPERIENCE. The DOCX has the
  `Heading 1` "Summary" paragraph right after the title.

**Pagination.** The summary adds 3 to 7 lines. The two-page gate still holds:
the fit cuts bullets, not the summary.

**Known limits.**

* The statements repeat bullets word for word.
* Years of experience are never computed.
* Versions made before this change keep no summary until the suggestion is
  applied.
* A long summary is flagged by Analyze, not by the job panel's long-line
  suggestion.

## Resume Tailor v1.1 (2026-11-09, branch `claude/resume-summary`)

Design and section matrix: `RESUME_TAILOR_V1_1_ADAPTIVE.md`. No migration,
no schema change, no model call. Synthetic data only.

**Audit, then changes.**

* Academic and personal experiences were printed under Experience as if
  they were jobs. A new Master now places them under Projects, and their
  skills are a project's, never a job's.
* A thin profile could get a two-sentence summary made of the role and the
  only bullet. That summary is now not composed, and the report says why.
* English frames were used for Portuguese statements on a default-language
  document. The frames now follow the statements' language.
* DOCX links were plain text. They are now real hyperlinks, and a new
  `LINKS` check compares links in the PDF and DOCX with the shown ones.
* `SUMMARY_SHORT` flagged short, specific summaries; it was removed.
* `EMAIL_MISSING` fired when a phone was given; it now needs both absent.
* With no shown role, Education and Projects move ahead of Experience;
  with one role and a stronger project, Projects do. A chosen order is kept.

**Personas.** `tests/integration/test_resume_personas.py`: senior, mid,
trainee, intern, new graduate, no formal job, projects only, no projects,
no links, one link, career changer, returning after a gap, insufficient
information, and a complete example with three links. Each checks no empty
section, no placeholder, evidence and numbers only from confirmed claims,
the section order, contact rules, link round trip, profile isolation, the
Master left unchanged, an older Master upgraded unchanged, and real PDFs
(complete example at most two pages, new graduate one page).

**Before and after (synthetic, `out/resume-tailor-v1.1-examples`, not
versioned).** Before is `main` at `48c6dce`; after is this branch. Every PDF
is one page in both.

| Persona | Before | After |
|---|---|---|
| Complete | No summary; academic work under Experience | 32-word summary; Summary, Experience, Projects, Education, Certifications, Skills |
| Trainee | No summary; project under Experience | 20 words; Projects before Experience |
| New graduate | Experience heading with academic work | Education, Projects, Skills; no summary, reason said |
| Career changer | No summary | 20 words, the confirmed title only |

**Parsing evidence.** For the complete example, every check is `PASS` or
`NOT_MEASURED` in PDF and DOCX: no `FAIL`. The PDF has three `/URI` link
annotations and the DOCX three `w:hyperlink` elements. The DOCX uses the
`Title`, `Heading 1`, `Heading 2` and `List Bullet` styles. The JSON
`ROUND_TRIP` is `PASS`.

**Suites on `9b5851f`.**

* Unit and integration: 13,514 passed, 8 skipped, 1 failed (exit 1). The
  failure was `test_findings_are_named_facts_never_a_score`, which removed
  only the email and expected `EMAIL_MISSING`. The test now removes email
  and phone and still requires the finding. The three affected files were
  rerun: 72 passed, exit 0.
* Browser: 601 passed, exit 0. The Edge cleanup pair did not fail.

The suite on the final commit is recorded below.

**Compatibility.** Older documents and Masters load unchanged. Academic
work already in an older Master stays under Experience until the person
moves it or makes a new Master. Exports made before keep their files.

**Known limits.**

* Summaries are often under 35 words and may repeat one bullet.
* The strongest number is not always chosen; relevance to the job leads.
* Bullets stay verbatim.
* A project is matched to its experience by name only.
* Languages, Volunteer and Company description need a design decision.
