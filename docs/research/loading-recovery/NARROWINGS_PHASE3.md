# Phase 3: `/api/jobs/narrowings` (2026-11-12)

The last real measurement (main `dd581cb`, auto-update answered in the
browser) left one residual: Find jobs drew its list and 20 cards in about 2 s,
then `/api/jobs/narrowings` took **154.9 s**. This phase finds why, on
synthetic data only, and makes it cheaper without changing what it answers.
Nothing here touched an owner's database, configuration or profile; no
migration, no index built, no new dependency.

## Verdict in one paragraph

Most of a cold narrowings read was not counting at all: it was reading every
advert's text, ten times. The shortlist lane clause measures each advert's
length with `length(description_text)`, and the planner served it from the
UNIQUE `content_hash` index, which reads the `job_raw` record (an advert of
~6 KB, on overflow pages) instead of the covering index migration 0054 built
for exactly this (`idx_job_raw_chars`). A second, smaller cost of the same kind
came from the excluded-work-model narrowing, which reads `job_match.work_model`,
a column stored AFTER the `result_json` blob (~15 KB), so each read walks the
blob's overflow chain. On top of that, the ten counts each re-read the same
revision. Three changes, all inside `ScoredJobQuery`: name the covering index;
ask the existing work-model index by rowid; count every narrowing in one pass.

## The flow

| Step | Where | What |
|---|---|---|
| Find jobs / Applications ask the list | `main.js` `load()` | `GET /api/jobs?...&narrowings=later` (only on those two pages since Phase 2) |
| The list | `JobsApi.list_jobs` | page + whole ad of the page + availability + deep relations on the request's connection; total (`_counts`: one or two `count`) and `facets` on two other connections in parallel; both remembered under `_aggregate_key` |
| The narrowings, deferred | same handler | sent only if remembered, else `null` and `narrowings_pending: true` |
| The page asks them | `main.js` `afterList()` | `GET /api/jobs/narrowings` with the same query, once the list is drawn; retried once after 3 s; not asked away from Find jobs/Applications (`listDue`) |
| The counts | `JobsApi._narrowings` | `_filter_from` (view prefs, deep levels, config), `_serving` (revision), `_jobs_freshness`; then up to ten `ScoredJobQuery.count` calls (below); remembered for 600 s under the same key |
| Consumers | `main.js` `renderHiddenNotice` | eight `hidden_*` numbers and `leads`; the summary "We hid {n} or more jobs" adds them in the client (overlapping counters, so the sum is a floor) |
| Inline callers | `list_jobs` without `narrowings=later` | same `_narrowings` (tests, any non-page caller); the digest and the jobs export ask `later` |
| Resume Tailor | `tailor_page.js`, `tailor_tips.js` | `narrowings=later`, never asks the narrowings; unchanged |
| Home | none | Home asks no job list and no narrowings (Phase 2); unchanged |

Cache: `_aggregate_key` = freshness token (catalogue `population_token` +
configuration, applications, readings, availability checks, this server's
write counter, the day) + revision + the filter without page, order or soft
words. Any write the server accepts moves it. TTL 600 s. A request the browser
abandons is not cancelled on the server (unchanged, as before this phase): the
work runs to the end and is remembered.

### The ten counts of one request

Each is `count()` of the request's filter with one field flipped, and the
answer is the difference with the request's own total (`total` comes from the
list's remembered totals when present, as before):

| Answer | Counted population |
|---|---|
| `hidden_by_eligibility` | `include_ineligible=True` |
| `hidden_off_target` | `include_off_target=True` |
| `hidden_unresolved` | `include_unresolved=True` |
| `hidden_by_seniority` | `include_excluded_seniority=True` (only if levels are excluded) |
| `hidden_by_work_model` | `include_excluded_work_model=True` (only if ways of working are excluded) |
| `hidden_by_age` | `max_age_days=None` minus `include_undated=True` (two counts) |
| `hidden_undated` | `include_undated=True` |
| `hidden_by_you` | `include_user_hidden=True` |
| `leads` | `lane="leads"` (only on the shortlist) |
| (total) | the filter itself, when the list's totals are not remembered |

