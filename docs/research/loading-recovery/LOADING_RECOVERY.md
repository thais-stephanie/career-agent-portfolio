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
- Home's lists, counted without a collection running, still cost what they
  cost (18.7 s for the whole payload on a cold disk). The page no longer waits
  for them to draw (see the next section), but the read itself is unchanged.
- While a recalculation runs, Home still reuses its lists for a minute at a
  time (`HOME_RUNNING_REUSE_SECONDS`, unchanged by this branch).
- A collection run by another process, or by another profile over the shared
  catalogue, is not this profile's run: Home counts again on each change, as
  before.
- Home's sections list a posting the person hid (unchanged, also outside a
  run). Observed while writing the tests; not changed here.
- In the full refresh panel, an automatic run's current site is still shown as
  its inventory key (unchanged; only the one-line bar was changed).
- A request cancelled in the browser is not cancelled on the server.

## Home during a source refresh (owner decision, 2026-11-10)

Home is two reads (`WorkspaceRoutes._home`):

- **Its lists**: the total, the open postings and the digest's sections, over
  the whole catalogue (`_home_lists_now`). Remembered under a key of everything
  they depend on: the profile's database and configuration, when the person
  last looked, whether a recalculation runs, and `_freshness_parts`. The
  person's half of that (applications, hides and saves, readings, availability
  checks, every write the server accepted, the day) always counts. The
  catalogue's half (`population_token`) counts except while this profile's
  collection runs (`RetrievalRunner.running`: a find, a source refresh, an
  update on opening). Each finished source no longer makes the next visit
  count everything again.
- **Everything else**: the cards, the profile gaps, the questions, the latest
  refresh's own count. These are this person's rows and take milliseconds, so
  they are read on every request. Nothing the person does is hidden behind the
  kept lists.

The answer carries `lists_at` (when the lists were counted) and `collecting`
(asked last, so a run that started while the lists were being counted is
said). The page draws its first steps, the collection and the tip before
Home's answer, and says the lists are being counted. During a run it says
"Lists and counts as of {time}" and that new jobs are counted when the update
ends. When any run ends (done, stopped or failed), the page asks Home once,
quietly: the dashboard stays, says it is counting what arrived, and is
replaced when the answer comes. If that read fails, the last lists stay with
their time. The job list, its filters and an open posting are not touched
("Update the list" is unchanged). `/api/home/outdated` follows the same rule.
`api.getHome` shares a read in flight (Home and the rail both ask as the page
opens), but never one asked before a write the page sent.

### When a run ends during an answer (independent review, 2026-11-10)

The review of `4161e1a` found that a run ending in the middle of a request
could be answered as current with lists kept or counted while it ran: on the
server (the run ends after the lists were chosen, or while they are counted)
and in the page (the end of a run shared a read asked before it ended). Fixed:

- **Server** (`_home_lists_checked`): whether a collection runs is asked last,
  and THEN the catalogue's token. A run reports it is over only after its last
  write is committed, so that token covers all it wrote. Lists counted at that
  token are `lists_current: true`. Lists behind it, with no collection to
  explain it, are counted again once; still behind (something keeps writing),
  they are answered with their own `lists_at` and `lists_current: false`, and
  the page says "Lists and counts as of {time}." without calling them current.
  At most two counts per request. During a recalculation `lists_current` is
  `null`: the recalculation's own progress is what Home shows then.
- **Page**: the end of a run calls `api.forgetHome()` before anything asks
  Home again, so a read asked before the end is not shared with what asks
  after it; what asks after it shares the one new read. The rail draws only
  its latest read (an older answer arriving late is dropped); Home already did.
  The end of a recalculation Home follows does the same before Home reads
  again (found by the re-review: a Home read asked during the recalculation,
  such as the quiet count after a collection ended, was shared with the read
  the end asked, and drawn). `test_home_after_recalculation.py`.
- **Request bodies** (`api.js request`): a body read that fails is no longer an
  empty answer. Aborted (Stop, a time limit, a page left) it is an abort, which
  Resume Tailor's time limits turn into "did not answer in time"; cut off on a
  successful status it is a network failure; on an error status the status
  still decides.

No time-to-live was added. The existing 10-minute bound
(`HOME_MEMO_SECONDS`) still caps any remembered lists, during a run too, and no
new persistence was added.

### Before and after, synthetic

