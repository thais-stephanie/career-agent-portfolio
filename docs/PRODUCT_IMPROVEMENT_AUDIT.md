# Product improvement audit

Branch `audit/alpha2-hardening`, 2026-09-23. A pass aimed at one person: somebody
who is not technical, has never seen Career Agent, and wants relevant jobs
quickly. Nothing here was pushed or merged.

Every observation below was made by running the application, not by reading
it: an isolated copy of the shipped configuration and a throwaway database per
run, driven by the repository's own headless Chrome harness
(`tests/browser/chrome.py`) at 1366x900 and 390x844, in both themes and both
languages. No run touched `data/`, a personal database or the network.

## 1. What a new person met before

* **Home on a fresh install** was every question at once: a paragraph about a
  "first checkpoint" for a list that did not exist, six numbered steps (one of
  them two text boxes), four counters all reading zero, a "Finish your Career
  Profile" card repeating three of the six steps as sentences, and two sections
  saying "Nothing here today". About 2,400 px before anything had been done.
* **Discover, empty**, showed the whole list toolbar -- filters, "Today", three
  views, grouping and a sort order -- over nothing, and an empty card telling the
  reader to open "Retrieve jobs" under Settings.
* **"Retrieve jobs"** lived inside a collapsed "Advanced refresh diagnostics"
  section at the bottom of Settings, and it only collected employer boards
  already in the database -- which a fresh database has none of. The sources
  that could collect had one "Refresh now" button each (26 cards), one run at a
  time. A first search was about twenty clicks with a wait between each.
* **Settings** was about 10,100 px tall once its sources loaded, most of it the
  26 source cards, always open.
* **Career Evidence, empty**, opened on three paragraphs of instructions, three
  buttons (one of them "Needs organizing (0)") and five empty disclosures, with
  no way to import a CV above the fold.
* **Applications, empty**, was five empty columns under the full toolbar, with
  no route back to where jobs are found.
* **The guided questions that did exist** (`onboarding.js`) asked for the
  country as a two-letter code, offered hiring regions by internal id, had no
  Back, and did not ask what work the person wants -- that was a separate text
  box on Home.
* **Settings and Career Profile were slow** on the worked-example configuration:
  each of `/api/profile`, `/api/preferences`, `/api/sources` and
  `/api/source-maintenance` took 240-275 ms, nearly all of it parsing YAML.

## 2. Friction found

| Where | Friction | Kind |
|---|---|---|
| Home, fresh | Every question on one screen; the same gaps listed twice; zero counters; "checkpoint" jargon | cognitive load |
| Finding jobs | The only all-at-once control was hidden and collected nothing on a fresh install | blocker |
| Discover, empty | Directions instead of a button; a toolbar that could act on nothing | empty state |
| Guided questions | Country codes, region ids, no Back, the work question elsewhere | terminology, navigation |
| Settings | 10,000 px; 26 always-open cards before anything a person changes | information architecture |
| Evidence, empty | Instructions before the one action that matters (importing a CV) | empty state |
| Applications, empty | No sentence saying what the board is for, no way to Discover | empty state |
| Evidence add form | The paragraph box had no accessible name | accessibility |
| Launcher | No sense of which step or how long; a port conflict answered with a PowerShell command | install/startup |
| Preferences | "Ways of working" and "levels you are looking for" are recorded but nothing matches, filters or ranks by them | honesty (see section 10) |

## 3. What changed

Commits are listed in section 12.

1. **A guided setup, one question per card** (`js/setup.js`). A fresh install
   -- nothing confirmed and no search described, the server's own `fresh`
   verdict -- opens on it instead of Home: Welcome, the work you want, career
   stage, where you live, where companies can hire you, hiring regions, levels
   to keep off the list, pay target, and a Ready card.
   * One decision per card; "Question n of 7" and a row of dots; Back, Skip on
     every question, Continue as the form's submit button so Enter works; focus
     moves to each card's heading; errors appear beside the field with
     `role=alert`, `aria-invalid` and `aria-describedby`.
   * Each answer is saved on Continue through the existing whitelisted routes
     (`PATCH /api/profile`, `POST /api/first-search`, `POST /api/firstrun/stage`);
     Back shows what was saved and what was typed and not yet saved.
   * Countries and currencies are chosen by name, in the chosen language
     (`Intl.DisplayNames`); hiring regions have plain labels ("Latin America",
     "Europe, the Middle East and Africa").
   * **Where you live is never copied into where you can be hired.** That is its
     own question -- "Can a company hire you directly in Brazil?" with "Yes" or
     "I am not sure yet" -- so unknown eligibility stays unknown.
   * The Ready card summarises the answers, each with Change, and has
     **Find jobs now**, a progress bar, a Stop button and "See your jobs".
   * "Do this later" leaves it (remembered in the browser only). Settings gains
     "Change my answers" and "Find jobs now"; Home's start list opens the setup
     at the right card; the page header reads "Set up your search" while it is
     showing.
