# Outdated count performance, Phase 4 Step 3: covering indexes for the receipts

Branch `claude/home-outdated-perf-phase4-step3`, cut from the published
`origin/main` (`9636ecf`, Step 2).

Two stages:

- **Diagnosis, first pass** (sections 1 to 8, written first). No safe
  improvement exists without an index: every query rewrite was refuted. That
  pass ended on an owner decision (section 7).
- **The owner authorised the covering indexes on 2026-11-18** (option 1 of
  section 7). Sections 10 to 15 record the implementation, its tests, the
  review and the real measurement on a disposable copy.

**Verdict: VERIFIED WITH KNOWN RISKS.** The same answer everywhere. The first
read with its file out of the cache is 22% faster, and the warm read 11%
faster. The code requires migrations 0062/0063 (section 14).

## 1. The question

The real diagnosis (Phase 4, section 12) measured `GET /api/home/outdated` at
9.6 s on its first read and 2.8 to 3.2 s afterwards. `rescore.plan` is all of it,
and `_missing_or_stale` is 65 to 85% of the plan. The second query of
`_missing_or_stale` (open postings whose input revision has no receipt for this
configuration) took 7.2 s of the first read. Ordering the targets is under 0.1 s.

## 2. The path

`WorkspaceRoutes.home_outdated` (`src/career_agent/web/workspace_api.py`) does three things:

- returns the running pass's remainder while a recalculation runs;
- otherwise returns its memo, keyed on database, configuration, digest and the
  person half of the freshness key, for 600 s or until the population moves;
- otherwise returns `len(rescore.plan(conn, config).targets)`.

`plan` in TARGETED mode reads, in order:

1. the ledger (`job_dirty`, twice) and the profile's requests;
2. `_missing_or_stale`: two anti-joins (below);
3. `_open_counts`;
4. `_reading_moved` (scores read by an older reader);
5. `_open_scoreable` on the ledger ids;
6. `_eligible_first` (stored eligible statuses).

`_missing_or_stale`, as written:

```sql
-- open postings with no current score
SELECT j.id FROM job j
WHERE j.closed_at IS NULL AND j.content_hash IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM job_match m INDEXED BY idx_job_match_population
                  WHERE m.config_id = ? AND m.config_version = ? AND m.job_id = j.id
                    AND m.content_hash = j.content_hash
                    AND m.schema_version >= ? AND m.config_digest = ?)
ORDER BY j.id
-- open postings whose input revision has no receipt for this configuration
SELECT i.job_id FROM job_input_revision i JOIN job j ON j.id = i.job_id
WHERE j.closed_at IS NULL AND j.content_hash IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM job_score_revision r
                  WHERE r.job_id = i.job_id AND r.config_id = ? AND r.config_version = ?
                    AND r.revision = i.revision)
```

`job` and `job_input_revision` live in the shared catalogue. `job_match` and
`job_score_revision` live in the profile. A receipt is deleted and written
again every time a score is written (`trg_population_job_match_*`, then
`invalidation.receipt`), so the receipts' physical order follows the order of
the scoring passes. The product never runs `ANALYZE`, so the real databases
have no statistics: the plan depends only on the schema, and the synthetic
plan is the real one.

## 3. Synthetic corpus

The corpus is built in scratch by `build_s3.py` from a 2,000-posting base made
by `scripts/synthetic_volume.py` (invented data). The proportions are the
measured ones:

- 291,000 postings, 6% closed, 0.3% without text;
- an input revision for every posting, 30% of them revised later;
- three scoring generations: 124,000 rows, 217,000 rows and the current one;
- 625,259 scores, each with a receipt;
- the current receipts come from one large pass in id order, then 30% are
  rewritten by small passes in random order;
- 3% of the current receipts are stale, 2% of the open postings are unscored,
  1% of the scores were made on other content, and 2% were read by an older reader.

Profile 3.0 GB (`result_json` 2 KB, against 7.3 KB real), catalogue 304 MB.

