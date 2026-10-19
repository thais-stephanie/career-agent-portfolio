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
* **A board gone for a day is switched off.** `SourceBoardRepo.mark_not_found`
  writes `NOT_FOUND since <first 404>: ...` into `last_error` and sets
  `active = 0` once the board has answered 404 for 24 hours
  (`NOT_FOUND_RETIRE_HOURS`). A 404 recorded before this change (no date)
  counts as old enough. Nothing is deleted, no job is closed, and
  `sync_registry` is additive, so it never switches a board back on.
  `collect` stats gain `boards_retired`.
* **A cut Workable transfer keeps its complete records.** Byte resume is not
  possible: Workable ignores `Range` (measured 2026-10-11, and the feed sent
  no headers within 15 s on 2026-10-18). Instead `download_feed(...,
  keep_partial=True)` leaves the partial file on a dropped connection or a
  short body, and the shared walk stages it exactly like a feed that did not
  parse to its end: complete records stored, the run FAILED (shown as
  "updated in part"), nothing closed, `processed_last_modified` untouched, the
  file discarded. A Stop or an expired budget still leaves no file.

## Verified

* New tests: `tests/unit/test_repositories.py` (three on retirement),
  `tests/integration/test_collect.py` (retired through a real run, nothing
  closed), `tests/integration/test_source_freshness.py` (404-only run does not
  hold the family; any other failure still does),
  `tests/integration/test_workable_update.py` (dropped connection keeps what
  arrived; a refused connection keeps nothing). The old "truncated download
  changes nothing" test now asserts the new behaviour, on purpose.
* ruff, ruff format, mypy, punctuation gate: clean.
* Full suite: 7 failures, all reproduced on `main` without this change. They
  come from two variables in the owner's Windows environment,
  `CAREER_AGENT_ELIGIBILITY_SOURCE` and `CAREER_AGENT_WORK_MATCH_SOURCE`
  (Phase C / Phase D switches), leaking into tests. With both unset, all 7
  pass, including the browser persona journey.

## Not done

* Not run against the real database. On the next refresh after the merge,
  each of the eleven boards is retired on its first 404 (their old errors
  have no date), and Ashby, Greenhouse and Lever run normally.
* The shipped `companies.yaml` still lists the eleven boards; a fresh install
  registers them and retires them a day later. Worth marking them inactive
  there.
* A board that 404s while it still holds open jobs keeps those jobs open (as
  before: a 404 is never read as "every job closed").
* Tests read the owner's environment variables. A `conftest` that clears
  `CAREER_AGENT_*` would make the suite green on this machine.
* No automatic retry of the Workable download in the same run.
