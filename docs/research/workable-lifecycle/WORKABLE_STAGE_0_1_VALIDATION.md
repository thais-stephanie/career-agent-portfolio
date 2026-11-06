# Workable lifecycle: validation of stages 0 and 1

Date: 2026-11-05, gate 5 correction 2026-11-06. Scope:
`WORKABLE_LIFECYCLE_IMPLEMENTATION_PLAN.md`, stages 0 and 1.
Activation and rollback: `WORKABLE_SHADOW_RUNBOOK.md`.

**Verdict: all pre-merge gates pass. Not active.** All three flags are off. No posting
is closed by this code, and stages 2 and 3 are not started.

## What was not validated

- **No real feed was read.** The rehearsal used real catalogue rows with synthetic
  feeds, so no number below predicts real churn, flap rate or backlog size.
- **The audit endpoints were never called.** `apply.workable.com` answers are mocked
  from the behaviour recorded in the audit. Their first live use is at activation.
  The sample's published and gone counts below come from that mock and mean nothing
  about Workable.
- **Stage 1 itself has not started.** The observation of 3 reads over 7 days needs
  a separate approval.
- **The real databases are not migrated.** They stay at 60 (main profile, catalogue)
  and 57 (second profile) until the app next opens with this code.

## Evidence

| Criterion | Result | Evidence |
|---|---|---|
| Infrastructure works | Yes | `tests/integration/test_workable_presence.py`; rehearsal |
| Shadow is non-destructive | Yes | Digest of `job` identical after 4 reads, the audit and rollback; tests with private rows |
| Operations are auditable | Yes | `workable_feed_read`, append-only `workable_shadow_event`, `workable_audit_sample`, `scripts/workable_shadow_report.py` |
| Backlog and new misses are apart | Yes | Gate 5 below |
| Other sources unchanged | Yes | Test with a non-Workable row; full regression |
| Migration is safe | Yes | Rehearsal below; upgrade-from-60 and rollback tests |
| Tests pass | Yes | Full regression below |

## Tests

Full regression, unit, integration and browser, on the final code, alone on the
machine: **13,852 passed, 8 skipped, 0 failed, 13,860 collected.** The previous `xfail`
for the reopen defect is now a normal passing test.

Two earlier full runs failed, and neither failure remains:

- The first, before the first commit, failed 5 tests, all caused by this work and all
  corrected: rowid-alias keys and a JSON column without a default (SQL portability
  guards), the new flags missing from the switches the suite clears, the HTTP `location`
  header (provider neutrality guard), and the expected table set of the resume store
  test. The keys are now ULID text, plus `seq` for the order of observation.
- The second, after the gate 5 correction, ran at the same time as the rehearsal and
  failed 5 tests that this work does not touch: a browser focus test, a browser persona
  journey, two browser-profile cleanup tests for PDF printing, and the 60 ms ceiling of
  `test_match_performance`. The first four passed when repeated. The performance test
  failed 1 of 3 runs on this branch and 1 of 3 on `main` without it, on a machine at
  about 90 % CPU, and can fail the same way on a busy machine. The run above replaced
  that one as the evidence.

## Mandatory protections

| # | Protection | How it holds |
|---|---|---|
| 1 | No posting closed | Nothing here writes `job`. Decisions are rows in shadow tables |
| 2 | No private data touched | Only the four tables of migration 0061 are written |
| 3 | Incomplete reads are not observations | `observe` runs only after a complete read with a readable version; 304, a cut or malformed transfer, Stop, budget and failure do not reach it |
| 4 | One version never counts twice | `version` is `UNIQUE`; a second read reports `IGNORED_ALREADY_RECORDED` |
| 5 | A reappearing posting resets | A feed listing clears `misses`, `first_miss_at`, `baseline` and the decision. A published answer from the sample clears the count and keeps `baseline` |
| 6 | Idempotent, resumable, Workable only | One transaction per version; rows filtered by `provider = 'workable'` |
| 7 | No score invalidation or revision bump | No trigger fires, since `job` is not written; dirty count and both revisions unchanged in the rehearsal |
| 8 | Activation starts no collection | Flags are read when a read ends. Tested |
| 9 | No historical closing | `WOULD_CLOSE` is a record only, with `BACKLOG` kept apart |
| 10 | The sample closes nothing | No answer changes `job`, and `UNKNOWN` is never a confirmation. Tested by population |

## Gate 5: backlog and new misses

The first version of the report mixed them per read, so the gate was only partly met.
The correction changes reporting, sampling and documentation. Migration 0061 is
byte-identical, and `job` is not touched. Because `observe` writes differently now,
**every gate was repeated** rather than reused.

Four synthetic reads over a copy of the real catalogue:

| Read | Outcome | Backlog absent | First misses | Would close, backlog / new | Reappeared, backlog / new | Sampled, backlog / new |
|---|---|---|---|---|---|---|
| 1 | `BASELINE` | 35,533 | 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| 2 | `COUNTED` | 35,533 | 1,300 | 35,533 / 0 | 0 / 0 | 50 / 0 |
| 3 | `COUNTED` | 35,533 | 1,300 | 0 / 1,291 | 0 / 9 | 25 / 25 |
| 4 | `COUNTED` | 35,493 | 0 | 0 / 1,300 | 40 / 0 | 25 / 25 |

- Read 2 has no new `WOULD_CLOSE`: a new miss needs a second counted read. All 50
  sample places went to the backlog, as the reallocation rule says.
- The flap rate of new misses is 9 of 2,600, without the backlog.
- Sample, eligible universe by read: backlog 35,533, 35,483, 35,418; new 0, 1,291, 2,566.
  Backlog selected and consulted 100; new 50. Mocked answers: backlog 10 published and
  90 gone; new 2 and 48. `UNKNOWN` 0. Requests 162, none inside a transaction, no real
  socket.
- Real closed Workable postings: 0 throughout.

## Rehearsal on a disposable copy of the real data folder

The copy (about 15 GB) was deleted afterwards. Sockets were blocked in every process.
The whole rehearsal ran alongside the full regression, so its times are pessimistic.

| Step | Result |
|---|---|
| Automatic backup before migrating, main profile | 386 MB. 466 s in the earlier rehearsal, 2,802 s under this load |
| Migration, main profile and catalogue 60 to 61 | 0.46 s |
| Migration, second profile 57 to 61 | 0.52 s |
| Open Workable rows | 139,349 |
| Older build `ba53276` on migrated files | Works; ignores the tables |
| Flags off | Writes nothing |
| Time of a read, machine busy | 30.87 s baseline; 18.82, 16.98 and 16.34 s counted |
| Time of a read, no tests running | 17.94 s baseline; 9.62 and 11.44 s counted |
| Other writers during a baseline read, machine busy | Waited about 30 s, then succeeded. A reader was served in 0.54 s |
| Hard kill during a counted read | Record unchanged, lock free in 0.02 s |
| Same version again | `IGNORED_ALREADY_RECORDED`, 0.0 s |
| Crash inside the migration | Schema stays 60, no tables |
| `job` digest `e05d851c5b22e438`, 281,609 rows, closed Workable 0, dirty 16,703, revision (733292, 794129) | Identical before, after migration, after every read and after the audit |
| Rollback | 0.11 s, schema back to 60, older build works |
| Migration applied again | Yes |
| `integrity_check` of the catalogue | ok. 562 s in the earlier rehearsal, 12,134 s under this load |
| `foreign_key_check` | 0 rows |
| `quick_check` of both profiles | ok; audit findings unchanged |
| Real files | Sizes and times unchanged |

Each read holds the catalogue write lock for its whole transaction. Writers wait up to
120 s. The baseline read is the slowest: about 18 s here, against about 11 s measured
before the correction, on a machine whose load was not controlled in either run.

## Deviations from the plan, for the owner to review

1. **Baseline read (added).** The plan's churn guard would hold every read: about a
   quarter of open rows already left the feed, and they would all look like first
   misses. So the first read evaluated skips that guard, and its absent rows are
   marked `baseline`. They later report `BACKLOG`, apart from `MISSED_REPEATEDLY`, and
   stay out of the flap rate.
2. **Reopen fix is not behind a flag.** The plan said it would be. It follows what
   every other source does, and the real catalogue holds 0 closed Workable rows, so
   it changes nothing there today.
3. **Four tables, not one.** `pipeline_run` is private to each profile, so it cannot
   be the shared ledger of feed versions.
4. **Three flags, not one**, so that the audit's network use can stay off alone.
5. **Time comes from the feed's `Last-Modified`**, not the clock of the read, so a
   late read cannot satisfy the 24 h rule.
6. **Two extra hold outcomes:** `HELD_FEED_EMPTY` and `HELD_OLDER_VERSION`.
7. **A held read still resets postings it listed**, counts no miss, and does not
   become the size reference. As a result, a lasting real rise in departures, or a
   long time with flags off, keeps reads held until the owner decides what to do. See
   the runbook: clearing the observations ends them and needs a new evaluation.
8. **Population counts live in `workable_feed_read.policy_json`.** No schema change was
   needed. The origin of each decision stays in the event's `reason`, and the
   population names are derived in the report. No new availability state exists.
9. **`WOULD_REOPEN` from the backlog now has the reason `REAPPEARED_FROM_BASELINE`.**
   It used to read `REAPPEARED`, like a new miss. No real events exist, so nothing was
   rewritten.
10. **A sampled posting is never sampled again**, in any read. So later answers come
    from postings not yet seen, and a posting's answer can grow old. The owner reads
    this against the 150 answers of plan §7, per population.
