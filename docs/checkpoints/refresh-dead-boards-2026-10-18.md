# Refresh: dead boards and the cut Workable feed (2026-10-18)

Branch `claude/refresh-dead-boards`, from `main` at `0ba924f`. Not merged,
not pushed. No migration, no schema or score change, no rescore needed.

## What happened

"Refresh all available sources" on 2026-10-18 (20:58 to 23:03 UTC) read 24
sources and reported four as not refreshed: Ashby, Greenhouse, Lever and
Workable. Two unrelated causes.

### Ashby, Greenhouse, Lever: never asked

1. Eleven registered boards answer HTTP 404 and have since at least
   2026-10-16 (`ashby:cribl`, `kraken`, `marqeta`, `spring-health`;
   `greenhouse:bitso`, `cybereason`, `hubspot`, `postman`, `sage`, `teach`;
   `lever:varomoney`). Confirmed live on 2026-10-18. None was ever collected
   (`last_collected_at` NULL), so none holds a job.
2. A failed board never gets `last_collected_at`, so source maintenance kept
   choosing them as the most overdue work. The automatic pass at 20:56 asked
   for exactly these eleven, each in a `collect` run of its own, and all
   eleven ended FAILED.
3. A family's state is read from its LATEST `collect` run (and from the shared
   `source_health` row written by the same run). Those single-board runs
   became the latest word on all three families: FAILED, so a one-hour
   cooldown (`FAILURE_COOLDOWN_HOURS`).
4. The full refresh started two minutes later. `_plan` skips any source in
   cooldown, so roughly 260 healthy boards (131 Ashby, 132 Greenhouse, 35
   Lever, minus the eleven) were never asked.

### Workable: the server cut the download

`collect-workable` ran and ended with `CONNECTION: feed transfer interrupted
after 847118336 bytes (RemoteProtocolError)`. The feed is ~850 MB, so the cut
came near the end, and `download_feed` deleted the whole file. The same
happened on 2026-10-16 at 442 MB; a retry that day succeeded.

## What changed

* **A run that only met 404 boards says nothing about the family.**
  `progress.gone_boards` counts NOT_FOUND failures per family;
  `_family_history` and `public_health._slices` skip a family slice in which
  every attempted board answered 404. Any other failure (timeout, 5xx,
  refusal) still puts the family on hold as before.
* **A board persistently gone is switched off.**
  `SourceBoardRepo.mark_not_found` keeps a streak in `last_error`:
  `NOT_FOUND since <first 404> (<n>x): ...`. The board is set `active = 0`
  only after at least 3 consecutive 404s (`NOT_FOUND_RETIRE_COUNT`) spanning
  at least 24 hours (`NOT_FOUND_RETIRE_HOURS`). Any other error or any
  success overwrites the streak. An error with no date (written before this
  change, or unreadable) starts a new streak: the clock never starts before
  a 404 this code saw. Nothing is deleted, no job is closed, and
  `sync_registry` is additive, so it never switches a board back on.
  `collect` stats gain `boards_retired`.
* **The eleven dead boards left the shipped registry.** `config/companies.yaml`
  drops ten companies whose only board was dead and Teach For America's
  Greenhouse board (its two Workday boards stay): 283 companies, 285 boards.
  A note at the top of the file records what was removed and why.
