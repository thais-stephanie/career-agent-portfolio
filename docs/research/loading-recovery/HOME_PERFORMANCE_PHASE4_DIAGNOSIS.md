# Phase 4: Home performance, diagnosis only (2026-11-13)

Branch `claude/home-perf-phase4-diagnosis`, from `main` at `8a43fb0`.
Diagnosis only: no production module, test or contract was changed. Every
measurement below ran on SYNTHETIC corpora built in a scratch folder by
`scripts/synthetic_volume.py`; no real database was opened, copied or read,
not even read-only. The real numbers quoted come from the browser-side
measurement of 2026-11-13 (Phase 3 validation), which recorded request timings
only.

## Verdict after the real read-only profiling (2026-11-16)

**REAL DIAGNOSIS COMPLETE** (section 12). The synthetic verdict below is kept
as it was written; the real data overturned two of its conclusions:

- **Home's dominant cost is CPU, not disk:** deriving the deep-reading level of
  every one of the 2,127 read postings (`deep_api._review_rows` ->
  `deep_assess.assess`, 2,367 assessments) takes 54% of a first Home
  (13.5 of 25.0 s) and 81-86% of a repeat (7.0-10.2 of 8.6-11.8 s). Only 244
  of those postings can appear in Home's sections. Deriving the levels for
  those alone gave the **identical Home answer** in 6 of 6 runs and took Home
  from 6.6-8.6 s to 2.2 s.
- **`/api/sources` is not the collection history on the real install**
  (524 runs, 1.8 MB of `stats_json`: under 0.3 s of the request). It is
  `_posting_facts` walking the whole `job` table in index order: 14.0 of
  17.6 s first, 3.3-4.2 of 4.8-5.5 s again. A plain table scan gives the same
  answer reading 151 MB instead of 799 MB (0.2-0.7 s instead of 1.0-2.9 s).
  Alternatives A and B are therefore not worth implementing.
- `/api/health` beside `/api/home` did not measurably slow Home (warm). What
  concurrency does cost is the reverse: Home holds the interpreter (CPU-bound
  Python), so `/api/sources` and `/api/home/outdated` take 1.6-3x longer when
  they run beside it.

## Verdict of the synthetic diagnosis (2026-11-13)

**DIAGNOSIS PARTIAL — REAL VALIDATION NEEDED.**

- Confirmed by profiling: `/api/sources` grows linearly with the collection
  history (`pipeline_run`, never pruned), which it reads five times and
  decodes up to four times per request: 0.25 s with no history, 34.6 s with
  600 synthetic runs. The page itself asks `/api/sources` and
  `/api/home/outdated` only after `/api/home` has answered.
- Not reproduced: Home's real 28.8 s. Every Home endpoint is under 1.5 s cold
  on 30,000 synthetic postings, alone or all together, and the caches behave
  as documented. The real warm Home was 1.6 s (2026-11-10), so the real cost
  is most likely cold reading of a large file under memory pressure, which a
  synthetic corpus cannot show. Which bytes, and how much is contention with
  `/api/health`, needs a read-only measurement on the real install.
- First implementation step that is safe now: one read and one decode of the
  collection history per `/api/sources` request (answer-identical).

## 1. Phase 3: formal closure

**COMPLETED — MERGED — PUBLISHED — REAL PERFORMANCE VALIDATED.**

| | |
|---|---|
| Commits | `18d00d5` (code), `5cfc33b` (docs), `ca67fe9` (review fix F1/F2), `8a43fb0` (docs) |
| Publication | `origin/main` moved `dd581cb` -> `8a43fb0` on 2026-11-13, fast-forward, no force |
| Real result | first `/api/jobs/narrowings` 154.9 s -> 14.2 s (-90.9%), target 15 s met |
| Equivalence | 800/800 narrowings and 150/150 filter bags equal against the `dd581cb` module after the F1 fix; production SQL byte-identical before and after the fix |
| Independent review | done; F1 (deep levels sharing a pass) and F2 (nesting guard) fixed, F3 left by design |
| Real data | not modified; DB files, logical counters, configuration and the untracked CSV identical before and after the real measurement |
| Migrations | none needed |

Carried-over observations, unchanged:

- The onboarding persona browser test (`test_release_personas`) is a
  pre-existing flake (failed 1/5 on `dd581cb`, 0/5 on the candidate).
- F3: the rowid work-model subquery is not limited to the configuration, so
  its set grows if many revisions are kept in `job_match`; the fix would be an
  index `(work_model, config_id, config_version)`, i.e. a migration (owner).
- The real measurement depended on machine load and cache state (2.5 GB free
  RAM, CPU about 50% busy, other sessions running; disk cache state unknown).
- Its counts move with the clock (the 120-day age cut and the 7-day
  "still listed" window), so two measurements on different days differ.
- `NARROWINGS_PHASE3.md` predates the real validation; this section is the
  record of its outcome.

## 2. Starting state of Phase 4

Last real measurement (2026-11-13, `8a43fb0`, auto-update answered in the
browser, nothing written):

