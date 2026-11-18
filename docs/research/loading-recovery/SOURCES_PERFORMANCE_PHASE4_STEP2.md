# Sources performance, Phase 4 Step 2: single-pass provider aggregation

Branch `claude/sources-perf-phase4-step2`, cut from the published `origin/main`
(`de8e69a`). Code A is `de8e69a`; code B is this branch. Nothing here is merged
or pushed. No migration, index, cache or data change.

## 1. Cause

`GET /api/sources` calls `sources.health.health()`, which read the whole
catalogue `job` table three times per request:

1. `_posting_facts`: all postings per provider (count and newest
   `first_seen_at`). SQLite chose `SCAN job USING INDEX sqlite_autoindex_job_2`
   (the `UNIQUE(provider, external_id)` index) to deliver rows already grouped,
   then fetched every table row in index order to read `first_seen_at`. On the
   real catalogue that is a random-order walk of the whole table: 70 to 79% of
   the request in the Phase 4 diagnosis.
2. `catalogue._counts_by_provider`: open postings per provider, a second read
   of `job` through `idx_job_open_population`.
3. `catalogue._producing_boards_by_provider`: boards with at least one
   posting, a join of `source_board` with `job` and a `COUNT(DISTINCT)`.

## 2. Contracts preserved

Every field of the answer keeps its meaning and value:

- `postings` stays the count of all postings, open and closed.
- `last_new_posting` stays `MAX(first_seen_at)` over all postings, closed included.
- `collected` stays the open count, absent provider read as 0. Zero still
  downgrades `LIVE_INTEGRATION` to `CONFIGURED` with the same reason, and
  coverage to `PLANNED`.
- `boards_with_postings` stays `None` (not 0) for a provider without a producing board.
- Keys stay `str(provider)`, so a NULL provider is still `"None"`.
- `_posting_facts` raised on a SQL error, and the new pass raises too.
  `_counts_by_provider` and `_producing_boards_by_provider` still swallow
  `sqlite3.Error` into `{}` on their own path.
- A `job` table without `closed_at` (old shape) is answered exactly as before,
  by falling back to the two original queries. The fallback fires only on
  `OperationalError` "no such column", so a lock or I/O error is still raised.
- `catalogue.resolve()` called without `open_counts` (from `matrix.py`,
  `auto_update`, `maintenance.py`, `cli_local`) runs its original query.

## 3. Original SQL

```sql
-- health._posting_facts
SELECT provider, COUNT(*) AS n, MAX(first_seen_at) AS newest FROM job GROUP BY provider
-- catalogue._counts_by_provider
SELECT provider, COUNT(*) AS n FROM job WHERE closed_at IS NULL GROUP BY provider
-- catalogue._producing_boards_by_provider
SELECT sb.provider AS provider, COUNT(DISTINCT j.source_board_id) AS n
FROM source_board sb JOIN job j ON j.source_board_id = sb.id GROUP BY sb.provider
```

## 4. Optimized SQL

```sql
-- health.PROVIDER_FACTS, replaces the first two
SELECT provider, COUNT(*) AS n, MAX(first_seen_at) AS newest,
       SUM(CASE WHEN closed_at IS NULL THEN 1 ELSE 0 END) AS open_n
FROM job NOT INDEXED GROUP BY provider
-- catalogue.PRODUCING_BOARDS
SELECT sb.provider AS provider, COUNT(*) AS n FROM source_board sb
WHERE EXISTS (SELECT 1 FROM job j WHERE j.source_board_id = sb.id)
GROUP BY sb.provider
```

`health()` passes the open counts to `resolve(..., open_counts=...)`. Only
providers with `open_n > 0` enter the map, so an absent provider is read as 0,
exactly as `counts.get(provider, 0)` read the old map. `COUNT(*)` over
`EXISTS` equals `COUNT(DISTINCT j.source_board_id)` over the join because
`source_board.id` is the primary key: each board counts once, and a NULL id
matches no posting in either form.

## 5. Plan explanation

`EXPLAIN QUERY PLAN`, identical on every synthetic corpus and on the real catalogue:

| Query | Plan |
|---|---|
| old facts | `SCAN job USING INDEX sqlite_autoindex_job_2` |
| old open counts | `SEARCH job USING INDEX idx_job_open_population (closed_at=?)`, `USE TEMP B-TREE FOR GROUP BY` |
| old producing | `SCAN sb USING INDEX sqlite_autoindex_source_board_2`, `SEARCH j USING COVERING INDEX idx_job_source_board (source_board_id=?)`, `USE TEMP B-TREE FOR count(DISTINCT)` |
| new facts | `SCAN job`, `USE TEMP B-TREE FOR GROUP BY` |
| new producing | `SCAN sb USING INDEX sqlite_autoindex_source_board_2`, `CORRELATED SCALAR SUBQUERY 1`, `SEARCH j USING COVERING INDEX idx_job_source_board (source_board_id=?)` |

