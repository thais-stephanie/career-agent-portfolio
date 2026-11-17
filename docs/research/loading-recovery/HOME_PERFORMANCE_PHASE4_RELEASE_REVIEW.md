# Home performance, Phase 4: release review

Date: 2026-11-17. Scope: the final safety review of Step 1 and Step 1b (the
Home deep-level snapshot and its part memo) before the owner decides what to
publish. No code changed in this review. Nothing was merged or pushed.

Candidates:

- **H** (Home only): `claude/release-candidate-home-perf4-only`, a fast-forward
  of `origin/main` (8a43fb0) carrying Step 1 and Step 1b only.
- **HG** (Home + Geography): `claude/integration-home-perf4-step1b`, H plus the
  two geography commits of another session (23d7051, fe8d946).

## 1. Verdicts

| Axis | Verdict |
|---|---|
| Performance | PARTIAL, MORE WORK NEEDED |
| Correctness | KNOWN RISK, OWNER ACCEPTANCE REQUIRED |
| Publication | BOTH OPTIONS AVAILABLE |

Recommendation: fix first (a small Step 1c, section 7), then publish H. Treat
geography as a separate owner decision.

## 2. Mixed epochs in the part memo

### 2.1 What the key cannot see

`_levels_key` stamps `deep_review` with `COUNT(*), MAX(created_at)`, plus the
contract, the context digests, the population token and the config. Two writes
change readings without moving that stamp:

1. **Same-second upsert.** `DeepRepo.save` replaces a reading in place
   (`ON CONFLICT DO UPDATE`, id kept). If that happens within the second of the
   newest save, neither the count nor the maximum moves.
2. **Digest backfill** (`deep-backfill-digests`). It rewrites `intent_digest`
   only where it is NULL. It is deterministic and has already run once on the
   real database (2026-10-19). A second run changes nothing.

Both blind spots exist on 8a43fb0 too: the whole-map cache uses the same key.
They are **pre-existing**. What Step 1b adds is the part memo, which can hold
levels from before and after such an event under one key.

### 2.2 Reproduction (synthetic, deterministic)

Corpus: 300 postings, read sets of 40, 20 and 10, and 200 clones. The first
Home sees half of the read postings (the rest are held out of the population).
Then the blind-spot event happens. A later Home admits the held postings.
Three answers are compared: Step 1b, base (8a43fb0's whole map) and truth (a
new process, everything derived after the event).

| Event | Levels moved | Memo: old epoch / new epoch | Step 1b vs base | Step 1b vs truth |
|---|---|---|---|---|
| Digest backfill | 145 | 65 / 46 | identical | identical |
| Same-second upsert | 238 | 122 / 64 | identical | 1 section reordered (also base) |
| Same-second upsert, held = shown cards | (targeted) | mixed | base is stale | identical |

Findings:

- The memo **does** hold two epochs under one key. This is reproduced.
- In every case, the visible Home answer was either the same as base or closer
  to truth than base. No section had an order matching neither base nor truth.
- The divergence from truth in the upsert case is the pre-existing staleness of
  the whole-map cache, not something Step 1b introduced.

### 2.3 Classification

- **Origin:** the blind spots are pre-existing. The mixed memo is new with
  Step 1b.
- **Requirements:** a same-second in-place resave of a reading while the server
  holds a memo, or a backfill over NULL digests. In practice, the first needs a
  Deep Reading run that reads an advert again with the same profile, contract
  and model inside one second of the newest save.
- **Probability:** low. **Impact:** one Home can show a stale level for some
  cards until the next key change (any new reading, scoring or config change).
  No data is written wrong; nothing persists.

### 2.4 Fixes without migration (proposed, not applied)

Both were tested in scratch against the current code: current fails, proposal
passes (4 tests).

- **P1, key sees attempts.** Every production save writes a `PUBLISHED`
  attempt in the same transaction, so `_levels_key` also stamps
  `SELECT COUNT(*), MAX(rowid) FROM deep_attempt`. Measured on the real
  database: 3.3 ms. This closes the same-second upsert.
- **P2, one epoch per part.** When a request brings postings not asked under
  the kept key, the part starts again and the whole request is derived. Cost:
  Step 1's cost, only when the population grows under one key. This closes the
  mixing for every event, including the backfill P1 cannot see.

Alternatives considered:

- An existing counter: `deep_attempt` is the one that already moves with every
  save (P1).
- `COUNT(intent_digest)` in the key: 48 ms on the real database, and it only
  covers the backfill, which no longer runs. Not worth it.
- Targeted invalidation from `DeepRepo.save`: it would not reach other
  processes (the CLI writes from another process). Rejected.
- A future migration (an `updated_at` or a version column on `deep_review`,
  bumped by a trigger) would make the stamp exact. Not needed now.

Regression tests to add with the fix:
`test_a_reading_replaced_in_the_same_second_moves_the_key` and
`test_one_home_never_mixes_two_epochs`.

## 3. H and HG

- H is a fast-forward from `origin/main`. Commits: 2afcb39, cee754a, 6c30137,
  eb829b0, 1727033, fe5f250, 4605059, ffe8064 (plus this document).
- The code difference from H to HG has the same patch-id as 8a43fb0..fe8d946:
  the geography commits are the only code difference.
- Files that differ from H to HG: `gates.py`, `preparation.py`, `i18n.js`,
  their tests, and docs.
- Geography changes only the reason text of an undecidable published location
  and the `geography_to_confirm` concern in preparation. No decision changes.
