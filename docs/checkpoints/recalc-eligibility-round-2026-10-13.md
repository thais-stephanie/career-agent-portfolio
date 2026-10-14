# Recalculation follow-up, eligibility to confirm, pay, tiles and ad availability (2026-10-13)

Branch `claude/recalc-eligibility-round`, from `8197c72`. Not merged.

## The recalculation the owner ran

171,319 scores were recalculated in 86 minutes, 0 errors, every page committed
as it went; the run finished well. What failed was the account of it:

* every Home redraw started one more 700 ms poll loop (14,635 progress requests
  in 86 minutes), and ten missed polls ended a loop for good;
* the Find jobs notice kept the count read when the page opened
  (`/api/health` `stale_scores`), so it showed the starting count throughout;
* each visit to Home during the run recomputed everything, because every
  committed page moved the cache key.

Now: one watcher per page (2 s while running, backing off and reconnecting on
misses), a server account in one vocabulary (`web/rescore_state.py`: RUNNING,
RUNNING_ELSEWHERE, DONE, FAILED, CANCELLED, INTERRUPTED, IDLE) read from the
runner and from the run's own row, so a restart reads INTERRUPTED with what was
saved; a second start refused also when another process is writing; Home
reused for a minute at a time while a run goes and counting what is left from
committed scores; the notices read again when a run ends, well or not; no
"takes a few minutes" (the last measured duration is shown instead).
`jobs_scored` is counted after each page commits: a failed page used to be
counted as saved.

## Speed

Measured on a copy of the owner's databases, base vs branch in fresh processes
under the same cache pressure: `/api/home` 0.63 s -> 0.34 s warm (the outdated
count, the slowest part on a cold disk, is asked apart, `/api/home/outdated`,
and the page draws without it); two simultaneous Home requests share one read.
Deep coverage 23.4 / 26.7 s -> 10.8 / 12.7 s under pressure, same counts, from
an index holding each advert's length (migration 0054, catalogue, about 33 s to
build once).

## Eligibility (readers-12)

* Two explicit location statements that disagree are "conflicting
  locations", both quoted (`code`, `conflict` on the gate). Seen at the source on
  two postings that said "should be based in Brazil" and, under Location,
  "can be executed from anywhere in Colombia / Mexico".
* General remote prose ("Work from anywhere" among perks) beside a published
  place that excludes the candidate is "to confirm" only when the posting is
  REMOTE, as ADR-0034 already says. Widening it to office postings (measured on
  the copy: 214 of 6,404 eligible, offices in London, Austin, Basel) broke
  invariant 3 (an office address is not a hiring scope) and was reverted; it is
  an owner decision, not made here.
* A U.S. security clearance the role may require, or export-controlled
  information (EAR/ITAR), is a requirement to confirm, never a refusal; a hedged
  configured blocker ("may require") no longer refuses.

## Contract type

A score made before the engagement was read (schema below 14) files under "Not
read yet (recalculate)" in the filter and claims nothing on the card; the card,
the table and the filter read the same value.

## Pay and Salary sort

A STATED worldwide hiring scope takes the USD period default; a band said to be
for U.S. candidates keeps its sentence ("U.S. range"). Salary sorts yearly and
monthly pay in one currency (the one filtered on, the pay preference's, or
USD), lower bound first; hourly, other currencies and no pay last.

## Tiles

Pay, Job type, Where and Level hold the fact in bold and short qualifiers; the
explanations open in one area under the four (`aria-expanded`,
`aria-controls`). Where names two places and "+N places"; the header offers the
rest.

## Ad availability

`docs/AVAILABILITY.md`. Migration 0055 (catalogue table).

## The flaky test

`test_sites_working_again_is_said_once_confirmed` waited for /Update .../ on the
whole card, which "Update your match scores" satisfied before the sources had
answered; 4 of 12 runs failed, every failing run with no failed answer from
`/api/sources`. The waits read the sites row now; a regression pins it.

## After integration

Migrations 0054 and 0055 change the shared catalogue (an index, a table) and
add ledger rows to every profile database. `READER_IDENTITY` moves to
`readers-12`: one recalculation re-reads every score (the last one took 86
minutes); until then the old scores stay, marked as made by earlier rules.