`NOT INDEXED` makes the facts query read the table once, sequentially, and sort
only one small row per posting (provider plus two values) in a temp B-tree. The
old plan saved that sort but paid a random table lookup per posting. The brief
asked not to presume `NOT INDEXED` is always better, so it was measured on
corpora where the index looks favourable (3 providers, 38 boards) and where
the closed share is 90%. It wins in every corpus at 100k postings and above.
At 30k postings both are under 0.35 s and the first-call difference is noise
(section 8). The producing probe stops at the first posting of each board,
in the covering index, instead of reading every posting and deduplicating.

## 6. Equivalence tests

`tests/integration/test_sources_provider_facts.py` (with `tests/unit/test_source_health.py`, 36 tests):

- Pinned shapes that pass on code A and on code B:
  - empty corpus;
  - one open posting;
  - open and closed postings;
  - closed only (`CONFIGURED`, `PLANNED`);
  - NULL `first_seen_at`;
  - providers no source declares, NULL included;
  - configured and producing boards (`None` kept);
  - exact identifier match.
- The whole nested `/api/sources` answer, B against the legacy path (the two
  original queries), on a demo database with:
  - an all-closed provider;
  - an idle board;
  - RUNNING, OK and FAILED runs and per-board history;
  - a paused source.

  The same comparison runs after the catalogue changes between requests, with
  two profiles sharing one catalogue, and for a `job` table without `closed_at`.
- The number of statements that read `job` drops by exactly one, and the plan
  of the new pass is `SCAN job` with no index.
- `resolve()` alone equals `resolve()` reached through `health()`.
- The producing probe equals the old join on edge cases (NULL ids, idle boards,
  boards of other providers) and on the demo, with a plan on `idx_job_source_board`.
- An error that is not a missing column is raised as before.

No value was normalised: the answers compare byte for byte.

## 7. Independent review

A separate reviewer with no access to this reasoning read the diff, the
consumers of each value and the schema. It found no behavioural difference on
the real schema, no blocker and no should-fix. It ran 35 tests. Three nits:

1. The fallback caught every `sqlite3.Error`, so a transient lock would have
   been answered by the slower path instead of raised. Fixed in `66b1315`
   (only "no such column"), with a test.
2. The equivalence of the grouping assumes `job.provider` compares as BINARY,
   which the schema declares. Documented in the docstring.
3. The temp B-tree of the `NOT INDEXED` pass grows with the number of
   postings (tens of MB at 300k). Kept as a residual risk (section 12).

## 8. Synthetic benchmarks

Corpora built from `scripts/synthetic_volume.py` (invented data), reshaped in a
scratch copy. Wall time in seconds, bytes read by the process, one new process
per run, both orders. The answer sha is identical A = B on every corpus and
call, and `same_rows` is true for each query pair.

| Corpus | Call | A wall | B wall | A read | B read |
|---|---|---|---|---|---|
| 300k postings, 25 providers, 40% closed, 23k boards, 3,000 runs | first | 4.27 | 0.88 | 2,288 MB | 318 MB |
| same | again | 4.33 | 1.08 | | |
| 300k, 90% closed | first | 2.68 | 0.68 | 1,734 MB | 333 MB |
| same | again | 3.89 | 0.54 | | |
| 300k, 3 providers, 38 boards | first | 3.41 | 0.51 | 1,920 MB | 162 MB |
| same | again | 3.72 | 0.58 | | |
| 100k | first | 0.99 | 0.34 | 636 MB | 68 MB |
| same | again | 1.30 | 0.13 | | |
| 30k | first | 0.30 | 0.34 | 51 MB | 13 MB |
| same | again | 0.068 | 0.043 | | |

On the 300k corpus, `_posting_facts` (2.2 to 2.5 s) plus
`_counts_by_provider` (1.1 s) became one `_provider_facts` (0.24 to 0.45 s).
The producing probe went from 0.31 to 0.16 s on first call and was even on
later calls. Statements per request went from 19 to 18, and `job` reads from 3 to 2.
Peak process memory stayed at 77 to 80 MB.

## 9. Real benchmarks

Read-only harness derived from the Phase 4 one: `mode=ro` URIs, an authorizer
that denies every write, TEMP in memory, connection guards on the product's
`connect` and `attach`, explicit paths, no fallback. A synthetic self-test ran
for A and B before the real run. It refused every write form on both
databases plus `ATTACH` and `PRAGMA journal_mode`, and the protected bytes were
unchanged. The real run used no `launch.py`, migration, collection, rescore or
LLM. It ran three rounds, A and B in separate processes, in the order
A B, B A, A B. Each process warmed up imports on a synthetic corpus, then
measured four `/api/sources` calls on the real install.

Median and range over the three rounds, wall in seconds:

| Call | A wall | B wall | A CPU | B CPU | A read | B read | A job reads | B job reads |
|---|---|---|---|---|---|---|---|---|
| first in new process | 1.82 [1.14..12.69] | 0.33 [0.32..0.34] | 1.83 | 0.33 | 1,030 MB | 184 MB | 3 | 2 |
| again (2nd) | 2.44 [2.16..2.91] | 0.32 [0.32..0.35] | 2.41 | 0.33 | 1,030 MB | 184 MB | 3 | 2 |
| again (3rd) | 2.87 [2.59..2.88] | 0.33 [0.32..0.34] | 2.73 | 0.33 | 1,030 MB | 184 MB | 3 | 2 |
| new API, same process | 2.64 [2.54..2.88] | 0.33 [0.33..0.71] | 2.61 | 0.33 | 1,030 MB | 184 MB | 3 | 2 |