- Gate reasons are stored at scoring. There is no `MATCH_SCHEMA_VERSION` bump,
  so publishing HG forces no rescore. The new text appears only on postings
  scored after publication; older postings keep the old text until rescored.
  `preparation.py` compares the stored reason with the current one, so the
  concern follows the gate for both texts.
- Approval: geography was made and reviewed by another session. Its full-suite
  run there was inconclusive. Here, the HG suites passed.

### Git procedure (for the owner, not executed)

- **H only:** `git push origin claude/release-candidate-home-perf4-only:main`
  (a fast-forward on the remote). Local `main` (fe8d946) then merges
  `origin/main` later. No reset, no rebase.
- **HG:** local `main` (fe8d946) is an ancestor of the integration branch, so
  `git merge --ff-only claude/integration-home-perf4-step1b` on `main`, then
  push.

## 4. Suites

| Candidate | Unit + integration | Browser |
|---|---|---|
| H (ffe8064) | 13,657 passed, 8 skipped | 644 passed, 1 failed |
| HG (0d2e489) | passed (earlier run) | passed (earlier run) |

The H browser failure was `test_complete_persona_journey[admin-br]`, which
timed out on the onboarding `work` step. It passed 3 of 3 times alone and with
its whole file. H does not touch onboarding. It is the known onboarding flake.

Static gates on H: ruff, format, punctuation, frontend and l10n clean; mypy
shows only the 3 pre-existing errors.

## 5. Real read-only benchmark

### 5.1 Harness

A scratch harness opened the real databases only through `mode=ro` URIs, with
an authorizer denying writes, DDL, ATTACH/DETACH and state pragmas (TEMP tables
in memory allowed). The target was mandatory, with no fallback. It was
validated first on synthetic installs (every refusal, TEMP allowed, handlers,
protected file bytes unchanged). The app and `launch.py` were not started. No
migration ran. No payload was persisted: only timings, counters and hashes.

Codes: A = 8a43fb0, H, HG. Three alternating rounds; medians below.

### 5.2 Results

| Scenario | A | H | HG |
|---|---|---|---|
| First Home, new process (s) | 11.45 | 3.74 | 3.11 |
| Later Home, memo expired (s) | 1.28 | 2.41 | 2.29 |
| Home, new API, same process (s) | 5.66 | 1.96 | 2.16 |
| Home, whole map in memory (s) | 0.97 | 2.11 | 1.86 |
| Whole-map derivation, Find jobs (s) | 4.34 | 7.38 | 3.67 |
| Health alone (s) | 0.52 | 0.72 | 0.67 |
| Home beside health (s) | 5.66 | 2.41 | n/a |
| Page, wall clock (s) | 19.40 | 5.99 | 5.55 |
| Peak memory (MB) | 188 | 163 | n/a |

First Home details, A to H: Deep Reading assessments 2,367 to 262 (2,105
avoided, 89%); SQL statements 2,425 to 343; CPU 5.72 to 3.33 s.

Equivalence: every Home answer was identical between A, H and HG in every
scenario and round (normalised hashes; only `lists_at` and the health process
fields are dropped). No refusal or denial happened.

### 5.3 Limits

- The OS cache was warm (a full hash pass came first). This is not a cold disk.
- The machine was busy: 1.3 to 2.1 GB of free RAM, CPU at 27 to 76%. The
  first-Home rounds for A ranged from 4.9 to 12.8 s.
- The whole-map derivation is unchanged code; its difference is noise.
- The page gain is partly cache noise. In a separate run, `/api/sources` and
  the outdated check took the same time on A and H once warm (2.4 and 1.7 s).
  The real page gain is the Home portion: about 5.3 to 2.6 s.

### 5.4 What regressed

A later Home whose memo expired, or one with the whole map in memory, is about
1.1 s slower on H. SQL time goes from 1.22 to 2.36 s. The cost is
`ids_among` plus `reviewed_postings` in chunks of 500 ids, which H runs even
when the whole map is already cached.

### 5.5 The 10 s goal

The full Home page did not reach 10 s on A (19.4 s). It is under 10 s on H in
this warm, read-only measure (6.0 s), but that is not a cold first open in the
real app. The goal stays a future target until it is measured cold.

## 6. Integrity

Before and after the whole review:

- The sha256 of all 24 files is identical: both profiles' databases, the
  catalogue, the WALs, `profiles.json`, `config/*` and the untracked CSV.
- Schema, migration ledger, maximum rowids and auto-update state are equal.
- Only the SHM files' modification times changed (same sizes). This comes from
  read-only opens.
- Port 8765 is free and no harness process is left.
- `main` is fe8d946 and `origin/main` is 8a43fb0, both unchanged.

## 7. Recommended Step 1c (needs approval)

1. P1 and P2 from section 2.4, with their two regression tests.
2. Skip `ids_among` and `reviewed_postings` when the whole map is cached, and
   replace the 500-id `IN` chunks with a join on a TEMP table, to remove the
   1.1 s later-Home regression.
3. Measure again with the same harness, including one cold run after a reboot.

## 8. Next step for `/api/sources`

Not implemented here. `/api/sources` reads about 1,030 MB per call. The
11-16 diagnosis found:

- `_posting_facts` scans `job` in index order (799 MB). A plain table scan
  gives the same answer with 151 MB.
- `pipeline_run` history is read five times and decoded up to four times.

Plan: force the table scan for `_posting_facts`, read and decode the history
once per request, prove the answer is identical with hashes, and measure with
the same read-only harness.

## 9. What still needs the owner

- Accepting the mixed-epoch risk, or approving Step 1c first.
- Choosing H or HG, and approving the merge and the push.
- Geography as its own decision (another session's work).
- Any `/api/sources` change.