| Request (Home's first opening) | start | duration |
|---|---|---|
| `/api/health` | 0.82 s | 21.3 s |
| `/api/home` | 0.82 s | 28.8 s |
| `/api/firstrun` | 0.85 s | 1.0 s |
| `/api/profiles`, `/api/app`, `/api/evidence`, `/api/career`, `/api/documents`, `/api/retrieval`, `/api/auto-update` | 0.79-0.83 s | 11-127 ms each |
| `/api/sources` | **29.63 s** (after `/api/home` answered) | 15.0 s |
| `/api/home/outdated` | **29.64 s** (after `/api/home` answered) | 8.8 s |
| `/api/rescore` | 38.48 s | 56 ms |

Milestones: structure and navigation usable at 0.87 s; dashboard drawn at
29.7 s; the last Home request (sources) ended at 44.6 s. Earlier real
measurements of the same reads: `/api/home` 18.7 s cold / 1.6 s warm
(2026-11-10), 26.3 s (2026-11-12, `dd581cb`, 4.7 GB free).

Two facts about this waterfall shape everything below:

1. **`/api/home` and `/api/health` run at the same time** (both start at
   0.82 s), on separate server threads (`ThreadingHTTPServer`, one SQLite
   connection per request). Their durations overlap; they are not additive.
2. **`/api/sources` and `/api/home/outdated` do not start until `/api/home`
   has answered.** The rail (`main.js loadRailReadouts`) awaits `getHome()`
   before `getSources()`, and `home.js` asks `getHomeOutdated()` only after
   drawing the dashboard. So the page's end-to-end time is roughly
   `home + max(sources, outdated)`, a frontend sequencing effect, not one
   slow query.

## 3. Map of Home's endpoints

Static reading of `8a43fb0`, confirmed by tracing every statement each handler
runs (`set_trace_callback` plus a timing wrapper around the connection, on the
synthetic corpora).

### Flow

```
browser: Home opens (main.js + home.js)
 |
 +-- at once ---------------------------------------------------------------
 |    GET /api/home ----------- WorkspaceRoutes.home  (module lock _HOME_GATE)
 |    GET /api/health --------- JobsApi.health        (no lock, no memo)
 |    GET /api/firstrun, /api/app, /api/profiles, /api/evidence, /api/career,
 |        /api/documents, /api/retrieval, /api/auto-update   (all < 0.2 s real)
 |    POST /api/auto-update/run (declined on the real install; changes nothing)
 |
 +-- after /api/home answers -----------------------------------------------
      rail:    GET /api/sources --------- JobsApi.sources (no lock, no memo)
      home.js: GET /api/home/outdated --- WorkspaceRoutes.home_outdated
                                          (module lock _OUTDATED_GATE, memo)
                then GET /api/rescore (56 ms)

server: ThreadingHTTPServer, one thread and one SQLite connection per request
        (profile DB with the shared catalogue ATTACHed as `catalogue`), WAL.
```

### `GET /api/home` (`workspace_api.py` `home` -> `_home`)

| Step | What it reads | Notes |
|---|---|---|
| `candidate_id_of`, `LAST_REVIEWED_AT` | `candidate`, `candidate_state` | 1 row each |
| `_freshness_parts` | `COUNT(*), MAX(..)` over `job_application`, `job_enrichment`, `job_availability`; `population_token` (`compute_revision`, `profile_revision`) | the memo key; run on every request |
| **lists** (`_home_lists_now`), only on a memo miss | `view_prefs.read`; `repo.count(default_filter)` (a grouped `COUNT` over `(company_id, title)`); `SELECT COUNT(*) FROM job WHERE closed_at IS NULL`; `deep_levels()`; then **one `repo.page()` per section** among `since_last_review`, `recent`, `best` (only those the digest builds: `since_last_review` needs a checkpoint) | the expensive part |
| `metrics` | this person's `job_application`, `job_application_event`, latest `find-jobs` run | milliseconds |
| `profile_gaps`, `read_unsure`, `declarations_pending` | configuration, `candidate_state` | milliseconds |
| `latest_refresh_run` | one `pipeline_run` row, `LIMIT 1` | |
| `_home_lists_checked` | `population_token` again; counts the lists again ONCE if the catalogue moved during the request and no collection runs | at most two list counts per request |

Each section page is `ScoredJobQuery.page(..., sort="deep", lane="shortlist",
deep_levels=...)`: the full `_select` (every card column, including
`jm.result_json`, `SUBSTR(jr.description_text, 1, 400)`, the company, the
application and the enrichment), the duplicate-group election
(`group_duplicates`), and `ORDER BY` the deep tier, the deep rank (two
correlated lookups in `temp.deep_level`), `jm.match_score`, `j.id`, then
`LIMIT 5`. The plan ends in `USE TEMP B-TREE FOR ORDER BY`: **every row that
passes the filter is materialised with all its columns before the sort**, and
five are kept.

`deep_levels()` (`deep_api.deep_levels`) derives every deep reading's level
and staleness; it is remembered under `(COUNT(*), MAX(created_at)) of
deep_review`, the contract identity, the two context digests, the
`population_token` and the configuration identity, and costs "about half a
second on 710 readings" (its own docstring); the real install has more.

### `GET /api/health` (`api.py` `health`)

No memo of its own, no lock. Per request:

- `SELECT COUNT(*) FROM job WHERE closed_at IS NULL` (also asked again by
  `runtime.indicator` inside the same request, and by Home and by sources);
- `MatchRepo.count_for` (covering index), `digests_for` (`DISTINCT
  config_digest`, covering index + temp B-tree), `stale_schema_count`
  (`job_match` JOIN `job`, covering), `_reading_moved` ->
  `reading_moved_count` (`idx_job_match_reading`, then a row lookup per posting
  whose reading moved; remembered under `population_token`);
- `search_index.is_current`: `COUNT(*), MAX(last_seen_at) FROM job` (covering
  `idx_job_last_seen`, the whole catalogue including closed postings);
- `read_identity`, `indicator`, `runtime_fingerprint`, the Ollama status (no
  socket).

### `GET /api/sources` (`api.py` `sources`)

No memo, no lock. Per request:

- `sources.health.health`:
  - `_board_facts` (`source_board` GROUP BY provider);
  - `_posting_facts`: `SELECT provider, COUNT(*), MAX(first_seen_at) FROM job
    GROUP BY provider`, plan `SCAN job USING INDEX sqlite_autoindex_job_2`:
    the whole `job` table, open and closed, **read row by row in index order**
    (the index does not hold `first_seen_at`);
  - `_runs`: every collection run of `pipeline_run` (`stage LIKE '%collect%'
    OR ...`, a full scan plus a sort), then `json.loads(stats_json)` per run in
    `_last_refresh` and **again** in the loop that follows;
  - `catalogue.resolve`: `_counts_by_provider` (`job` open, GROUP BY
    provider, temp B-tree), `_boards_by_provider`,
    `_producing_boards_by_provider` (`source_board` JOIN `job`, `COUNT
    DISTINCT`).
- `modes`, `opted_in`, the experimental states (small tables).
- `_refresh_progress` -> `sources.progress.read_progress`:
  - `_latest_runs`: `SELECT * FROM pipeline_run WHERE stage LIKE 'collect%'
    ORDER BY started_at DESC` and `json.loads` of **every** row (`_non_event`)
    to keep the newest per stage;
  - `_last_success`: `MAX(finished_at)` per stage with `json_extract` in SQL
    over every collection run;
  - `_family_history`: every `stage = 'collect'` run, `json.loads` again (up
    to twice per row).

So one `/api/sources` request reads the whole collection history five times
and decodes each run's `stats_json` up to four times in Python. Nothing in the
product ever deletes a `pipeline_run` row (no retention exists for it), and a
shared-collector run carries a `by_board` entry per board it visited.

### `GET /api/home/outdated` (`workspace_api.py` `home_outdated`)

Memo `_outdated_memo` under `(db path, configuration, digest, person half of
the freshness)` plus the catalogue half (kept while a collection runs), at
most `HOME_MEMO_SECONDS`. On a miss it runs the full `rescore.plan(conn,
config)` (TARGETED) and returns `len(plan.targets)`: the dirty ledger,
`_missing_or_stale` (open postings without a current score), `_open_counts`,
`_reading_moved` (the same set `/api/health` counts, as ids), the
`job_input_revision`/`job_score_revision` receipts, `_open_scoreable` for the
marks, a `sorted()` of every target and `_eligible_first` (one more read of
the eligible stored scores) to order a list whose length is all Home uses.

### `GET /api/firstrun`

Twelve small counts (`cv_proposal`, `intake_claim`, `job_match` COUNT, ...).
1.0 s real, 0.1 s synthetic. Not a bottleneck.

### Work done more than once

| Work | Where |
|---|---|
| `COUNT(*) FROM job WHERE closed_at IS NULL` | Home lists, health (twice: `health` and `indicator`), sources (`_counts_by_provider` is the same population grouped) |
| Full scans of `job` | health (`is_current`, covering), sources (three: `_posting_facts` row by row, `_counts_by_provider`, `_producing_boards`) |
| "Scores whose reading moved" | health (`reading_moved_count`, a count, memo by population) and outdated (`_reading_moved`, the ids, inside the plan) |
| Collection history | sources: five reads, up to four JSON decodes per run |
| `_freshness_parts` | Home and outdated, each request (cheap) |
| `population_token` | Home (twice), outdated, health (`_reading_moved` key), deep levels |

## 4. Caches and freshness (audit, nothing changed)

| Cache | Holds | Key | Dropped when | Bound |
|---|---|---|---|---|
| Home lists `_home_lists` (per `JobsApi`) | total, open postings, sections | db path, configuration id/version/digest, the person half of `_freshness_parts` (applications, readings, availability checks, `self.writes`, the day), last review, recalculation running, served profile; **plus** the catalogue half (`population_token`) unless this profile's collection runs | any key part moves; the catalogue moves with no collection running; TTL | `HOME_MEMO_SECONDS` = 600 s (fixed from the time counted, not sliding) |
| Home during a recalculation | the same lists | `("recalculating", monotonic // 60)` | every minute | `HOME_RUNNING_REUSE_SECONDS` = 60 s |
| `_outdated_memo` | the outdated count | db path, configuration, digest, person half | key moves; catalogue moves with no collection running; TTL | 600 s |
| `_deep_levels_cache` | every reading's level and state | `COUNT, MAX(created_at)` of `deep_review`, contract, context digests, `population_token`, configuration | any of those | none (exact key) |
| `_reading_moved_memo` (health) | the count | db path, configuration, digest, reading identity, `population_token` | any of those | none (exact key) |
| `_aggregates` (Jobs list totals, narrowings) | per-filter totals | freshness token + filter | token moves | 600 s |
| `search_config` | the configuration | loaded once | server restart | process |
| `/api/health`, `/api/sources` | **nothing** | | | owner decision 2026-11-12: not cached |
| Browser `api.getHome` | the read in flight | shared between Home and the rail, never across a write the page sent; `forgetHome()` at the end of a run | | |

Answers to the audit questions:

1. **What is stored:** see the table. No cache persists across a restart; the
   first Home after a restart always counts.
2. **Key:** everything the lists depend on except the catalogue counter,
   which is checked beside the key so it can be waived during a collection.
3. **Invalidation:** any change of the person's own state (applications,
   hides, saves, readings, checks, any write the server accepted, the day);
   any catalogue or score write (`population_token`) while no collection
   runs; the 600 s bound.