`rescore.plan` on it took 0.97 to 1.32 s, of which `_missing_or_stale` was 0.78
to 0.83 s (60 to 86%). It selected 18,886 targets (13,752 missing or stale,
5,134 moved readings). That is the real profile, so the corpus reproduces the bottleneck.

## 4. Plans

Every variant below returned the same set (sha `76525d84fee8285f`, 11,126 ids)
as the query as written.

| Variant | Plan (after the open-index walk) |
|---|---|
| as written | `SEARCH j USING COVERING INDEX idx_job_open_population (closed_at=?)`, `SEARCH i USING INDEX sqlite_autoindex_job_input_revision_1 (job_id=?)`, `SEARCH r USING INDEX sqlite_autoindex_job_score_revision_1 (job_id=? AND config_id=? AND config_version=?)` |
| `INDEXED BY idx_job_open_population` on `j` | the same: the planner already drives from the covering open index |
| driven from the open index, `i` joined by key | the same |
| set difference (`EXCEPT`) of `(job_id, revision)` | two table scans, `EXCEPT USING TEMP B-TREE`, an automatic index on the result |
| one walk for both anti-joins (`NOT EXISTS ... OR EXISTS ...`) | one walk, three correlated subqueries |

The cost is inherent to the plan. For each of about 274,000 open postings it
makes two key probes, plus a row read in each: `revision` is in neither
table's key index, and both are rowid tables.

## 5. Measurements (synthetic, one new process per run, warm OS cache)

The machine carried 44% load from other sessions throughout, so single runs
vary by up to 2x. Reads are bytes asked of the OS. With the product's default
page cache (2 MB) the same pages are asked for again and again. "Distinct" is
the same run with a 2 GB page cache, which approximates the pages a cold read
must fetch.

| Variant | wall (s, 2 to 3 runs) | read | distinct |
|---|---|---|---|
| `changed`, as written | 0.50 to 0.96 (one 2.7 first run) | 642 MB | 109 MB |
| `INDEXED BY` open index | 0.41 to 0.82 | 642 MB | 109 MB |
| driven from the open index | 0.44 to 0.57 | 642 MB | 109 MB |
| `EXCEPT` | 0.58 to 0.86 | 449 MB | **370 MB** |
| both anti-joins, as written | 0.67 to 1.85 | 821 MB | 109 MB |
| one walk for both | 1.23 to 1.81 | 795 MB | 109 MB |

None reduces the dominant cost reproducibly. `EXCEPT` triples the distinct
pages (it scans every receipt, all generations included, and spills a temp
B-tree). The single walk saves one pass over the open index but runs the
subqueries less efficiently, and it is not faster. Counting without ordering
was already refuted on the real data (diagnosis, section 12.6).

## 6. What would work, measured on a scratch copy of the synthetic corpus

**(a) Covering indexes for the receipts** (a migration, not made). The indexes,
on a copy:

- `job_score_revision(job_id, config_id, config_version, revision)`;
- `job_input_revision(job_id, revision)`.

The planner does not choose them by itself (it keeps the autoindex), so the
query must name them with `INDEXED BY`, as `_missing_or_stale` already does for
`job_match`:

| | wall (s, 3 runs) | read | distinct |
|---|---|---|---|
| as written | 0.51 to 0.58 | 642 MB | 109 MB |
| covering indexes, `INDEXED BY` | **0.24 to 0.28** | **150 MB** | **75 MB** |

The same set, about half the time, and a third fewer distinct pages. On a cold
real disk those pages are random reads, so the saving would weigh more there.
The cost is two indexes (about 40 MB on the real profile and 15 MB on the
catalogue, estimated from row counts), and more work on every score write and
input revision.

