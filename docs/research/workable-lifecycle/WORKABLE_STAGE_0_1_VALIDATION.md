# Workable lifecycle: validation of stages 0 and 1

Date: 2026-11-05. Scope: `WORKABLE_LIFECYCLE_IMPLEMENTATION_PLAN.md`, stages 0 and 1.
Activation and rollback: `WORKABLE_SHADOW_RUNBOOK.md`.

**Verdict: ready for review. Not integrated and not active.** All three flags are
off. No posting is closed by this code, and stages 2 and 3 are not started.

## What was not validated

- **No real feed was read.** The rehearsal used real catalogue rows with synthetic
  feeds, so no number below predicts real churn, flap rate or backlog size.
- **The audit endpoints were never called.** `apply.workable.com` answers are mocked
  from the behaviour recorded in the audit. Their first live use is at activation.
- **Stage 1 itself has not started.** The observation of 3 reads over 7 days needs
  a separate approval and integration.

## Evidence

| Criterion | Result | Evidence |
|---|---|---|
| Infrastructure works | Yes | `tests/integration/test_workable_presence.py`; rehearsal |
| Shadow is non-destructive | Yes | Digest of `job` identical after 3 reads and after rollback; tests with private rows |
| Operations are auditable | Yes | `workable_feed_read`, append-only `workable_shadow_event`, `workable_audit_sample`, `scripts/workable_shadow_report.py` |
| Other sources unchanged | Yes | Test with a non-Workable row; full regression |
| Migration is safe | Yes | Rehearsal below; upgrade-from-60 and rollback tests |
| Tests pass | Yes | Full regression below |

## Tests

Full regression, unit, integration and browser, on the final code: **13,827 passed,
8 skipped, 0 failed, 13,835 collected.** The 59 tests of the two Workable files are
included. The previous `xfail` for the reopen defect is now a normal passing test.

The first full run failed 5 tests, all caused by this work and all corrected:
rowid-alias keys and a JSON column without a default (SQL portability guards), the
new flags missing from the switches the suite clears, the HTTP `location` header
(provider neutrality guard), and the expected table set of the resume store test.
The keys are now ULID text, plus `seq` for the order of observation.

## Mandatory protections

| # | Protection | How it holds |
|---|---|---|
| 1 | No posting closed | Nothing here writes `job`. Decisions are rows in shadow tables |
| 2 | No private data touched | Only the four tables of migration 0061 are written |
| 3 | Incomplete reads are not observations | `observe` runs only after a complete read with a readable version; 304, a cut or malformed transfer, Stop, budget and failure do not reach it |
| 4 | One version never counts twice | `version` is `UNIQUE`; a second read reports `IGNORED_ALREADY_RECORDED` |
| 5 | A reappearing posting resets | `misses`, `first_miss_at`, `baseline` and the decision are cleared |
| 6 | Idempotent, resumable, Workable only | One transaction per version; rows filtered by `provider = 'workable'` |
| 7 | No score invalidation or revision bump | No trigger fires, since `job` is not written; dirty count and both revisions unchanged in the rehearsal |
| 8 | Activation starts no collection | Flags are read when a read ends. Tested |
| 9 | No historical closing | `WOULD_CLOSE` is a record only, with `BACKLOG` kept apart |

## Rehearsal on a disposable copy of the real catalogue

The copy (9.53 GB) was deleted afterwards. No network request was made.

| Step | Result |
|---|---|
| Migration 60 to 61 | 0.42 s |
| Open Workable rows | 139,349 |
| Read 1, `BASELINE` | 35,533 absent, 11.08 s |
| Read 2, `COUNTED` | 1,300 new misses, 35,533 would close, 13.80 s |
| Read 3, `COUNTED` | 1,300 new misses, 9 reappeared, 1,291 would close, 10.60 s |
| File growth after 3 reads | About 20 MB, plus a WAL of about 17 MB |
| Rows | 3 reads, 139,349 presence, 74,966 events, 100 audit sample rows, none asked |
| `job` digest, row count 281,609, closed Workable 0, dirty 16,703, revision (733292, 794129) | Identical before, after the reads, and after rollback |
| Rollback | 0.08 s, schema back to 60, `quick_check` ok, no foreign key violations |
| Migration applied again | Yes |

Each read holds the catalogue write lock for its whole transaction, about 10 to 15 s
here and up to about 28 s in an earlier run. Writers wait up to 120 s.

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
   long time with flags off, keeps reads held until the owner clears the tables and
   takes a new baseline. That is deliberate.