4. **During a collection:** the lists are kept across the catalogue moving
   (shown with `lists_at`) and counted once when the run ends.
5. **During a recalculation:** reused one minute at a time; progress comes from
   the run.
6. **External changes** (another process, another profile writing the shared
   catalogue): detected through `population_token`, which every catalogue
   write moves. They are counted again on the next request; only this
   profile's own collection waives the check.
7. **Profiles:** one `JobsApi` per profile, so every memo is per profile; the
   key also holds the db path and the served profile. But `_HOME_GATE` and
   `_OUTDATED_GATE` are **module-level** locks: two profiles served by one
   process wait for each other's Home (correct, slower; worth knowing, not a
   defect).
8. **TTL expiry:** the next request counts everything again, synchronously,
   inside the request; nothing refreshes in the background.
9. **Simultaneous requests:** Home requests share the work (`_HOME_GATE`: the
   second waits and reads the first's memo); outdated likewise. Health and
   sources do not share anything, with each other or with Home.
10. **Duplicate work right after expiry:** not for Home (the gate); yes across
    endpoints: Home, health and sources each count the open postings, and
    health and outdated each compute the moved readings, at the same moment.
11. **Old answers:** the server marks lists counted during a collection or
    behind the catalogue (`lists_current: false`, `lists_at`); the page drops
    answers for another profile and older reads.
12. **Risk of stale information:** bounded by 600 s for anything the token
    cannot see (the token sees every write this product makes). During a
    collection the lists are knowingly older, and say so. No unmarked stale
    path was found.

Classification:

- **Must be current on every request:** the person's own rows (cards,
  applications, gaps, questions, the latest refresh), and whether a collection
  or recalculation runs. Already so.
