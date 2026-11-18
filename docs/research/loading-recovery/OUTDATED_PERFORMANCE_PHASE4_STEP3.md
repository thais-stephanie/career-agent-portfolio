# Outdated count performance, Phase 4 Step 3: diagnosis, no safe change

Branch `claude/home-outdated-perf-phase4-step3`, cut from the published
`origin/main` (`9636ecf`, Step 2). **Verdict: NO SAFE IMPROVEMENT FOUND** within
the authorised scope (no index, no migration, no connection or cache change).
No product code changed. Two levers measured on synthetic data need the
owner's decision (section 7).

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

## 9. Git state

- Branch `claude/home-outdated-perf-phase4-step3` at `9636ecf` plus this
  document. Worktree `ca-outdated-perf4`, no upstream.
- Worktree purpose: Step 3 diagnosis and Step 4 measurement. Base `9636ecf`.
  Status UNDER REVIEW. It closes when the owner rules on section 7.
- `origin/main` = `9636ecf`; the local `main` (`fe8d946`) is untouched.

The scripts are `build_s3.py`, `exp.py` and `planparts.py`, kept in the
session scratch folder. The aggregated results are in this document.
