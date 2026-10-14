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

Found by the browser suite on the new watcher (2026-10-14): `watchRescore`
answers a new listener at once, and Find jobs' notice subscribed before it
held its own handle, so a notice redrawn from that answer subscribed again
until the stack overflowed and the polling stopped. Fixed, and one listener's
error no longer stops the others. A start now waits for an answer about its
own run (by run id; two runs can start in the same second). `getHome` is no
longer shared between callers on the page: a visit after a write must read
after it, and the server already lets one Home read run at a time.

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

## Pre-integration confirmations (2026-10-14)

**Old scores keep these postings in both selections until recalculated.**
The default search and the deep selection read the eligibility stored with
each score. A readers-11 score says "eligible" for the Mattermost and
ElevenLabs shapes, so both stay in the default list and the deep selection
until the readers-12 recalculation re-reads them.
`tests/integration/test_eligibility_old_scores.py` pins the dependency and
what holds after the re-read: out of both selections (to confirm, or not
eligible for an "anywhere in Mexico" restriction), the stored score
unchanged, and still reachable from Saved and the applications board. Not
built, proposed:

* A (recommended): the readers-12 recalculation takes the postings whose
  stored score says eligible first. About 6,400 of them on the copy measured
  on 2026-10-13; at the measured rate (171,319 in 86 minutes) they are
  re-read in the first few minutes instead of anywhere in 86. No schema
  change, no cost per page view; it still needs the recalculation started.
* B: a gates-only re-read of those postings, kept in a side table keyed by
  posting, content hash and reading, read by both selections until the
  recalculation catches up. No recalculation needed, but a migration and a
  data-model decision; its cost was not measured.

**The availability check pauses for work in other processes too.** It was
refused to start, and stopped between requests, only when a collection or a
recalculation ran in the same app. A recalculation from the command line or
a second window (a live `rescore` run row) and a collection holding the
catalogue's lock from another process now count as well
(`CatalogueWriteLock.busy_elsewhere` probes and releases under the lock's
own guard). A stopped check keeps the answers it already had.

**Migrations.** 0054 and 0055 are `scope: catalogue`: the index and the
table are created in the shared catalogue only. Each profile database gets
the two ledger rows and nothing else.

PT/EN: `docs/product/pt-en-equivalence-proposal.md`. Sources:
`docs/product/source-leads-2026-10-14.md`.