- **Reusable after a freshness check:** Home's lists, the outdated count, deep
  levels, the moved-readings count, the totals: already reused under exact
  keys. Newly reusable under an exact key, if wanted: sources' per-provider
  counts and the collection-history summary (both are functions of
  `population_token` and `pipeline_run`'s own state), and health's counts.
  Each of these would be a NEW cache, which the owner declined for health and
  sources on 2026-11-12: **NEEDS OWNER DECISION**.
- **Policy decisions for the owner:** the 600 s bound, a sliding TTL,
  computing in the background before Home is asked, keeping memos across a
  restart, and a retention rule for `pipeline_run`.

## 5. Synthetic profiling

### Method

- Corpora from `scripts/synthetic_volume.py` in a scratch folder: 10,000
  postings (700 employers, 7,000 older revision rows; 448 MB) and 30,000
  (2,000 employers, 20,000 older rows; 1.3 GB). A 60-posting corpus is used
  only to pay imports and lazy module loads before anything is timed. No
  90,000-172,000 corpus was built: the 30,000 one already shows that the
  synthetic Home does not reproduce the real slowness (below), and Phase 2's
  172,000 synthetic corpus gave Home 1.1-1.2 s, so a bigger one would not
  answer the question.
- Handlers called in process (`JobsApi(ServerConfig(...))`), each connection
  wrapped to time every `execute` and fetch (SQL time), the rest of the
  handler being Python time; thread CPU, process bytes read
  (`GetProcessIoCounters`, OS cache hits included), peak working set,
  serialisation (`json.dumps`) and response size.
- **first** = a fresh unbuffered copy (`xcopy /J`, so none of it is in the
  file cache) opened by a fresh process; **warm** = same process, a new
  `JobsApi` (no application cache, OS cache warm); **hit** = the same
  `JobsApi` asked again. Three rounds, each case on its own fresh copy;
  medians with ranges. The machine had other load; single runs vary.
- Plans with `EXPLAIN QUERY PLAN` on the synthetic files only.
- Scripts stayed in the scratch folder; no production module was touched
  (deep levels and section pages were substituted by monkeypatching in the
  scratch process).

### Isolated, 30,000 postings (3 rounds)

| Endpoint | first (cold) | warm | hit | SQL (first) | queries | read | response |
|---|---|---|---|---|---|---|---|
| `/api/home` | 1.40 s [0.59-1.85] | 0.31 s | 0.022 s | 1.18 s | 32 | 43.6 MB | 38 KB |
| `/api/health` | 0.17 s [0.10-0.27] | 0.05 s | 0.031 s | 0.07 s | 15 | 20.3 MB | 1.2 KB |
| `/api/sources` (no run history) | 0.63 s [0.34-0.65] | 0.29 s | 0.30 s (no cache) | 0.42 s | 19 | 47.6 MB | 161 KB |
| `/api/home/outdated` | 0.40 s [0.31-0.57] | 0.25 s | 0.017 s | 0.28 s | 18 | 16.6 MB | 37 B |
| `/api/firstrun` (10,000) | 0.14 s | 0.05 s | 0.03 s | 0.02 s | 12 | 0.3 MB | 0.9 KB |

Serialisation is under 3.5 ms everywhere; peak working set 79-82 MB
process-wide. Home's two section pages are 0.9 of its 1.18 s of SQL
(0.50 s / 13.9 MB and 0.39 s / 20.0 MB cold); the grouped total 0.17 s.

### Concurrency, 30,000

| Case | Result |
|---|---|
| All five at once, cold (3 rounds) | everything done in 1.18-1.22 s; Home ends last, the others 0.15-0.61 s |
| Home + health, warm | 0.27-1.07 s |
| Home + sources, warm | 0.15-0.60 s |
| Home + outdated, warm | 0.13-0.39 s |
| All five, warm | 0.44-0.64 s |

On the synthetic corpus concurrency costs little: the endpoints overlap and
none waits for a lock other than its own gate. (One harness pitfall: opening a
freshly copied file from several threads at once raced the WAL recovery and
failed with "database is locked" on `PRAGMA journal_mode`; the server never
meets it, because `launch.py` opens and migrates on one connection before
serving, which the harness now does too.)

### Caches, 30,000 (warm)

| State | wall | queries | read |
|---|---|---|---|
| first | 0.28 s | 32 | 43.5 MB |
| hit | 0.02 s | 21 | 0.2 MB |
| memo older than 600 s | 0.26 s | 32 (counted again) | 43.5 MB |
| catalogue moved (`population_token`) | 0.27 s | 32 (counted again) | 43.5 MB |
| catalogue moved during this profile's collection | 0.03 s | 21 (kept) | 0.2 MB |

The memo behaves as documented in every state.

### What the synthetic corpus lacks, measured one by one

| Factor | Experiment | Result |
|---|---|---|
| **Collection history** in `pipeline_run` (none in the corpus; never pruned in the product) | 1,500 invented boards; 50, 200 and 600 invented `collect` runs with a `by_board` entry per board (10,000 corpus, warm) | `/api/sources`: 0.25 s (no runs) -> **2.6 s** (50 runs, 22.5 MB of `stats_json`) -> **11.3 s** (200, 90 MB) -> **34.6 s** (600, 270 MB). Linear in the history; 97% CPU. Home, health, outdated and firstrun unchanged |
| Deep levels (none in the corpus) | 1,500 and 3,000 invented levels passed to Home's pages (30,000, cold, 2 rounds each) | Home 1.5 s -> 2.0-2.3 s (1,500) and 1.6-2.1 s (3,000); same bytes read; the cost is the ordering's correlated lookups |
| Deep reviews read whole (`DeepRepo.newest_per_job` runs `SELECT * FROM deep_review`, `raw_response` included) | 1,500 invented reviews of ~13 KB (19.5 MB) | 0.29-0.39 s cold, 0.07-0.14 s warm, 28 MB read; the per-review derivation is "about half a second on 710 readings" (docstring), so about 1 s more at 1,500 |
| Ids first, then the five rows (deferred join) for Home's pages | same page through both forms, equivalence checked (same ids, same order, same rows) | **no gain**: 13.6 vs 13.9 MB and 19.7 vs 20.0 MB; the pages' cost is the filter and the duplicate-group election, not the selected columns. Rejected |

Profile of `/api/sources` with 600 runs (cProfile, 38.3 s):

| Part | Time |
|---|---|
| `json.loads` of `stats_json`: 2,400 decodes for 600 runs | 15.4 s (40%) |
| SQLite reading the history: five reads (`_runs`, `_latest_runs`, `_last_success` with `json_extract`, `_family_history`, plus the sort) | 11.7 s (31%) |
| `_last_refresh` loops over every `by_board` entry per provider (903,000 generator steps each for two sums) | about 6 s |
| everything else (catalogue, counts, building the answer) | under 1 s |

### Relevant plans (synthetic)

| Query | Plan | Note |
|---|---|---|
| Home section page | `SEARCH jm USING INDEX idx_job_match_discovery_gates`, the lane via `COVERING INDEX idx_job_raw_chars`, the group election as co-routines, `USE TEMP B-TREE FOR ORDER BY` | every matching row is sorted before `LIMIT 5`; fine at 30,000 |
| `SELECT provider, COUNT(*), MAX(first_seen_at) FROM job GROUP BY provider` (sources) | `SCAN job USING INDEX sqlite_autoindex_job_2` | the whole `job` table read row by row in index order; no index holds `first_seen_at` |
| `SELECT provider, COUNT(*) FROM job WHERE closed_at IS NULL GROUP BY provider` | `SEARCH job USING INDEX idx_job_open_population`, temp B-tree | a row read per open posting |
| collection history (`stage LIKE ...`) | `SCAN pipeline_run`, temp B-tree for ORDER BY | full scan and sort, five times per request |
| `stale_schema_count`, `count_for`, `digests_for` (health) | covering indexes | cheap |
| `reading_moved_count` (health), `_reading_moved` (outdated) | `idx_job_match_reading`, a row lookup per moved reading | proportional to the outdated scores (21,769 on the real install's last screen) |
| `_freshness_parts` | `SCAN job_application`, `SCAN job_enrichment` | small tables |

## 6. Confirmed bottlenecks and unconfirmed hypotheses

**Confirmed (synthetic profiling or the real waterfall):**

1. **The page's own sequencing.** `/api/sources` and `/api/home/outdated` start
   only after `/api/home` answers (real waterfall; `main.js`, `home.js`).
   The page ends at `home + max(sources, outdated)`.
2. **`/api/sources` is linear in the collection history**, which nothing
   prunes: five reads of it, up to four JSON decodes per run, a per-board loop
   per provider. Reproduced from 0.25 s to 34.6 s by adding history alone.
3. **Work done more than once across Home's requests** (open-posting count
   four times, moved readings twice, three `job` scans in sources).
4. **The memo and its invalidation work as documented**; the miss path is the
   whole cost, and the first Home after every restart is a miss.

**Not reproduced; hypotheses for the real install:**

- H1. **Home's 28.8 s is mostly cold reading of a large file, not
  computation.** The synthetic Home is 1.4 s cold / 0.3 s warm at 30,000 and
  1.1-1.2 s at 172,000 (Phase 2); the real Home was 18.7 s cold and 1.6 s warm
  on 2026-11-10. Same queries, so the difference is bytes read from disk on a
  ~10 GB file that does not fit the free RAM (2.5 GB on 2026-11-13), and the
  real distributions (more older revisions in `job_match`, larger
  `result_json`, the real eligible population, the deep readings). Not
  measurable without reading the real install.
- H2. **`/api/health` and `/api/home` slow each other down on a cold disk.**
  Health took 10.0 s on 2026-11-12 (4.7 GB free) and 21.3 s on 2026-11-13
  (2.5 GB free), both concurrent with Home. Synthetic concurrency cost little,
  but the synthetic file is in a fast cache; real I/O contention is not
  reproduced.
- H3. **The real `/api/sources` 15.0 s is largely the collection history**
  (15 s on the synthetic curve is about 250 to 300 runs of 1,500 boards, or
  more runs of fewer boards). The real history size is unknown.
- H4. **The deep readings add 1-3 s to Home's miss** (the derivation over every
  review, the full `SELECT * FROM deep_review`, the ordering), more on a cold
  disk. Each part measured small in isolation.