A grouped request (`group_duplicates=1`, the cards view's default) counts
`(company_id, title)` groups: every count is a `GROUP BY` over its population.
Filters are all SQL (`ScoredJobQuery._where`); no Python work per posting.

## Profiling (synthetic, `scripts/synthetic_volume.py`)

Corpus: 30,000 invented postings (2,000 employers, a second revision of
20,000 rows; 1.3 GB), plus a mutated copy of it with 3,000 applications (saved,
tracked, 910 hidden), 1,500 availability answers (half REMOVED), 4,000
postings folded into 300 duplicated roles, 300 pasted, 1,200 closed, 2,000
old-but-still-listed, 2,500 short adverts (leads), 1 in 6 blocked, every level
and way of working, and preferences excluding JUNIOR and ONSITE. A 90,000
corpus checks the scaling. 172,000 was not built: the cost is linear in rows
(below) and the 30,000 corpus already reproduces and isolates the cause; the
real install is where the absolute number has to be measured.

"Cold" means a fresh copy made with `xcopy /J` (unbuffered, so no page of it
is in the Windows file cache) opened by a fresh process; "warm" is the same
process asking again with the application caches cleared (OS cache warm, a new
SQLite connection, as every request opens one). Old and new run in alternating
order. `read` is bytes the process read from files (cache hits included), a
deterministic proxy for cold I/O. The machine had other load (browser, other
sessions; 2.8-4.7 GB free RAM), so single runs vary; medians are reported with
ranges.

Before the change (30,000, default query, one cold run broken down):

| Count | ms |
|---|---|
| total | 1,012 |
| `include_ineligible` | 3,537 |
| `include_off_target` | 35 |
| `include_unresolved` (75% of the corpus) | 12,400 |
| age (2), undated, hidden by you, leads | 33-115 each |

Plans: every count drove from `idx_job_match_discovery_gates` (covering) and
looked each surviving row up in `job`, then ran the lane subquery as
`SEARCH r USING INDEX sqlite_autoindex_job_raw_1 (content_hash=?)`: a read of
the `job_raw` record. 444 MB read per request at 30,000 rows; warm 0.6-0.8 s.
Serialisation of the answer: 0.03 ms (211 bytes). Peak working set unchanged
(~78 MB process-wide) in every variant.

The three most expensive operations, in order:

1. **The lane subquery reading adverts.** `length(r.description_text)` from the
   table record: every row of every count, ~6 KB of overflow pages each.
   Naming `idx_job_raw_chars` (as `semantic/deep_run.py` already does): plan
   becomes `USING COVERING INDEX idx_job_raw_chars`, read per request 444 ->
   135 MB, warm 0.74 -> 0.25 s, same counts.
2. **The excluded-work-model test reading blobs.** With an excluded way of
   working the planner can no longer use the covering gates index (it does not
   hold `work_model`) and reads `job_match` rows; `work_model` sits after
   `result_json`, so each read walks ~15 KB. Measured on one count: 137 MB to
   read `work_model` of 30,000 rows, 4 MB for `seniority` (in the index).
   Asking `rowid NOT IN (SELECT rowid FROM job_match WHERE work_model IN
   (...))` (served by `idx_job_match_work_model`, migration 0017) brings the
   covering index back: 330 -> 84 MB on that count.
3. **The same revision read ten times.** After 1 and 2, separate counts are
   cheap when the OS cache is warm, but each still re-runs its joins and
   subqueries; a search re-runs its full-text subquery in every count (1.1 GB
   per request with `search=engineer`).

## Changes

All in `src/career_agent/storage/mvp_repo.py`, plus five lines in
`JobsApi._narrowings`.

1. Lane clause: `FROM job_raw r INDEXED BY idx_job_raw_chars`. Applies to every
   query with a lane (list page, totals, facets, Home's counts), not only the
   narrowings. The index exists in every migrated database (0054) and is
   carried by the catalogue split; a database without it fails loudly
   ("no such index") rather than silently reading every advert.
2. Excluded work models: `(jm.rowid NOT IN (SELECT rowid FROM job_match WHERE
   work_model IN (...)) OR <tracked exemption>)` instead of `(jm.work_model IS
   NULL OR jm.work_model NOT IN (...) OR ...)`. Same rows: a NULL and every
   other value are not in the set, and a rowid is never NULL.
3. `ScoredJobQuery.count_together(config_id, version, ask)`: runs `ask` (the
   unchanged comprehension of `_narrowings`) once with every `count` answering
   0 to learn which filters it counts, counts them all in one pass
   (`_count_many`), then runs `ask` again with each `count` answered from that
   pass. The populations are still defined only by the `hidden_*` methods and
   `_where`; nothing restates them. `_where` now exposes its clause list
   (`_where_parts`); the pass keeps the clauses every filter shares as its
   WHERE, turns each other distinct clause into one bit of a per-row mask, and
   a filter's count is the number of rows (or `(company_id, title)` groups with
   a row) holding all of its bits. Filters that group differently or need a
   different inner join are counted in separate passes; more than 60 distinct
   clauses falls back to separate counts. One clock for the whole call (the
   separate counts each read their own `now`).

Not changed: the API answer, its fields, `narrowings=later`, the cache key and
TTL, freshness, which filters exist and what they mean, matching, scoring,
eligibility, the client.

## Equivalence

* `tests/integration/test_narrowings_one_pass.py` (committed): a 700-posting
  corpus cloned from the demo with every attribute a narrowing reads varied
  from a fixed seed (eligibility, screening, duplicates, saved/tracked/hidden,
  REMOVED, old/undated/pasted/still-listed, short adverts, levels, ways of
  working, an older revision); 162 random filters (lane, grouping, every
  include flag, exclusions, age, tracking exemption, statuses, saved only,
  eligibility and worksite chips, search, keywords, minimum score, remote)
  compared count by count, one pass against the separate counts; one SELECT
  per pass asserted; the route answers equal the methods; the work-model
  clause selects the same `job_match` ids as the column test for four value
  lists; the clause/value pairing fails loudly when it does not add up. A
  deliberate mutation of the mask test fails two of these tests.
* Cross-version A/B (scratch harness, not committed): the `dd581cb` source and
  this branch answer the same 36 queries (every include flag, lanes, grouping,
  search including punctuation only, keywords, chips, statuses, saved only,
  hidden only, five sorts, soft words, minimum salary, posted within) through
  `job_narrowings`, `list_jobs` with `narrowings=later`, and `list_jobs`
  counting them inline. Compared whole (cards, facets, totals, hidden counts,
  revision; only the freshness token is dropped, as it hashes this server's
  state). 30,000 corpus: 108 answers, 0 differ. Mutated corpus: 108 answers,
  0 differ, and every narrowing is non-zero in 28-34 of the 36 queries.
* Not exercised here and why it holds: two profiles (each profile has its own
  `JobsApi` and connection; nothing in the change is shared across profiles,
  and the cache key is unchanged); catalogue, preference and note changes and
  freshness moves (the change is below the cache: a moved key recounts exactly
  as before); concurrent answers (the per-request repository holds the pass
  state; `test_web_api`/`test_writes_counter` pass); a cancelled request
  (server-side work is not cancelled, before or after; it is now shorter).

## Before and after (same corpus, same machine, alternating order)

`/api/jobs/narrowings` handler, cold = first request on a cold copy, warm =
two repeats with the application cache cleared; `idx` is changes 1 and 2
without the single pass.

Medians of 3 rounds (4 for the first row), range in brackets; `read` per
cold request.

| Corpus, query | old cold | `idx` cold | new cold | old warm | new warm | read old -> new | cold / warm |
|---|---|---|---|---|---|---|---|
| 30k, default (ungrouped) | 10.20 s [9.43-10.60] | | 1.32 s [1.19-1.46] | 0.74 s | 0.22 s | 444 -> 97 MB | -87% / -70% |
| 30k, grouped (cards) | 8.09 s [8.07-8.29] | 0.84 s | 1.15 s [0.76-1.19] | 1.18 s | 0.39 s | 444 -> 93 MB | -86% / -67% |
| mutated 30k, default | 33.48 s [17.71-34.95] | 3.13 s | 3.13 s [1.43-4.31] | 4.90 s | 0.77 s | 1,184 -> 176 MB | -91% / -84% |
| mutated 30k, grouped | 14.94 s [14.54-16.73] | 1.17 s | 1.04 s [0.96-1.40] | 2.77 s | 0.62 s | 1,184 -> 171 MB | -93% / -77% |
| 90k, default | 32.84 s [26.75-34.00] | 4.08 s | 3.85 s [3.67-5.54] | 3.71 s | 1.38 s | 1,451 -> 456 MB | -88% / -63% |
| 90k, grouped (cards) | 29.18 s [26.89-38.86] | 3.99 s | 3.96 s [3.75-4.00] | 4.00 s | 1.72 s | 1,454 -> 448 MB | -86% / -57% |
| 90k, grouped + `search=engineer` | 30.43 s [27.26-31.22] | 15.03 s | 7.96 s [7.92-11.84] | 9.87 s | 2.30 s | 3,864 -> 646 MB | -74% / -77% |

The two default (ungrouped) 30k rows come from earlier runs; the only change
made after them (one sort for grouped counts) does not touch ungrouped
queries, and the plain corpus has no exclusion for the work-model change to
touch. Every other row is the final code. CPU per cold request fell in proportion (90k grouped 10.9 -> 2.1 s).
Scaling is linear: 30k -> 90k multiplies old by ~3.2 and new by ~3.0.

What the single pass adds over the two index changes alone: level on the
plain grouped query at 90k (3.99 vs 3.96 s cold, 1.70 vs 1.72 s warm), 0.3 s
slower on it at 30k, and much faster wherever the counts repeat costly work:
a search (cold 15.0 -> 8.0 s, warm 9.0 -> 2.3 s at 90k), exclusions and
applications (mutated, warm 1.2 -> 0.8 s). It is kept for those.

Warm reductions are smaller than cold ones because the remaining cost is the
one read every count shares (the gates index, a `job` row, two `job_raw`
index probes, the availability and application probes per posting), which
is what the single pass already does once.

Other screens (fresh process on a cold copy, default query; 3 rounds each):

| Measure | 30,000 old | 30,000 new | mutated old | mutated new |
|---|---|---|---|---|
| `/api/home` first | 1.40-1.55 s | 1.04-1.34 s | 4.04-4.54 s | 1.38-2.00 s |
| list first, alone (7 / 3 rounds, median) | 1.92 s | 2.00 s | 4.72 s | 2.24 s |
| narrowings right after the list | 7.55-8.80 s | 0.68-0.83 s | 15.9-17.2 s | 0.79-0.90 s |
| list again, warm | 0.46-0.50 s | 0.38-0.48 s | 0.53-0.54 s | 0.45-0.55 s |
| narrowings beside Home (narrowings / Home) | 1.11-1.19 / 0.24-0.28 s | 0.30-0.40 / 0.17-0.21 s | 1.83-1.94 / 0.71-0.74 s | 0.45-0.55 / 0.25-0.31 s |
| narrowings beside the list (narrowings / list) | 1.11-1.32 / 0.50-0.54 s | 0.44-0.51 / 0.44-0.51 s | 1.93-2.05 / 0.62-0.65 s | 0.57-0.62 / 0.56-0.61 s |

The list read alone on the plain corpus moved +4% in median over 7 rounds,
inside the run-to-run spread (1.65-2.19 s old, 1.77-2.47 s new); the grouped
read after it went 0.54 -> 0.48 s.

## Effects on Home, Tailor and Applications

* Home: asks no narrowings; its counts carry the lane clause and the
  work-model clause, so they read less (first Home 4.0-4.5 -> 1.4-2.0 s on the
  mutated corpus).
* Resume Tailor: lists with `narrowings=later`, unchanged; its list reads use
  the same `_where`.
* Applications: `status=...` lists, tracking exemption on; covered by the A/B
  (`status`, `saved_only`, `user_hidden_only`), 0 differences.
* Frontend: no file changed. Behaviour is what Phase 2 left (narrowings only
  on Find jobs and Applications, after the list; stale answers ignored by the
  token checks), verified by the browser suite below.

## Gates and suites (on `18d00d5`, sequential, own temporary folders)

| Step | Result |
|---|---|
| focused integration (27 files: filters, find jobs, deep, caches, web API, hidden, age, population, eligibility, multi-profile, local profiles, writes counter, exports, Home during refresh, Home tasks, applications, Tailor v1, storage equivalence, recovery, daily, leads) | 627 passed, exit 0 |
| `pytest tests/unit tests/integration` | 13,610 passed, 8 skipped, exit 0 (48 min) |
| `pytest tests/browser` | 645 passed, exit 0 (31 min); 6 `SyntaxWarning`s in `test_home_during_refresh.py`, pre-existing; the known onboarding persona and Edge-profile flakes did not appear |
| `ruff check src tests scripts`, `ruff format --check` | clean |
| `mypy src` | 3 errors in `web/export.py` and `evaluation/ranking.py`, files this branch does not touch (pre-existing) |
| `node scripts/frontend_check.mjs`, localisation, punctuation | clean |

## Residual risks

* `INDEXED BY idx_job_raw_chars` makes the index mandatory for every lane
  query. Every migrated database has it (0054, `IF NOT EXISTS`; the catalogue
  split carries it; `deep_run.py` already depends on it). If an install lacked
  it, Find jobs would answer an error instead of a slow list. Worth one
  `SELECT name FROM sqlite_master` check on the real install before merging.
* The rowid form of the work-model clause relies on `job_match` being a rowid
  table (it is: `id TEXT PRIMARY KEY`, no `WITHOUT ROWID`).
* `count_together` assumes `ask` does not branch on a count's value. True of
  every `hidden_*` method and of `_narrowings`; a future method that branches
  would be counted with 0 in the first run and could ask a filter the pass did
  not count, which raises `KeyError` (loud, not wrong).
* The clause/value pairing counts `?` per clause; every inlined literal in
  `_where_parts` is a module constant without `?`, and a mismatch raises.
* The pinned clock: the separate counts each took their own `now`; one request
  now cuts every population on the same moment (seconds apart before).
* Grouped requests sort the union of the populations once instead of each
  population separately; on the plain corpus this is level with the separate
  counts warm, faster with search or more narrowings.

## Limits of these measurements

* Synthetic data at 30,000 and 90,000 postings, not 172,000; the real install
  has larger adverts and `result_json`, a 15.8 GB footprint and less free RAM,
  so its cold I/O is worse than any copy here.
* Windows file cache could not be dropped; cold runs use unbuffered copies,
  which is cold for the database files only (Python and the code were warm).
* The machine was shared with other work during the runs; ranges are wide.
* `read` counts file reads including OS-cache hits; it is a volume, not disk
  traffic.

## Proposed real validation (needs the owner's approval)

Same harness as Phase 2 (server `--no-open`, auto-update answered in the
browser, GET only): open Find jobs once in cards view and record
`/api/jobs` and `/api/jobs/narrowings` first and second time, Home first,
Applications, Tailor Start. Before that, read-only: confirm
`idx_job_raw_chars` exists in the catalogue and `idx_job_match_work_model` in
the profile database. Target: narrowings at most 15 s on the first opening.
