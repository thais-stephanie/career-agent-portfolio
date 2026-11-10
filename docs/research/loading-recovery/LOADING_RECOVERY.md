# Loading recovery: Resume Tailor, Home, the resumes library, the refresh bar

Branch `claude/perf-ux-recovery`, from `main` at `43c8569`. Not merged.

## What was measured before (owner's install, 2026-11-10, read-only probes)

| Read | Cold disk | Warm |
|---|---|---|
| `GET /api/jobs?group_duplicates=1&limit=8` (Tailor's "best" list, no `narrowings=later`) | 197.5 s | 2 to 9 s |
| the same list with `narrowings=later` | 13.5 s (server cache empty) | |
| `GET /api/jobs/narrowings` for that list (the "N more" counts alone) | 104.4 s | |
| Resume Tailor, Start, in the interface | about 150 s on "Loading..." | 9.1 s coming back |
| `GET /api/home` | 18.7 s | 1.6 s |

Every opening of the app asked `POST /api/auto-update/run`. The update declined
(recent), but the request counted as a write, so the remembered totals and Home
were read again from scratch.

## Causes and what changed

1. **Tailor asked the "N more" counts it never shows.**
   `static/js/tailor_page.js` sent both lists without `narrowings=later`, and
   `JobsApi.list_jobs` then counted every narrowing over the whole corpus.
   Both lists now ask `narrowings=later`, like the Jobs page. Tips
   (`static/js/tailor_tips.js`) asks its saved jobs the same way, for at most
   15 s, and draws the tips without the note when it does not answer.
2. **Every part of Start and Make waited for every read.** Start draws its first
   card and button at once; each other card (`data-part` = `waiting`, `made`,
   `evidence`) fills in when its own read answers. Make draws the pasted ad, the
   base and the rule once the library answers; only the job list
   (`data-part=jobs`) waits for the lists. A job chosen in the drawer is
   selectable before the list arrives.
3. **No limit, and failures drawn as empty lists.** Each read has a limit (job
   lists 45 s, with a "taking longer than usual" note at 8 s; the library and
   the career 20 s). Past it the request is cancelled in the browser with an
   `AbortController`. The server may still finish that work, and it remembers
   the totals, so Try again is usually quick. A failure or a time-out is said in
   its part, with Try again, in words for a read that failed. It is never drawn
   as "no saved jobs" or "nothing confirmed".
4. **Coming back read everything again.** Start forced both lists on every
   visit. Lists already held are now kept while the server's freshness token
   (`GET /api/jobs/freshness`, the one the Jobs page uses) is unchanged. A read
   in flight is shared, so quick tab switches ask once.
5. **A refusal counted as a write.** `LocalApp.handle_api` counted every
   request other than GET. A handler can now say its own request changed
   nothing (`server.changed_nothing`). Only the automatic update's refusals
   say so (`DEMO`, `DISABLED`, `RECENT`, `BUSY` before anything is written,
   `NOTHING_DUE`), and each of them only reads: the switch, the last start and
   result, whether anything runs, and `overdue`, a plan over the inventory.
   Once the start time is written, the request counts as a write, refused or
   not. The flag sits on a per-thread stack, so a nested call never borrows or
   clears it.
6. **Home asked its two reads one after the other.** `static/js/home.js` asks
   `/api/home` and `/api/firstrun` together; neither needs the other's answer.
7. **The empty resumes library did not say where a CV went.** The empty list
   says it holds the resumes made or imported here, and that the profile's
   files and confirmed experience are kept apart. When `GET /api/documents`
   answers for this profile, it adds how many documents and confirmed
   statements there are. A failed or slow read adds nothing, so the screen
   never says "none" when it does not know. There are buttons for Resumes home
   (where the Master is built, with its own confirmation), Import, and
   Documents. Nothing is created or moved by opening the list.
8. **"0 of 29" for minutes.** The one-line refresh bar now says a site is being
   read and for how long. An automatic run holds the site as an inventory key,
   not a readable name, so the bar does not name it.

## Profiles

Each local profile is served by its own `JobsApi`, so the server's remembered
totals and Home never cross profiles, and switching profile reloads every tab.
In the page, a read records the profile it was asked for, and its answer is
dropped if another profile is open when it arrives. A read asked before the
page knew its profile counts as the open one, because the server answered it
for that profile.

## Before and after, synthetic (demo jobs, synthetic profile, same machine)

Measured with `tests/browser` tooling. Tailor's two lists were held 5 s in the
browser, or not at all. Server work was counted in process. These numbers are
synthetic and are not production times.

| | Before (`43c8569`) | After |
|---|---|---|
| Start's first button, lists held 5 s | 4.77 s | 0.05 s |
| Start complete, lists held 5 s | 4.77 s | 4.5 s (the lists themselves) |
| Start again, lists held 5 s | 5.16 s | 0.05 s |
| Tailor list requests over 4 visits | 8 | 2 |
| Lists ask `narrowings=later` | no | yes |
| Population counts on the server, 4 visits (includes the Jobs page's own background list) | 37 to 39 | 21 to 23 |
| Home reads again across a refused update | 1 | 0 |
| List population counts again across a refused update | 4 | 0 |

## Not changed, and still open

- `list_jobs` still counts its total and facets over the whole population, even
  with `narrowings=later` (13.5 s cold on the owner's install). Tailor no longer
  waits for that, but it still costs the server. A lighter read would be a new
  contract. It is not added here.
- Home is one payload. It is no longer held behind first-run, but the payload
  itself (18.7 s on a cold disk) is unchanged. During a source refresh, each
  finished source still moves the freshness, and Home is read again. Reusing
  Home with a "last updated at" label during a refresh would need an owner
  decision.
- In the full refresh panel, an automatic run's current site is still shown as
  its inventory key (unchanged; only the one-line bar was changed).
- A request cancelled in the browser is not cancelled on the server.

## Tests

- `tests/integration/test_writes_counter.py`: refusals keep the counter, the
  freshness, the list totals and Home's answer; a real write, a started run and
  a failed request still count. It also covers nested calls and threads.
- `tests/browser/test_loading_recovery.py`: lists held, failed (500, 503),
  never answering (cancelled at the limit), fast tab switches, coming back with
  and without a change, an answer for another profile, Tips, Portuguese, Home's
  two reads, the empty library (documents, none, failure, with a Master), and
  the one-line refresh bar.

## Suite results on the final commit

- Unit and integration: 13,551 passed, 8 skipped, exit 0.
- Browser: 618 tests, 617 passed. The one failure,
  `test_release_personas.py::test_complete_persona_journey[retail-br]` (an
  onboarding step waiting 30 s), passes alone (exit 0) and touches no file
  changed here.
- An earlier browser run failed 8 tests in `test_loading_recovery.py`: other
  files leave their fetch spies installed for the session, and one keeps
  `window.__calls` as strings. This file's globals now carry an `__lr` prefix.
  The four colliding files, run in suite order, pass (exit 0).
- Ruff, ruff format, mypy on the changed server modules, and
  `scripts/frontend_check.mjs` (60 modules): clean.