- H5. **`/api/home/outdated` 8.8 s real** is the plan over 21,769 moved
  readings and every open posting on a cold disk; the synthetic plan has few
  targets.

## 7. Alternatives considered

| # | Problem | Evidence | Change | Expected gain (hypothesis) | Divergence risk | Freshness | Migration | Other screens | Complexity | Tests |
|---|---|---|---|---|---|---|---|---|---|---|
| A | sources reads and decodes the history 5 and 4 times | cProfile: 40% JSON, 31% history SQL at 600 runs | read the collection runs once per request and decode each `stats_json` once, handing the decoded rows to `health._last_refresh`, `health` and `progress.read_progress` | 38 s -> roughly 10-12 s on the 600-run synthetic history; real share unknown (H3) | low: same rows, same order, same functions; prove by comparing the whole `/api/sources` answer on synthetic histories | none (still read on every request) | no | the sources panel and the rail use the same answer | medium (three modules share one read) | answer equality on 0/50/600 runs, feeds and shared-collector runs, running and crashed runs, deferred runs, failures and gone boards |
| B | `_last_refresh` loops over every board per provider per run | 2 x 903,000 generator steps at 600 runs | group `by_board` by provider once per run | about 4-6 s of the 38 s | low | none | no | same | small | same equality tests |
| C | the history grows for ever | nothing deletes `pipeline_run` | a retention rule (keep the receipts the screens read, summarise or drop older `by_board` lists) | bounds sources for good | **changes what history exists** | none | data change | audit and run history screens | medium | **NEEDS OWNER DECISION** |
| D | sources and outdated wait for Home | real waterfall | ask them at once instead of after Home | the page's END could come earlier; the dashboard could come LATER (more cold I/O beside Home, H2) | none in data | none | no | rail, Home | small (frontend) | browser tests for order and dropped answers; **needs a real measurement before choosing** |
| E | health runs beside Home on a cold disk | H2 | ask `/api/health` after Home's answer (it feeds only the revision notice and the empty state) | Home could get the disk to itself; unproven | none in data | none | no | every page that shows the notice | small (frontend) | browser tests for the notice and the empty state; needs a real measurement |
| F | outdated builds, sorts and reorders a target list to take its length | plan code: `sorted(chosen)` and `_eligible_first` (one more read of eligible stored scores) | a count path for `home_outdated` that skips the ordering | small (one read and a sort); real share unknown (H5) | low if the set is the same; must equal `len(plan(...).targets)` | none | no | Recalculate must keep using the full plan | small | equality against `plan()` on every ledger state the rescore tests build |
| G | the open-posting count is computed four times | trace | pass the count already read (health -> `indicator`) | milliseconds warm; one covering scan cold | none | none | no | none | trivial | existing health tests |
| H | `_posting_facts` reads every `job` row in index order | plan | an index `job(provider, first_seen_at)` | one full-table read fewer per sources request | none | none | **yes** | none | small | **NEEDS OWNER DECISION** (migration) |
| I | Home's section pages sort the whole population | plans | ids first, rows after | **refuted**: same bytes | | | | | | rejected |
| J | Home's miss is paid on every restart and after every collection | design | keep the lists across a restart, or compute them in the background after a collection, or a sliding TTL | could make the first Home instant | stale-on-restart risk | **changes freshness policy** | maybe | Home | medium to large | **NEEDS OWNER DECISION** |
| K | health and sources are recomputed on every request | design | exact-key caches (`population_token` + `pipeline_run` state) | sources/health near zero on a repeat | none if the key is exact | new cache | no | sources panel | small | **NEEDS OWNER DECISION** (declined 2026-11-12) |

## 8. Recommendations, in order

1. **Measure the real install first (read-only, needs approval).** Without it,
   nothing about Home's 28.8 s can be attributed. See section 10. Cost: half a
   day; no code.
2. **A + B in `/api/sources`** (one read, one decode, grouped boards). The
   only bottleneck confirmed by profiling, semantically neutral, provable by
   answer equality. 1 to 1.5 days with tests.
3. **F** (outdated count) and **G** (shared open count). Small, neutral.
   Half a day together.
4. **D/E (request order on the page)**, only after the real measurement says
   which order helps the dashboard: half a day plus browser tests, plus one
   real measurement.
5. Owner decisions, in the order they would matter if the real measurement
   confirms H1/H3: **C** (history retention), **J** (when Home's lists are
   computed), **K** (caches for sources and health), **H** (index).

## 9. Success definition