Per function (median ms, later calls): `_posting_facts` 1,500 to 1,900 and
`_counts_by_provider` 450 to 500 became `_provider_facts` about 210, and
`_producing_boards_by_provider` went from 220 to 250 down to 36 to 39.
Statements per request: 22 to 21. Errors, refusals and denials: 0 in every process.
The normalised answer hash was `8834e2fb319a11d8` for all 24 measurements, A
and B. No value needed normalising.

The 12.69 s outlier is A's first call of the first round, the first read of
the catalogue after hours idle. It is not presented as a cold-disk number: the
operating system had part of the file cached and the share is unknown. Every
other call ran with warm pages. The A numbers are lower than in the Phase 4
diagnosis (4.8 to 5.5 s) because that measured a busier machine and the whole
Home load. The A/B comparison here is same-machine and interleaved.

Environment (`machine.log`): 5.0 to 5.3 GB RAM free and CPU at 0 to 9% between
processes, with one 49% sample between A round 2 and A round 3. No other
project Python process ran, and port 8765 was free.

## 10. CPU, I/O and memory differences

- CPU per request: from 1.8 to 2.7 s down to 0.33 s (about 87% less), almost
  all of it SQLite time.
- Bytes read per request: 1,030 MB down to 184 MB (82% less). The sequential
  table read replaces random page reads in index order.
- Writes: 0 bytes in both.
- Peak process memory: 88 MB for A, 90 MB for B. The temp B-tree of the
  `GROUP BY` costs about 2 MB on the real catalogue.

## 11. Impact on other consumers

- `health()` is also called by `source_refresh.py` (twice), `cli.py` and
  `cli_local.py`. They get the same values faster, through the same code.
- `resolve()` without `open_counts` (`matrix.py`, `auto_update`,
  `maintenance.py`, `cli_local`) is unchanged.
- `_producing_boards_by_provider` has one query for every caller, now the
  probe. Its output was compared with the old join (section 6).
- The full unit, integration and browser suites pass (section 13), Home and
  profile tests included.

## 12. Residual risks

- The temp B-tree of the facts pass grows linearly with postings (tens of MB at
  300k). At today's size it is about 2 MB. It is bounded by SQLite's temp
  store, with no cache kept between requests.
- If `job.provider` ever got a non-BINARY collation, the grouping of the new
  pass and of the old index could differ. The schema declares BINARY.
- At very small catalogues (30k postings or fewer) the gain is within noise.
- The real speed-up was measured on the current catalogue only. The synthetic
  corpora cover the shapes the real one does not reach.
- `/api/home/outdated`, `pipeline_run` history and the frontend load order
  remain as they were (out of scope).

## 13. Gates and exit codes

Run on `66b1315`, one pytest session at a time, each with its own `--basetemp`:

| Gate | Result |
|---|---|
| sources and catalogue tests (9 files) | exit 0, 4,639 passed |
| provider, collection and progress tests (16 files) | exit 0, 1,125 passed |
| Home and profile tests (10 files) | exit 0, 160 passed |
| ruff check | clean |
| ruff format --check | 858 files already formatted |
| mypy | 3 errors, all pre-existing on `de8e69a` (`evaluation/ranking.py` twice, `web/export.py`), none in changed files |
| frontend check | clean |
| localisation check | clean |
| punctuation check | clean |
| unit and integration (13,700 collected) | exit 0, no failure or error, 8 skipped |
| browser (645 collected) | exit 0, no failure or error |

No suite needed a repetition.

## 14. Git state

- Branch `claude/sources-perf-phase4-step2` in its own worktree, with no
  upstream so a plain push cannot target `main`:
  - `25d5cb8` perf(sources): one pass over `job`;
  - `23e0d21` perf(sources): one probe per board;
  - `66b1315` fix(sources): narrow the fallback;
  - plus this document.
- `origin/main` = `de8e69a`, unchanged.
- The local `main` (`fe8d946`, unpublished geography) and every geography
  worktree are untouched.

Real data integrity, before and after the real run:

- the SHA-256 of the 24 database and WAL files is identical;
- the logical state is identical: schema, migration ledger, counters and
  maximum rowids;
- WAL files stay at 0 bytes;
- only the three SHM mtimes moved (size 32,768 unchanged), as read-only WAL
  access does;
- `jobspy_resultados.csv` is intact;
- no orphan process remained.

## 15. Publication recommendation

Ready for integration review. The answer is identical and the request is about
7 to 8 times faster on the real install, with 82% fewer bytes read. To publish,
the owner would authorize the same release path as Step 1c: a candidate cut
from `origin/main` with these commits, pushed by explicit refspec, without
touching the local `main`.