**(b) A larger page cache per connection** (a connection setting, not made).
`PRAGMA cache_size = -65536` (64 MB, for the life of one request's connection)
on the whole `plan`:

| | plan wall (s) | read |
|---|---|---|
| default (2 MB) | 0.89 / 1.68 | 511 MB |
| 64 MB | 0.69 / 0.91 | 164 MB |

That is 20 to 45% less time and a third of the reads, with no change to the
answer. It touches every connection's memory ceiling, which is a product
decision, not a query rewrite.

## 7. Owner decisions (NEEDS OWNER DECISION)

1. Whether to add the two covering indexes (a migration on both databases)
   and name them in `_missing_or_stale`. Expected: the receipts query about 2x
   faster. On the synthetic corpus that query is about half of the plan, so
   the outdated count would be about 25% faster warm, and probably more cold
   (not measured).
2. Whether connections may use a larger page cache. Expected: fewer re-reads
   everywhere, not only here. It needs its own measurement of memory with two
   profiles open.

Neither is needed for correctness. The count stays memoised for 600 s, so it
is paid on the first Home of a session and after the population moves.

## 8. Not done, by the brief

No index, migration or connection setting was changed in the product. No real
database was opened in this step: every plan and measurement above is
synthetic. Phase F (the real A/B) applies only to an implemented change, so
it did not run.

## 9. Git state at the end of the first pass

The branch was at `9636ecf` plus the documents (`b369b78`), with no product
change. Section 15 has the final state.

## 10. Implementation (after the owner's authorisation)

- `src/career_agent/storage/migrations/0062_job_input_revision_cover.sql`
  (`scope: catalogue`) creates `idx_job_input_revision_cover ON job_input_revision (job_id, revision)`.
- `src/career_agent/storage/migrations/0063_job_score_revision_cover.sql`
  (`scope: profile`) creates
  `idx_job_score_revision_cover ON job_score_revision (job_id, config_id, config_version, revision)`.
- `rescore._missing_or_stale` names both with `INDEXED BY`, as it already
  names `idx_job_match_population`. Without the names the planner keeps the
  primary keys and reads the rows (section 6). Nothing else in the query
  changed: its WHERE text, joins and parameters are byte for byte the old ones.
- Additive only: no data moves, and no other query, contract, cache or
  freshness rule changed.
- Three tests that list the migrations learnt 62 and 63. The 0061 upgrade test
  now migrates to 61 exactly, as its subject is 61.

Commits: `d778d38` (the change), `50e8b91` (review nits).

## 11. Equivalence tests

`tests/integration/test_rescore_receipt_indexes.py` (51 tests) covers:

- **The plan.** Both probes are `COVERING INDEX` on the new indexes, with no
  autoindex in the plan. The old query does not use them, so the test fails
  if a hint is silently ignored. The SQL the product sends (by trace) carries both hints.
- **Index columns.** Pinned.
- **The receipts rule, through the product's own `_missing_or_stale`:**
  - no receipt;
  - an older revision;
  - the right revision under another version or another configuration;
  - the current receipt;
  - the input moving on;
  - closing.
- **Random states.** Forty random states, mixing:
  - closed and textless postings;
  - deleted scores and scores of other text;
  - moved readings;
  - current, older and newer-version receipts, and other configurations;
  - revised inputs, and deleted inputs and ledger.

  In each state the new and the legacy `_missing_or_stale` are equal; the raw
  query is equal for two versions; and the whole `plan` is equal (targets,
  order, breakdown, priority, dirty, counts) in TARGETED and DIRTY modes.
- **Edges.** Empty and all-closed corpora, and a full pass leaving nothing.
- **Split install (two profiles on one catalogue).**
  - Each index exists only on its own side.
  - The plan is covering on both profiles.
  - The plan is the same before and after a pass.
  - `/api/home/outdated` itself answers the same as with the old query.
- **The upgrade from 61.**
  - Rows unchanged, the same targets.
  - `[62, 63]` applied once.
  - An older build opens the result with nothing to do.
  - Rollback (drop both indexes and their ledger rows) runs the old code, and
    migrating again restores them.
  - This build's code on an unmigrated database raises "no such index".

**Mutations.** Five mutants of the new query, each caught:

| Mutant | Tests failed |
|---|---|
| revision ignored | 41 |
| a newer version's receipt certifies | 26 |
| closed postings kept | 28 |
| textless postings kept | 12 |
| any configuration certifies | 27 |

A first run let the second mutant survive. The pinned checks used the
test's copy of the SQL, and the random states had no newer-version receipts.
Both were fixed before the run above.

## 12. Independent review

A separate reviewer read the diff, the migration runner, the split
(`catalogue.py`, `catalogue_split.py`), retention, backup and every reader
that may open a database without migrating. Result: no blocker and no should-fix.

- Equivalence: `INDEXED BY` only restricts the access path. Every column is
  NOT NULL, and both keys are unique.
- Scopes: right.
- A second profile on a migrated catalogue applies only 0063.
- A single database at 63 that is later split keeps each index on its own side.
- No code compares index sets.
- Every reader that runs `rescore.plan` migrates first: `serve` and the
  profile switch migrate, and `rescore --no-migrate` is refused while
  migrations are pending. The read-only connections never plan.

Two nits were fixed in `50e8b91`: a test of `/api/home/outdated` itself on
split profiles, and a single random roll for the ledger branch.

## 13. Benchmarks

**Synthetic** (section 3 corpus; A = `9636ecf` unmigrated, B = this branch
migrated; alternating, one new process each):

| | A | B |
|---|---|---|
| plan, wall (4 rounds, first and again) | 0.94 to 1.98 s | 0.66 to 1.42 s |
| `_missing_or_stale` | 0.78 to 1.69 s | 0.52 to 1.22 s |
| bytes read by the plan | 511 MB | 265 MB |
| distinct pages | 164 MB | 131 MB |
| targets (ordered list hash, priority) | `5288917ad35e3a44`, 553 | the same |
| `/api/home/outdated` (handler, 2 rounds) | 0.98 to 1.98 s | 0.69 to 1.41 s |
| receipt rewrite cost, 20,000 receipts | 0.13 to 0.16 s | 0.15 to 0.24 s |
| migration on the 291k corpus | | 5.6 s, once |

**Real data, on a disposable copy.** A copy of the real install's databases
and configuration was made in the session scratch folder (`xcopy /J`, unbuffered).
The live files were only read. The copy was migrated with this build (0062
and 0063 took **3.35 s**; the profile grew 31.5 MB and the catalogue 11.5 MB).
On the copy, the old query's plan still uses the primary keys (checked with
`EXPLAIN`), so A on the migrated copy runs exactly as A on an unmigrated one.