- Home's dashboard drawn within 10 s of opening on the real install, cold
  after a restart, with navigation usable under 0.5 s (already 0.87 s on
  2026-11-13 including the page itself; the target applies to the shell).
- No hidden Find jobs read (none today: zero `/api/jobs` from Home).
- No regression in Tailor (lists 7.7/10.0 s), Applications (0.23 s) or
  narrowings (14.2 s).
- Identical answers: every change proven equal on synthetic corpora before the
  real check; no lost precision; freshness rules unchanged unless the owner
  decides otherwise.

**Is 10 s feasible?** Not shown. The synthetic evidence says the queries are
not expensive in themselves; the real cost is cold I/O (H1) plus the request
order. If the real measurement shows Home alone, cold, still well above 10 s,
reaching the target needs either fewer bytes read per miss (requires the real
profile to know which), or not paying the miss when Home opens (J), which is a
product decision about freshness and possibly an architectural change (a
background or persisted computation).

## 10. Criteria for the future real validation

Needs the owner's approval; read-only; the Phase 3 harness discipline.

1. Read-only sizes (no row content): `pipeline_run` rows and `SUM(length(stats_json))`
   by stage; `deep_review` rows and total size; `job_match` rows per
   configuration revision; `job` open and total; `page_count * page_size` per
   file. This settles H3, H4 and the scale of H1.
2. Each Home endpoint called **alone**, sequentially, by GET, with the server
   process's I/O counters and CPU recorded per request: after a restart
   (cold), then again (warm), then a cache hit. This settles H1 and H2.
3. The same requests concurrently, as the page asks them, to measure H2.
4. After A + B: `/api/sources` before and after on the same history, and the
   same answer body.
5. Unchanged: integrity checks (ledger, counters, file hashes except `-wal`
   and `-shm`), the CDP interception of every non-GET, the CSV hash.

## 11. Estimates

| Step | Estimate |
|---|---|
| Real read-only measurement (sizes + per-endpoint isolation) | 0.5 day |
| A + B (sources: one read, one decode, grouped boards) with equality tests | 1-1.5 days |
| F + G | 0.5 day |
| D/E after the measurement | 0.5 day + browser tests + one real measurement |
| Owner decisions C, H, J, K | design 0.5-2 days each once decided; J may be larger |

## 12. Real read-only profiling (2026-11-16)

Authorised by the owner: reading, measuring and profiling the live install,
nothing written. Code as published at `8a43fb0`, unmodified.

### 12.1 How it was measured, and what was protected

- **No server, no `launch.py`** (it migrates and syncs the board registry,
  both writes). The handlers ran in process on a `JobsApi` subclass whose
  only change is `connect()`: the profile database opened with
  `mode=ro` and the catalogue attached with the product's own
  `attach_read_only` (`mode=ro`), `temp_store=MEMORY` (Home's deep sort needs
  `temp.deep_level`; `PRAGMA query_only` refuses temp tables, so it is used
  for the direct aggregate queries only), and an SQLite authorizer denying any
  write to `main` or `catalogue`. `sqlite3.connect` and the product's
  write-capable `storage.db.connect` and `catalogue.attach` were replaced by
  guards that refuse the live data folder. Validated first on a synthetic
  copy: both guards refused, `UPDATE`/`CREATE`/`DELETE` were denied, temp
  tables worked; the I/O counter matched a file read byte for byte; CPU
  time matched a 1 s busy loop (0.91 s on a loaded machine).
- Timings per handler: wall, thread CPU, process bytes read (OS cache hits
  included), peak working set, SQL time per statement (no parameters stored),
  Python time (wall minus SQL), serialisation, response size, and inclusive
  timers on the functions named in the hypotheses. Imports and lazy module
  loads were paid first on a 60-posting SYNTHETIC corpus, so no live page was
  read before a "first" measurement. One process per measurement.
- Nothing stored but aggregates, SQL text and hashes: no response body, row,
  posting, JSON body or personal text.

**An incident to record.** The first validation run of the harness was meant
for the synthetic copy, but the path override had not been applied (a shell
edit failed silently), so it ran against the live install, read-only, from
15:09 to 15:13. Every guard was active, no write was attempted or denied, and
it was stopped before the guard self-test (which issues write statements on
purpose) could reach the live files. The state checks below show nothing
changed. Its numbers are kept only as a labelled side note, because during
those minutes **another session's full unit and integration suite started
(15:10) in the main checkout**, sharing CPU and disk. That suite was then
waited out: every measurement below started at 16:19, with no test suite or
other app process running.

**Cache state.** The machine had booted at 14:57 and the database files had
not been opened since 2026-11-13, so the very first read (the side-note run)
met an almost certainly cold file cache. That one-shot state was spent by it.
The clean runs are therefore "first read in a fresh process", with an OS
cache warmed by earlier runs but under memory pressure (1.2-2.0 GB free of
16 GB, a 5.9 GB profile file and a 9.9 GB catalogue), not cold-disk numbers.

### 12.2 The real volume

| | |
|---|---|
| Catalogue file | 9.9 GB (2,421,310 pages of 4 KB, freelist 0) |
| Profile file | 5.9 GB (1,447,234 pages, freelist 3) |
| `job` | 291,417 rows, 274,420 open; Workable 147,766 open, fourdayweek 46,241, jobgether 16,993, others below 13,000 |
| `job_raw` | 257,251 adverts, about 4.9 KB each (1% sample, max 28 KB) |
| `source_board` | 23,117 boards (jobgether 6,062, workable 5,934, himalayas 3,551, ...) |
| `job_match` | 615,644 rows in three revisions kept: v7 124,233, v9 217,394, v10 274,017; `result_json` about 7.3 KB each (2% sample, max 16 KB) |
| current revision (v10) | 12,462 VERIFIED_ELIGIBLE, 75,227 VERIFIED_NOT_ELIGIBLE, 186,328 UNRESOLVED (all NOT_BLOCKED) |
| `pipeline_run` | 524 runs, 1.77 MB of `stats_json` in total (483 under 10 KB, 41 of 10-100 KB, largest 50.6 KB); 310 shared `collect` runs carrying 2,764 `by_board` entries in total (at most 141 per run) |
| `deep_review` | 3,341 reviews of 2,127 postings: 20.4 MB `raw_response`, 20.1 MB `review_json`, 14.5 MB `sent_json`; 1,925 of the postings open, **244 open and eligible under v10** |
| `job_score_revision` | 615,644; `deep_attempt` 3,344; applications 6; confirmed claims 261 |

The aggregate `SELECT provider, COUNT(*) FROM job WHERE closed_at IS NULL
GROUP BY provider` alone took 61 s during the survey (shared with the other
suite): the first sign of where `/api/sources` spends its time.