Six sources finishing during one run, Home read twice after each (12 reads);
Home's answer held 3 s in the browser for the opening. These numbers are
synthetic and are not production times.

| | Before (`93a3526`) | After |
|---|---|---|
| Home list counts during the run (sections read) | 12 (6 recounts) | 0 |
| Home list counts when the run ends | 0 (the last visit had already counted) | 1 recount (2 sections) |
| Mean Home read during the run | 29.7 ms | 11.3 ms |
| First section of Home drawn, answer held 3 s | 3.06 s | 0.12 s |
| Whole dashboard drawn, answer held 3 s | 3.08 s | 3.16 s |
| `/api/home` requests as the page opens | 2 | 1 |

On the owner's corpus a recount is seconds, not milliseconds; how much this
saves there has not been measured.

## Tests

- `tests/integration/test_writes_counter.py`: refusals keep the counter, the
  freshness, the list totals and Home's answer; a real write, a started run and
  a failed request still count. It also covers nested calls and threads.
- `tests/browser/test_loading_recovery.py`: lists held, failed (500, 503),
  never answering (cancelled at the limit), fast tab switches, coming back with
  and without a change, an answer for another profile, Tips, Portuguese, Home's
  two reads, the empty library (documents, none, failure, with a Master), and
  the one-line refresh bar.
- `tests/integration/test_home_during_refresh.py`: sources finishing in a row,
  no snapshot yet, a source running for minutes (and the 10-minute bound), a
  save, an application and a hide during the run, the latest refresh's count,
  a run that wrote nothing, failed and stopped runs, another profile,
  concurrent reads, a run that starts while the lists are counted, and the
  outdated count.
- `tests/browser/test_home_during_refresh.py`: the page before its numbers
  (one `/api/home` request), the "as of" line during a run, the end of an
  update (counted once, the page kept, the job list not asked again, a partial
  run said), a failed recount, an answer for a profile no longer open, and
  the Portuguese text.
- Review fixes. `tests/integration/test_home_during_refresh.py`: a run ending
  after its lists were kept, a run ending while they are counted, a catalogue
  moving during a count (counted again once) or moving on (twice at most, not
  called current), a recount failing after the run, and answers around the
  run's end, each coherent. `tests/browser/test_home_during_refresh.py`: the
  end of an update never reuses a read asked before it (and a late old answer
  is not drawn, in Home or the rail), a write after the end still asks again,
  and a real run on the test server's own runner, nothing intercepted.
  `tests/browser/test_request_failures.py`: abort before the headers, abort
  while the body is read, a body cut off, 500 and 503 (with a body cut off
  too), an empty body, and Resume Tailor's confirmed list whose body never ends.

## Suite results on the final commit

Run on the review fixes (`3150142`, clean tree), one suite after the other:

- Unit and integration: 13,575 passed, 8 skipped, exit 0 (46 min).
- Browser: 633 passed, exit 0 (30 min). No failure, flaky or not.
- Ruff, ruff format, mypy on `web/workspace_api.py`,
  `scripts/frontend_check.mjs` (60 modules), the localisation and punctuation
  checks: clean.

Run on the Home-during-a-refresh change, before its commit:

- Unit and integration: 13,569 passed, 8 skipped, exit 0.
- Browser: 624 tests, 622 passed, 2 failed.
  - `test_onboarding_v2.py::test_each_card_costs_a_bounded_number_of_requests`
    (a job-list read landing after its timer started) passes alone (exit 0).
  - `test_first_run_flow.py::test_a_six_digit_new_count_fits_its_circle`
    failed for a reason of its own: it patched `fetch` while the opening
    `/api/home` read was in flight, and the visit that followed now shares that
    read (nothing was written in between), so no answer passed through the
    patch. The test now waits for the dashboard before patching; its
    assertions are unchanged. The file passes (7 tests, exit 0).
- The previous round's runs: unit and integration 13,551 passed, 8 skipped;
  browser 617 of 618 (`test_release_personas.py::test_complete_persona_journey[retail-br]`
  passed alone). The `__lr` globals fix the collisions of an earlier run in
  `test_loading_recovery.py` (other files leave fetch spies installed).
- Ruff, ruff format, mypy on the changed server modules, and
  `scripts/frontend_check.mjs` (60 modules): clean.

## Phase 2: the job list is read where it is shown (2026-11-12)