* **A cut Workable download is retried, then kept in part.** Byte resume is
  not possible: Workable ignores `Range` (measured 2026-10-11; on 2026-10-18
  the feed sent no headers within 15 s). So a cut transfer is asked for again
  from the start: 3 attempts, the second after 30 s and the third after 2 min
  (`DOWNLOAD_ATTEMPTS`, `DOWNLOAD_BACKOFF_SECONDS`). Only a dropped or slow
  connection or a 5xx is retried; a 4xx or a 429 refusal is not. The wait
  checks Stop and the time budget every second. If every attempt is cut,
  the LONGEST partial file is staged exactly like a feed that did not parse
  to its end: complete records stored, the run FAILED (shown as "updated in
  part"), nothing closed, `processed_last_modified` untouched, the files
  discarded. `download_feed(..., keep_partial=True)` is what leaves a cut
  file on disk; a Stop or an expired budget still leaves none.
* **The feed is processed as a stream; nothing needed fixing.** The download
  is written to disk in 64 KiB chunks; `xml_records` uses `iterparse` and
  clears the root after every `<job>`; records are staged into an on-disk
  SQLite file and read back 200 at a time. Memory does not grow with the
  feed. What does grow with the CORPUS is two lookup dicts the update builds
  once per run (`_own_rows`: 115,904 Workable rows, ~20 MB of strings;
  `_held_external_ids`: 88,373 ids). Tens of MB, bounded by what is stored,
  not by the 850 MB file: not worth a change now.
* **Tests no longer read the owner's product switches.** `tests/conftest.py`
  removes `CAREER_AGENT_ELIGIBILITY_SOURCE`, `CAREER_AGENT_WORK_MATCH_SOURCE`,
  `CAREER_AGENT_DB` and `CAREER_AGENT_ALLOW_UPGRADE_WITHOUT_BACKUP` at import,
  before any fixture or test server inherits the environment.
  `tests/unit/test_env_isolation.py` fails if `src/` ever reads a
  `CAREER_AGENT_*` name that is not on that list.

## Verified

* Full suite, clean environment, 2026-10-19: **13,388 passed, 6 skipped,
  0 failed** (41 min, browser suite included).
* The seven tests that failed on this machine on 2026-10-18 pass with both
  switches still SET in the shell, through `tests/conftest.py` alone.
* New or changed tests:
  * `tests/unit/test_repositories.py`: a 404 starts a dated streak; 3 over a
    day retire; 2 a day apart and 9 within a day do not; an undated,
    unreadable or broken streak starts over.
  * `tests/integration/test_collect.py`: retired through a real run, nothing
    closed.
  * `tests/integration/test_source_freshness.py`: a 404-only run does not
    hold the family back; any other failure still does.
  * `tests/integration/test_workable_update.py`: a cut download is retried
    and a whole second attempt is a full update (30 s waited); three cuts keep
    the longest partial's complete records and leave no file (150 s waited);
    403, 404 and 429 are not retried; a dropped connection keeps what arrived;
    a refused connection keeps nothing. The old "truncated download changes
    nothing" test asserts the new behaviour, on purpose.
  * `tests/unit/test_env_isolation.py`: every `CAREER_AGENT_*` name in `src/`
    is cleared for the suite, and none is set while tests run.
* ruff, ruff format, mypy, punctuation gate: clean.

## Not done

* Not run against the real database. After the merge, each still-registered
  dead board in an existing install starts a streak on its next 404 and is
  retired after its third 404 at least a day after the first. Until then a
  404-only run no longer holds its family back, so the refresh is unaffected.
* A board that 404s while it still holds open jobs keeps those jobs open (as
  before: a 404 is never read as "every job closed").
* A cut Workable download costs up to three full downloads (~2.5 GB) plus
  2.5 min of waiting before the partial result is used.

## Real validation (2026-10-19)

`main` fast-forwarded to `6922de8` (not pushed). The app, open since the day
before on the old code, was restarted the way the shortcut starts it, and
"Refresh all available sources" was triggered through its own route.

* **26 of 26 planned sources finished in 77.9 min** (2026-10-18: 24 planned,
  4 not refreshed, 124.9 min). Only LinkedIn was left out, still waiting out
  the 24-hour refusal cooldown from 2026-10-18 22:04, unrelated to this fix.
* **Ashby, Greenhouse and Lever ran normally.** Ashby 131 of 135 boards, 300
  new; Greenhouse 131 of 138, 1,218 new; Lever 35 of 36, 877 new. Every
  failure was a 404: the eleven known boards plus `greenhouse:pantherlabs`,
  newly dead. None held its family back. The extra boards are 11 registered
  that day by employer board discovery.
* **The eleven dead boards**: still the same eleven rows, still active, each
  now `NOT_FOUND since 2026-10-19T14:0x (1x)`. None was re-created or
  retired; retirement needs 3 such answers over 24 h. `cybereason` keeps its
  2 open jobs.
* **Workable**: one request, 849,612,138 bytes, 167,985 records, OK in
  217 s, 2,062 new, 526 changed. No cut, so the retry and the partial path
  were not exercised live (they are covered by tests).
* **Closures**: 1,397 on Ashby, Greenhouse and Lever boards that were read
  in full this run (960 of them `lever:jobgether`, an aggregator board that
  listed 4,238 and holds 4,238 open). The mass-drop guard held
  `greenhouse:webflow` (0 listed). No closure on a board that failed. 10
  more (Speedrun 9, Jobicy 1) were closed by `fold_sighted` at the start of
  the rescore: aggregator twins of an employer's open posting, by design.
* Open jobs 198,873 -> 205,292. The targeted rescore of 18,463 postings was
  running at the end of this check, 0 errors so far.

## Second real validation (2026-10-19, 15:19 to 16:52 UTC)

No code or data was changed; no timestamp was forced and no 404 simulated.
"Refresh all available sources": 26 of 26 sources in 80.6 min, 6,055 new;
the targeted rescore scored 16,928 postings with 0 errors.

* **The rule, as written and as stored**: retire when the streak in
  `last_error` (`NOT_FOUND since <first> (<n>x)`) reaches 3 or more AND at
  least 24 h separate the first 404 from the current one. A successful read
  sets `last_error = NULL` (`mark_collected`), so the next 404 starts at 1x;
  any other error overwrites the streak the same way.
* **Each 404 is a real request.** The eleven boards went 1x -> 3x in this
  window because the app's own hourly auto pass (`find-jobs` kind `auto`,
  15:18:49) ran source maintenance 27 s before the full refresh, and
  maintenance selected exactly those eleven (never successfully collected,
  so oldest first). `pantherlabs`, collected before, was not selected and is
  at 2x. First 404s 14:07 to 14:13, latest 16:21 to 16:27, 2.2 h apart:
  every board stays active, as the 24 h condition requires. The earliest
  possible retirement is the first 404 after 2026-10-20T14:13Z.
* **That auto pass is the live proof of the original fix**: eleven 404-only
  runs, and the full refresh 27 s later still planned and ran Ashby (131 of
  135), Greenhouse and Lever.
* **No board recovered** and no new board started a streak; none was retired.
* **Closures (161)**: 116 by the collector on boards read in full this run
  (Greenhouse 77, Ashby 28, Lever 7, Teamtailor 2, Workday 1, Recruiterflow
  1); 3 by `fold_sighted` at the start of the rescore (Jobicy, aggregator
  twins of an employer's open posting); 0 by any other mechanism; 0 on a
  board that failed. 5 previously closed postings reopened (listed again).
  `cybereason` keeps its 2 open jobs.
* **Observed, not a defect**: until the eleven are retired, the hourly
  maintenance pass spends its whole selection on them and finds nothing.
  Retirement ends that by itself.