### 12.3 Each endpoint alone (16:19-16:23, two rounds, each a new process)

| Endpoint | first, round 1 | first, round 2 | again (new `JobsApi`) | same `JobsApi` | SQL of first r1 | CPU of first r1 | read | peak WS | queries |
|---|---|---|---|---|---|---|---|---|---|
| `/api/home` | **25.0 s** | 12.7 s | 11.8 s / 8.6 s | 0.02 s (memo) | 16.0 s | 14.2 s | 523 MB | 161-176 MB | 59 |
| `/api/health` | 8.8 s | 0.9 s | 0.6 s / 1.4 s | 0.8 s / 1.2 s (no cache) | 8.7 s | 2.8 s | 222 MB | 78 MB | 15 |
| `/api/sources` | **17.6 s** | 5.5 s | 4.8 s / 5.4 s | 4.5 s / 4.8 s (no cache) | 17.4 s | 7.6 s | 1,030 MB | 87 MB | 22 |
| `/api/home/outdated` | 9.6 s | 3.2 s | 2.8 s / 2.9 s | 0.01 s (memo) | 9.4 s | 4.5 s | 339 MB | 95 MB | 98 |
| `/api/firstrun` (control) | 0.8 s | 0.04 s | 0.07 s / 0.04 s | 0.04 s | 0.7 s | 0.3 s | 7 MB | 79 MB | 14 |

Serialisation is under 2.2 ms everywhere (Home's answer 17 KB, sources' 166
KB). Round 1 is the first time each endpoint's pages were read in this boot
without the other suite running; round 2 repeats it with those pages partly
in the file cache. Side note, not comparable (cold, beside another suite):
Home 89.1 s then 53.4 s, health 22.5 s then 2.3 s.

### 12.4 Where each one spends it

**`/api/home`** (inclusive timers):

| Part | first r1 (25.0 s) | first r2 (12.7 s) | again r1 (11.8 s) | again r2 (8.6 s) |
|---|---|---|---|---|
| lists (`_home_lists_now`) | 24.7 | 12.7 | 11.8 | 8.6 |
| **deep levels** (`deep_levels` -> `_review_rows`) | **13.5** (54%) | **10.9** (86%) | **10.2** (86%) | **7.0** (81%) |
| of which `SELECT * FROM deep_review` (`newest_per_job`) | 2.2 | 1.1 | 0.5 | 0.4 |
| of which reading score, title, company for the 2,127 read postings | 1.7 | | | |
| two section pages (`ScoredJobQuery.page`) | 5.9 | 1.4 | 1.2 | 1.2 |
| grouped total (`ScoredJobQuery.count`) | 2.4 | | 1.1 (side note) | |
| `COUNT(*) FROM job WHERE closed_at IS NULL` | 2.8 | | | |
| cards, gaps, questions, freshness, latest refresh | under 0.1 | | | |

cProfile (a separate warm run, 16.8 s under the profiler): `deep_assess.assess`
2,367 calls, 11.6 s cumulative; inside it `_requirement` 18,011 calls,
`_fold` (accent folding, 5.6 million `unicodedata.combining` calls) 4.9 s,
231,677 `re.search` calls with 1,423 pattern recompilations (dynamic patterns
overflow the `re` cache), `_category` 2.1 s; JSON decoding of reviews 0.8 s.
The profiler inflates this generator-heavy code: see 12.7 for what a CPU-only
change actually saved.

**`/api/health`**: first 8.8 s = `digests_for` 4.4, `reading_moved_count`
2.1, `search_index.is_current` 1.2, `count_for` 0.6, the open count twice;
afterwards 0.6-1.4 s. Cheap once its index pages are cached.

**`/api/sources`**: `_posting_facts` (`SELECT provider, COUNT(*),
MAX(first_seen_at) FROM job GROUP BY provider`) is **14.0 of 17.6 s (79%)**
first and 3.3-4.2 of 4.8-5.5 s (70-78%) afterwards; `catalogue.resolve` 1.1-3.1
s (`_producing_boards_by_provider` 0.4-2.4, `_counts_by_provider` 0.6-0.8);
the collection history (`_runs`, `_refresh_progress` with `read_progress`)
0.16-0.3 s and `json.loads` 0.09 s: **about 1-4% of the request**. Plan of
`_posting_facts`: `SCAN job USING INDEX sqlite_autoindex_job_2`, the whole
table visited in `(provider, external_id)` order with a row lookup each:
799 MB read for a 291,417-row table.

**`/api/home/outdated`**: `rescore.plan` is all of it; `_missing_or_stale` 8.1
of 9.6 s first (its `job_input_revision` ... `NOT EXISTS job_score_revision`
query 7.2 s) and 1.8-2.0 of 2.8-2.9 s afterwards; `_open_scoreable` 0.5;
`_eligible_first` and the sort of the targets do not reach the top seven
timers (under 0.08 s each).

### 12.5 Concurrency (16:23-16:26; after 12.3, so file cache as there)

| Case | Home | the other | end |
|---|---|---|---|
| A. Home alone (3 runs) | 9.8, 9.5, 8.1 s | | |
| C. Home + health together (3 runs) | 8.5, 10.2, 7.9 s | health 0.9, 1.5, 0.7 s (alone 0.6-1.4) | |
| D. Home + sources (2 runs) | 9.1, 9.2 s | sources **8.4, 8.2 s** (alone 4.8-5.5) | 9.1-9.2 s |
| E. Home + outdated (2 runs) | 9.0, 8.7 s | outdated **9.7, 9.1 s** (alone 2.8-3.2) | 9.1-9.7 s |
| F. The page's order: Home, health and firstrun at once, sources and outdated after Home (2 runs) | 7.6, 8.6 s | health 1.2 s; outdated 4.6 / 3.1 s, sources 6.1 / 4.6 s after Home | **13.8, 13.2 s** |

- **Health does not slow Home** measurably (A against C, interleaved). On a
  cold disk it may; that state could not be measured twice (12.1).
- **Home slows the others**: it is CPU-bound Python, and the others need the
  interpreter for each row they fetch, so sources takes 1.6x and outdated about
  3x as long beside it. Home itself is unchanged.
- The page as written ends at about Home + the slower of sources and outdated
  (13.2-13.8 s warm). Started together with Home (D, E), the same work ended in
  9.1-9.7 s, without slowing Home.

### 12.6 Answers to the hypotheses