The controlled real measurement of `494a862` (owner's install, auto-update
answered in the browser, nothing collected) left Home as the bottleneck. At
the first opening of the process `/api/home` took 143 s and, beside it, a
`/api/jobs` read took 142 s, `/api/health` 40 s, `/api/sources` 43 s and
`/api/home/outdated` 32 s. About 1.7 GB of RAM was free, so the disk cache was
not in a known state.

### What the opening asks, and who for

| Request | Asked by | When | Needed on Home |
|---|---|---|---|
| `/api/jobs` (Find jobs' list) | `main.js` `load()`, first paint | at once, on every page | no: only Find jobs and Applications draw it |
| `/api/jobs/narrowings` | `afterList()`, after that list | when the list answers, retried after 3 s | no |
| `/api/home` | `home.load()` and the rail (`loadRailReadouts`), shared in flight | at once | yes |
| `/api/firstrun` | `home.load()`, beside `/api/home` | at once | yes |
| `/api/health` | `showHealth()` | at once | yes: the mode banner and the revision notice are global |
| `/api/sources` | `loadRailReadouts()`, after `/api/home` | when Home answers | yes: the rail's attention status |
| `/api/home/outdated` | `fillOutdated()` | after the dashboard is drawn | the recalculation row only |
| `/api/retrieval?funnel=false` | `collection.refresh()` | at once | yes: whether a run goes |

Nothing outside Find jobs and Applications reads the list: the drawer reads
its own posting, `mergeJob` and `findJob` fall back to it, and
`exposureFor` records `elsewhere` when the decided posting is not in a list on
screen (more truthful than a rank in a list nobody saw).

The list was also read again, on Home, when a collection the person started
ended and when a recalculation ended.

### Change

`load()` returns at once on any page that is not Find jobs or Applications and
notes the list as due; `goTo()` reads it when one of the two pages opens, or
shows the list it holds (`showHeld`, which checks writes and freshness). One
guard in the function every caller goes through (fifteen call sites). Opening
the app on `#jobs` reads the list at once, as before.

### Synthetic contention (172,000 invented postings, `scripts/synthetic_volume.py`)

The real server over HTTP in one process, a fresh server per scenario (no
Home or aggregates memo), the OS cache warmed once, old and new opening in
A/B/A order. Synthetic times, not production ones.

| Scenario | `/api/home` | Server CPU | Wall |
|---|---|---|---|
| Opening with the list, round 1 | 1.62 s | 3.59 s | 1.62 s |
| Opening without it, round 1 | 1.21 s | 1.45 s | 1.21 s |
| Opening with the list, round 2 | 2.13 s | 5.84 s | 2.13 s |
| Opening without it, round 2 | 1.08 s | 1.31 s | 1.08 s |

Alone: `/api/home` 1.22 s, `/api/health` 0.31 s, the list 1.20 s,
`/api/sources` 0.91 s, `/api/home/outdated` 1.31 s, and
`/api/jobs/narrowings` **75.0 s** on its first read (7.3 s once the cache is
warm). During the list and its narrowings `/api/health` took 0.40 s; during
the narrowings `/api/home` took 1.41 s. Light reads are not held by a lock
(one connection per request, WAL readers): the cost is CPU and disk shared
with the heavy reads, which the larger real catalogue and a cold cache make
far worse. How much the change saves on the owner's install has not been
measured.

### Not changed, and why

- **Return to Home (43.8 s `/api/home`, 107 s `/api/home/outdated` on the
  real install):** both answers are remembered for `HOME_MEMO_SECONDS` (600 s)
  from the moment they were counted; a hit does not renew it, and the return
  came more than 600 s after the count. Renewing it on a verified-current hit,
  or lengthening it, changes the freshness policy: NEEDS OWNER DECISION.
- **`/api/health` and `/api/sources`:** whole-catalogue counts that feed the
  global banner and the rail. Remembering them is a new freshness rule: NEEDS
  OWNER DECISION.
- **Abandoned reads:** the server finishes a read the browser gave up on
  (`ThreadingHTTPServer` notices only when it writes the answer). Interrupting
  read-only SQL on a closed socket is a new mechanism, left out; the opening no
  longer sends the heaviest of those reads (the narrowings).
- **`/api/profiles` asked twice at the opening** (tens of milliseconds): left.