2. **One press finds jobs** (`POST /api/sources/refresh-all`). It runs, in turn,
   every source the per-source button would refresh and that is not paused by
   the person or by their markets, through that button's own work. No new
   collection policy; demo databases refused; refused while another refresh or
   a maintenance run is active; one source failing does not stop the rest and
   its exception text is not shown; Stop takes effect after the source in
   flight; new postings are then scored by the normal targeted rescore.
3. **Empty states with one sentence and one action**: Discover ("Find jobs now",
   toolbar hidden until there is something to filter), Applications ("Go to
   Discover", outside the list of columns so the board keeps its list
   semantics), Career Evidence ("Build your evidence bank" with "Import your
   CV", the empty import review not drawn, the hand-written form kept). Home
   drops the zero counters and the repeated gap list while the start list is
   open; a finished start-list step keeps its answer and loses its paragraph.
4. **Settings folded**: the 26 source cards sit under "Each job source (26, 1
   paused): status and refresh timing", which remembers whether it was open.
5. **Career Evidence's introduction**: the guidance sentence stays; the worked
   example and the "what this is used for" paragraph move under "Examples, and
   what this is used for".
6. **Faster configuration reads** (`career_agent/yaml_io.py`): YAML is parsed
   through libyaml's safe loader. Same safe constructor, same objects, same
   errors, no caching.
7. **Launcher words**: numbered stages that say whether they happen once;
   PowerShell's slow progress bar silenced; failures say nothing saved was
   changed and add the offline hint only when relevant; a port conflict says
   Career Agent is probably already open, where, and that the data is safe;
   start-up says what to do if the browser did not open; stopping says where the
   data is.
8. **Accessibility**: the evidence form's label follows the visible box.

## 4. Why each matters

* The setup turns "six things, in this order, none required" into the next
  question. A person always knows what is asked, why (the sentence under each
  title), and what comes next (the count and the dots). Nothing is a gate.
* Finding jobs was the product's whole purpose and had no front door. It is now
  one button in four places, and it reuses the per-source code paths exactly,
  so nothing about collection policy, politeness or permissions changed.
* Empty screens were the first screens. Each now says what the area is, why it
  is empty and the single best next step.
* Settings is a place people come back to; it now reads answers, preferences,
  search model, sources, with the long tail one click away.
* The eligibility question is the one a new person is most likely to get wrong
  and the one with the largest effect. Asking it plainly, separately from
  residence, protects the invariant that residence alone never proves
  eligibility.

## 5. Performance, before and after

Median of five direct handler calls after one warm-up; demo corpus (21
postings) with the worked-example configuration.

| Request | Before | After |
|---|---:|---:|
| `GET /api/profile` | 242 ms | 12 ms |
| `GET /api/preferences` | 270 ms | 7.5 ms |
| `GET /api/sources` | 274 ms | 10 ms |
| `GET /api/source-maintenance` | 261 ms | 21 ms |

Page height, same corpus, after sources loaded:

| Page | Before | After |
|---|---:|---:|
| Settings, 1366 px wide | 10,121 px | 1,888 px |
| Settings, 390 px wide | 10,367 px | 2,156 px |

Request counts per interaction were measured and are already minimal: loading
Discover makes three calls (`/health`, `/home`, `/jobs`), a sort change one, a
job detail one, Settings three. No duplicates.

A synthetic corpus of 3,150 postings (the demo postings, varied) was also
measured -- see section 9.

## 6. Resource footprint

Full method in [`RESOURCE_FOOTPRINT.md`](RESOURCE_FOOTPRINT.md). In short:
runtime environment 57.6 MB (development environment 142.7 MB); shared Python
62 MB and a shared uv cache that is not Career Agent's; backend ready in about
1.1 s, about 60 MB working set and 46 MB private, idle CPU effectively zero,
barely moving after Discover and a representative sequence of screens.

## 7. Accessibility

* An automated pass over every page -- fresh install and demo, 1280 and 390
  wide -- for unnamed controls, unlabelled fields, duplicate ids, images without
  alt and horizontal overflow. It found one real defect (the evidence form's
  paragraph box had no label, fixed) and now reports nothing.
* The setup: labelled fields, radio groups and checkbox groups in fieldsets with
  legends, focus on each card's heading, visible focus rings, errors associated
  with their field and announced, 44 px option rows, a phone-width single column,
  reduced-motion respected by the progress bar.
* Light and dark themes checked visually for every new component.

## 8. Remaining UX debt

* **Career Evidence** is still dense once it has content: the experiences
  workspace keeps "Needs organizing (0)", "Review imported groups (0 groups)"
  and "Organization history" visible at zero. Batch review and "next
  unreviewed" were not attempted in this pass.