| | Verdict on the real install |
|---|---|
| H1 Home is cold reading | **Partly.** The first read (25.0 s) is 16.0 s SQL; but even with its pages cached Home stays at 8.6-11.8 s, 81-86% of it the CPU of deriving every deep level |
| H2 health slows Home | **Not measurable warm** (A vs C); cold not measurable again |
| H3 sources is the collection history | **Refuted**: history is 1-4%; 70-79% is the `job` walk in index order |
| H4 deep readings add 1-3 s | **Refuted low**: they are 7.0-13.5 s, the largest single cost of Home |
| H5 outdated is the moved readings | **Refuted**: `_missing_or_stale` (the receipts query) is 65-85%; ordering the targets is negligible |

### 12.7 Experiments on the real data (scratch only, answers compared)

1. **Deep levels for Home's population only.** Home computed with its deep
   levels derived only for the read postings that are open and eligible under
   the current revision (244), against Home as written, alternating, a new
   `JobsApi` each time. Home's whole answer (minus the moment it was counted)
   hashed: **identical in all 6 runs**. Wall: as written 6.6, 8.6, 8.1 s;
   restricted **2.2, 2.3, 2.2 s** (-67% to -74%). Why it cannot change the
   answer: the levels only enter Home through `ORDER BY` lookups in
   `temp.deep_level` for rows that already passed the section's `WHERE`, and
   no row outside that population passes it.
2. **CPU-only changes inside `deep_assess`** (an ASCII fast path and memo in
   `_fold`, memoised compilation of dynamic patterns): levels identical, but
   6.4, 5.7, 5.7 s against 4.6, 6.8, 7.2 s: within the run-to-run spread.
   **Inconclusive; not recommended as a first step.**
3. **The `job` walk in sources.** Same aggregate, read-only, three alternating
   rounds:

   | Query | Plan | Time | Read |
   |---|---|---|---|
   | `_posting_facts` as written | `SCAN job USING INDEX sqlite_autoindex_job_2` | 1.0, 1.4, 2.9 s | 799 MB |
   | the same with `NOT INDEXED` | `SCAN job`, temp B-tree | 0.21, 0.21, 0.68 s | 151 MB |
   | `_counts_by_provider` as written | `idx_job_open_population` | 0.19, 0.23, 0.69 s | 172 MB |
   | both in one `NOT INDEXED` pass | `SCAN job` | 0.21, 0.22, 0.64 s | 151 MB |

   Same rows in every case (checked).

### 12.8 The alternatives, on the real data

| | Verdict |
|---|---|
| A + B (one read and one decode of the history in sources) | **Do not implement**: the history is 0.16-0.3 s of the request |
| F (count the outdated targets without ordering them) | **Do not implement**: ordering is under 0.1 s; the cost is the receipts query |
| G (reuse the open-postings count) | Small: 0.3 s warm, up to 2.8 s cold in Home; worth folding into the step it touches, not a step of its own |
| D (ask sources and outdated together with Home) | **Worth doing after the first step**: page end 13.2-13.8 s -> 9.1-9.7 s warm with Home unchanged; cold effect unmeasured |
| E (ask health after Home) | **Do not implement**: health does not slow Home |
| New: deep levels for Home's population only (12.7.1) | **First implementation**: Home -67% to -74% warm, identical answer, no cache, TTL or freshness change |
| New: one `NOT INDEXED` pass for sources' provider facts and counts (12.7.3) | **Second**: 799 + 172 MB -> 151 MB; sources' largest part from 3.3-14.0 s to an expected 0.2-0.7 s, identical answer |
| deep_assess CPU changes (12.7.2) | Not now: no measurable gain |
| Outdated's receipts query (7.2 s first, 1 s again) | A plan question for a later step; not diagnosed here |

### 12.9 Freshness

Neither recommended change adds a cache, changes a key, a TTL, the
`population_token`, the write counter or when anything is computed:

- the restricted deep levels are computed where and when Home's lists are
  computed today (on a memo miss) and are NOT stored in `_deep_levels_cache`,
  which other screens read as the whole map; when that whole map is already
  remembered, Home can read from it unchanged;
- the sources change is the same SQL aggregate over the same table, read in
  another order.

Still owner decisions, unchanged: C (history retention, now irrelevant for
speed), H (index), J (when Home's lists are computed, persisted across a
restart), K (caches for health and sources).

### 12.10 Recommendation

1. **Home: derive deep levels only for its sections' population.** The ids
   should come from the section filter itself (the same `_where`), not a
   hand-written eligibility test, so the argument in 12.7.1 holds by
   construction. Tests: Home's answer equal with whole-map and restricted
   levels on synthetic corpora carrying deep reviews (read postings inside
   and outside the population, stale and current, every level); the whole-map
   cache untouched; Find jobs' deep order unchanged. **1 day.** Expected:
   Home again 8.6-11.8 s -> about 2-3 s; first read 25 s -> about 12-14 s
   (hypothesis: the cold SQL of the pages, the grouped total and the open
   count remains).
2. **Sources: one `NOT INDEXED` pass for the provider facts and counts**,
   feeding both `health._posting_facts` and `catalogue._counts_by_provider`.
   Tests: answer equality over providers with open and closed postings, none
   open, none at all. **0.5 day.** Expected: sources 4.8-5.5 s -> about 1.5-2
   s again; first read 17.6 s -> about 4-6 s (hypothesis).
3. **D: ask sources and outdated together with Home** (frontend only), after 1,
   since 1 frees the interpreter: **0.5 day** plus browser tests and one real
   measurement.
4. Then measure again (real, read-only) before anything else; the 10 s target
   for a first opening after a restart looks reachable with 1 + 2 + 3 warm
   and is not proven cold.

### 12.11 Integrity

| Check | Result |
|---|---|
| Logical state (migration ledger, schema 61 everywhere, indexes, `MAX(rowid)` of the main tables of each database, the auto-update keys) | identical before (15:20) and after (16:31), and identical to the end of the 2026-11-13 measurement |
| 403 files under `data/` and `config/` (size and mtime) | unchanged except the three `-shm` files (shared-memory index rewritten by every read-only open) |
| WAL files | 0 bytes, mtimes unchanged |
| Main database files | size and mtime unchanged (2026-11-10); SHA-256 recorded at the end for later comparisons: profile `c38d4f87b4c6b484...`, catalogue `84130dac83ea6b31...`, second profile `d68fb484f006d438...` (a full hash before was skipped on purpose: reading 16 GB would have warmed the cache the first measurement needed) |
| Writes | none attempted, none denied by the authorizer (its log only lists `PRAGMA table_info` reads) |
| Processes and port | no harness process left, port 8765 free, no collection, recalculation or deep reading started |
| `jobspy_resultados.csv` | SHA-256 unchanged (`ed80bfa9...961b`) |
| Git | `main` = `origin/main` = `8a43fb0`; nothing pushed or merged |