The harness is the Phase 4 read-only one:

- `mode=ro`, a write-denying authorizer, TEMP in memory;
- guards on the product's `connect` and `attach`;
- the copy treated as a synthetic root, so the live install is never opened.

Its self-test passed for A and B. Answers and targets are compared by hash
only, and no posting id is written to a log.

Warm (3 rounds alternating, a new API per call, the copy's pages partly cached):

| | A | B |
|---|---|---|
| endpoint wall | 0.91 to 1.46 s | 0.89 to 1.46 s |
| `_missing_or_stale` | 594 to 1,017 ms | 528 to 882 ms |
| read | 339 MB | 312 MB |

**File out of the cache.** Each run used a fresh unbuffered copy, so none of
its pages were in the file cache, and the copy was deleted after the run.
Two rounds, in the order A, B, B, A, on an idle machine (CPU 0 to 3%):

| | A | B |
|---|---|---|
| endpoint, first call | **13.53 s, 12.96 s** | **10.64 s, 10.20 s** (-22%) |
| `_missing_or_stale`, first call | 8.85 s, 8.41 s | 5.66 s, 5.49 s (-35%) |
| endpoint, next calls (warm) | 0.84 to 0.88 s | 0.75 to 0.79 s (-11%) |
| thread CPU, first call | 4.7 to 4.8 s | 3.8 to 4.0 s |

This is a file that the operating system has not cached, not a cold boot: the
interpreter, the imports and the warm-up corpus were warm.

In every real run the answer was `4fdc0cfdab2a5432` and the targets were
`d5b2f86922cd3e8a` (21,769 targets, 477 eligible first). That answer is the
same as the live install gave in Step 4. There were no errors, refusals or denials.

## 14. Residual risks

- **The code requires 0062/0063.** On a database without them `plan` raises
  "no such index". Every product path migrates before planning, and the only
  way to get there is a catalogue file swapped behind its profile by hand,
  which the existing `INDEXED BY idx_job_raw_chars` (0054) is exposed to as
  well. A rollback must revert the code with the indexes.
- **Writes.** Each receipt write and input revision now maintains one more
  index: a few microseconds per receipt, against seconds of scoring per page.
- **Disk.** About 43 MB on the real install, growing with scores and postings.
- **The gain is moderate.** About 3 s off a first, uncached read of about 13 s,
  and about 0.1 s warm. The rest of the plan (the first anti-join,
  `_reading_moved`, `_open_scoreable`) is unchanged and now dominates.
- **The memo is unchanged**, so a warm Home still answers outdated from it.

## 15. Gates and Git state

Gates on `50e8b91`, one pytest session at a time, each with its own basetemp:

| Gate | Result |
|---|---|
| new tests (`test_rescore_receipt_indexes.py`) | 51 passed |
| rescore, migration and split tests (10 files) | exit 0 |
| Home and profile tests (9 files) | exit 0 |
| ruff check, ruff format | clean, 859 files formatted |
| mypy | 3 errors, the same three as on `9636ecf` (`evaluation/ranking.py` twice, `web/export.py`) |
| frontend, localisation, punctuation | exit 0 |
| unit and integration (13,763 collected) | exit 0, no failure, 8 skipped |
| browser (645 collected) | exit 0, no failure |

Real data integrity, before and after every real step (the copy, the warm
runs, the out-of-cache runs):

- the SHA-256 of the 24 database and WAL files is identical;
- the logical state is identical: schema, ledger, counters, rowids;
- WAL files are at 0 bytes;
- only the three SHM mtimes moved;
- `jobspy_resultados.csv` is intact;
- no process remained and port 8765 is free.

All copies of the real data were deleted after measuring.

Git state:

- Branch `claude/home-outdated-perf-phase4-step3`:
  - `9636ecf` (published);
  - `b369b78` (first-pass documents);
  - `d778d38`;
  - `50e8b91`;
  - this document.

  No upstream, not pushed. The worktree `ca-outdated-perf4` is UNDER REVIEW;
  it closes when the branch is published or discarded.
- `origin/main` = `9636ecf`; the local `main` (`fe8d946`) is untouched.

**Publication recommendation:** ready for integration review. Publish it the
way Steps 1c and 2 were published, from a candidate cut at the branch head
and pushed by explicit refspec. The first start after the update applies
0062/0063 in about 3.4 s. Nothing else is needed.

## 16. Publication (2026-11-18)

The preflight passed:

- the candidate is `8ec7263` and the worktree is clean;
- `src`, `tests` and the migrations are the tested tree (only documents
  follow `50e8b91`);
- `origin/main` (`9636ecf`) is its ancestor, and the remote had not moved;
- no geography commit is included.

Migration safety was checked on every path that plans:

- `serve` and a profile switch migrate first (`cli_local` `serve`, `web/profiles.py` `_build`);
- the CLI opens through `_open_personal`, which migrates;
- `rescore --no-migrate` is refused by `jobs_export.preflight` while any
  migration is pending, which covers a second profile still at 61;
- auto-update plans on the served profile's own app.

No normal path runs the receipts query before 0062/0063.

The candidate `claude/release-candidate-outdated-perf4-step3` was pushed to
`refs/heads/main` by explicit refspec, a fast-forward `9636ecf..8ec7263`.
Only `refs/heads/main` moved among the 19 remote heads. GitHub's comparison
lists exactly `b369b78`, `d778d38`, `50e8b91` and `8ec7263`, and 11 files.
The local `main` stays at `fe8d946`.

Publishing did not install anything. The live checkout and its databases are
unchanged (schema 61). The upgrade of the live install, with its blocker (the
checkout's divergent `main`), backup, migration, checks and recovery, is in
`STEP3_OPERATIONAL_UPGRADE.md`. It has not been run.