* **"Source refresh status"** in Settings speaks in maintenance terms
  ("bounded maintenance command", "inventory", "deferrals"). A browser test
  reads its visible text, so folding it needs that test reconsidered.
* **Home after setup** is still the six-step list plus sections; it is shorter
  and no longer repeats itself, but a "what next" line would serve better than a
  checklist once the setup is done.
* **Discover cards** carry three chips (Search Fit, posting detail,
  eligibility). They are accurate; a first-time explanation of the three would
  help.
* **When the structured location is unresolved for a residence-only person**,
  the geography reason reads "The posting does not state where it hires" even
  when it did. That wording predates this pass.

## 9. Remaining performance debt

On a synthetic corpus of 3,150 postings:

| Request | Median |
|---|---:|
| `/api/jobs` (Discover, default) | 415 ms |
| `/api/jobs?search=engineer` | 711-738 ms |
| `/api/home` | 128 ms |
| `/api/jobs/<id>` | 14 ms |

The profile splits the list request between the facet tally (one statement over
every matching row, then Python), deserialising each card's full stored match,
and, with a search, five separate "hidden by ..." counts. This code has already
been optimised twice with measurements (fourteen statements to one; row objects
to tuples); the next step would be caching facets per configuration version
with explicit invalidation, which this pass deliberately did not take on.

## 10. Decisions deliberately deferred

* **Inert preferences.** `preferences.remote.accepted_work_models`,
  `require_remote` and `preferences.seniority.preferred` are saved and displayed
  but nothing matches, filters or ranks by them. The setup therefore does not ask
  about them (it asks which levels to hide, which does have an effect). Whether
  to wire them into scoring or remove them is a product decision.
* **Optional AI dependencies.** `openai` and `google-genai` are installed for
  everyone (about 18 MB) though AI providers are optional. Making them an extra
  changes installation for people who use them.
* **Facet caching**, above.
* **Measuring the combined launcher** (both apps in one process) needs a copy
  of the release folder, because the launcher writes `data/` in its own folder.

## 11. Tests and checks run

* **Full suites during the pass.** Career Agent unit + integration after the
  setup slice: 7,071 passed, 7 skipped; the three failures were two frontend
  line-length findings (fixed before committing) and the environment-dependent
  test named below. Career Agent browser after the setup slice: 274 passed,
  0 skipped (the Alpha 2 baseline of 266 plus the new setup tests).
* **Targeted re-runs after every later slice**, on final code: the setup,
  first-run, persona, maintenance, career, evidence, empty-state and source
  browser tests; the find-jobs integration tests; every config and YAML test
  (1,950 passed, 1 skipped); the localisation, catalogue, punctuation and
  frontend gates.
* **The final full re-run at the branch head was stopped by Claude Code**
  because the machine ran critically low on memory. The unit + integration run
  had reached 60% with 4,314 passed, 6 skipped and nothing failed; the browser
  run had not reported. They were not restarted. Re-run both before merging:
  `uv run python -m pytest tests/unit tests/integration` and
  `uv run python -m pytest tests/browser`.
* Resume Tailor Python: 22 passed; its ruff check clean. Its code and frontend
  were not changed, so its frontend tests and build were not re-run.
* `ruff check src tests scripts`, `ruff format --check` on every changed file,
  `mypy src` (210 files), the punctuation gate, the frontend hygiene gate, the
  localisation gate and the EN/PT catalogue parity tests.
* Open Code Review on each code slice and on the whole range. Findings acted on:
  a dead parameter and a missing double-submit guard in the setup, a progress
  poll outliving its card, a Home card that could never render, and the source
  fold snapping shut on redraw -- each fixed, the last with a test that fails
  without the fix.

Known, not caused by this pass:

* `tests/integration/test_live_runner.py::test_live_execution_without_an_explicit_database_is_refused`
  fails when the shell environment makes Rich colour and wrap CLI output; it
  fails identically on the untouched `05634ee` source, and passed in earlier
  runs in this session.
* `tests/browser/test_release_personas.py::test_complete_persona_journey[success-us]`
  timed out once waiting for the board and passed on two immediate re-runs.
* `tests/unit/test_docs_accuracy.py` is not `ruff format`-clean at HEAD; it was
  not touched.

## 12. Local commits

| Commit | Change |
|---|---|
| `f71bf77` | Parse configuration YAML with libyaml's safe loader |
| `95d2b83` | Add one-press job finding across refreshable sources |
| `3bbdaea` | Guide a fresh install through setup, one question per card |
| `8aa1926` | Say what the launcher is doing, and what to do when it cannot |
| `b199ae3` | Give empty Evidence and Applications one sentence and one way forward |
| `ddbc4c4` | Fold each source's card in Settings under one summary line |
| `dd9e729` | Label whichever evidence box is showing |
| `2cfb8f6` | Keep the source list open while a source is changed |

Plus the commit adding this document and `RESOURCE_FOOTPRINT.md`.
