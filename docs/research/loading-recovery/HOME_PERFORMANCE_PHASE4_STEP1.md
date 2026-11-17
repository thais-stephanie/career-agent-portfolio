# Phase 4, Step 1: Home derives only the deep levels it can use (2026-11-16)

Branch `claude/home-perf-phase4-step1`, from `main` at `8a43fb0` with the
diagnosis commits (`2afcb39`, `cee754a`, `6c30137`) brought in by
fast-forward. Not merged, not pushed. No migration, schema, contract, cache,
TTL or freshness change. No real database was opened: every measurement here
is on synthetic corpora.

**Step 1b verdict: READY FOR INTEGRATION REVIEW.** The owner chose B: a
memory-only memo of the parts Home derives, under the whole map's own key
(section "Step 1b" below). The first miss keeps the Step 1 gain (12%: 2.47 ->
0.86 s), a later miss under the same key derives nothing (0.54 -> 0.05 s, the
`8a43fb0` cost), 120 of 120 answers are byte-identical to `8a43fb0`, the
whole map is never written from a part, and TTL, freshness and the API are
unchanged. Left open: the key's existing blind spots, no longer in time or
postings than `8a43fb0`, but able to mix epochs in one answer (7b, residual
risks). A final review on 2026-11-17 found no BLOCKER or MAJOR; its one code
fix is in (7b). Still synthetic only:
the real figures need the separately authorised validation.

**Step 1 verdict (before the owner's decision): NEEDS OWNER DECISION.** The change is correct on every synthetic
check (36 of 36 byte-identical answers against the `8a43fb0` code, 24 new
tests, full suites green) and cuts Home's first miss by 3.5x at the real
profile's share. One trade-off needs the owner: a later Home miss, after the
600 s memo, now re-derives the admitted subset (0.09 s -> 0.44 s at 12%),
because a part is never stored as the whole map. Removing that cost needs a
new derived-levels memo, which this task did not authorise.

- **Option A**: accept the trade-off; the branch is then ready for
  integration review as it stands.
- **Option B**: authorise a separate memo of the derived subset keyed by the
  same levels key (no TTL change, never read as the whole map), as a Step 1b.

## The problem (from the real read-only profiling, diagnosis section 12)

Home's lists passed the WHOLE map of deep-reading levels to its section
pages. Building it derives every read posting's level (`deep_assess.assess`
for each newest review): 2,367 assessments for 2,127 read postings on the
real profile, 7.0 to 13.5 s per Home miss, 54-86% of Home. Only 244 of those
postings were open and eligible under the revision in force, so only their
levels could ever reach Home's pages.

## What changed

| Where | Change |
|---|---|
| `storage/mvp_repo.py` | `ScoredJobQuery.ids_among(config_id, config_version, f, job_ids)`: which of `job_ids` pass `f`'s filters, duplicate grouping aside. The WHERE is `page`'s own (`_where_parts`), plus `jm.job_id IN (...)` in chunks of 500. Refuses a filter with `deep` set |
| `semantic/deep_run.py` | `DeepRepo.reviewed_job_ids()`: every posting `newest_per_job()` can answer for when asked for all (the postings a review was asked for, and every posting holding a reviewed advert, i.e. a repost), from the review's two key columns |
| `web/deep_api.py` | `_levels_key` (the whole map's key, extracted unchanged from `deep_levels`); `reviewed_postings`; `deep_levels_within(app, conn, ids)`: the remembered whole map's entries for `ids` when it is current, else only those derived (`_review_rows(app, conn, ids)`) and **nothing stored** |
| `web/api.py` | `JobsApi.deep_levels_within(conn, ids)` in `JobFilter.deep_levels` form; `_level_entries` shared with `deep_levels()` |
| `web/workspace_api.py` | `_home_lists_now` chooses its sections first, asks `_home_deep_levels` for their levels, and reads the levels and the pages in ONE read snapshot (`BEGIN` ... `COMMIT`, deferred, read-only); `_home_deep_levels` falls back to the whole map if any section filters by the reading |

`JobsApi.deep_levels()`, `deep_api.deep_levels`, `deep_relations`, the Jobs
list, the narrowings, `daily`, the export and every other consumer are
unchanged and still get the whole map.

## How the relevant postings are found

1. **R** = `DeepRepo.reviewed_job_ids()`: the exact set of postings the whole
   map can hold a level for. `newest_per_job(None)` walks the postings named
   by a review's `job_id` and the postings whose `content_hash` a review has;
   `R` is those two lookups.
2. For each Home section filter (`dc_replace(section.job_filter, sort="deep",
   lane="shortlist")`, the very filters the pages use), `ids_among` keeps the
   members of R that pass the section's WHERE with grouping off.
3. The union of the sections' results is derived (or read from the
   remembered whole map), and that one tuple is given to every page, so
   `temp.deep_level` is filled once per Home miss.

Nothing is restated: no hand-written eligibility, age, hidden or lane test;
`ids_among` reuses `_where_parts`, the only place the filters are built.
No number (244 or any other) is in the code.

## Why the answer cannot change

- **A page reads a level only in its ORDER BY.** With `deep` empty (Home's
  sections), `_where_parts` reads no level; `_deep_order` looks up
  `temp.deep_level` for each row the WHERE kept. The duplicate election
  (`_representative_clause`) ranks by eligibility, score and id, never by
  level; `_siblings_for`, the soft order and the card read none. So only the
  levels of rows a page returns matter.
- **Every row a page returns is in the derived set.** The grouped WHERE is
  the ungrouped clauses plus "is the elected row of its group", and the
  elected row passes the ungrouped clauses. A read posting on a page is in R
  (its reading is its own or its advert's), so it is in `ids_among`'s
  answer. A posting outside R has no level in the whole map either.
- **Each level is the whole map's.** For a requested posting,
  `newest_per_job(ids)` fetches every review of the posting and every review
  of its current advert: all the candidates the whole walk chooses from,
  ordered the same way. `_review_rows` derives each posting from that row and
  the profile-wide context only. `test_levels_for_a_set_are_the_whole_maps_entries_for_it`
  checks it for random sets, unknown ids and reposts, with and without the
  remembered map.
- **One snapshot.** Without it, a score written between `ids_among` and the
  pages (by a collection or a recalculation running beside Home) could put a
  read posting on a page without its level, sorted as unread. With it, that
  answer is the one before the write and says so (`lists_current: false`),
  and the next answer is the whole-map one. The test fails with the snapshot
  disabled.

## The whole-map cache

- Read, never written, by Home: `deep_levels_within` uses
  `_deep_levels_cache` only when its key is the current `_levels_key`
  (same lock); otherwise it derives the set and stores nothing, and the
  relations cache is untouched.
- A remembered map under any other key (another revision, another reading,
  another population) is ignored (`test_a_remembered_map_of_another_key_is_never_used`).
- Each profile has its own `JobsApi` and therefore its own cache
  (`test_two_profiles_never_share_levels`).

## Tests

New file `tests/integration/test_home_deep_levels_within.py`, 24 tests, on a
synthetic corpus with stored readings (the suite's fake CLI, then copied onto
other postings with every work relation and support so every recommendation
level occurs): duplicate groups, reposts sharing a reading, eligible,
ineligible, unresolved and screened-out scores, closed and old postings,
excluded seniority and way of working, stale by title, evidence changed,
hidden, applied and saved postings, a review checkpoint.

| What | How |
|---|---|
| Home's whole answer equals the whole-map answer | `home` against `home_whole_map` (the `8a43fb0` behaviour: every page gets `deep_levels()[0]`), only `lists_at` (the moment counted) removed |
| ... with no cache, the whole map remembered, a map under another key | three tests |
| ... after the catalogue moves (a read advert retitled, unread postings closed, the population counter moved), during a collection, after the memo expires | two tests |
| ... over corpora of every shape: no postings, no readings, few, many | parametrised |
| ... when every reading is of a posting Home cannot show | and no reading is derived at all |
| Levels for a set = the whole map's entries for it | 6 random sets, unknown ids, cache on and off |
| R covers every posting the whole map holds | |
| Home derives only admitted readings; never stores a part as the whole map; Find jobs still gets every level | three tests that **fail on the old behaviour** (running the file with a plugin restoring it: 4 failed, 20 passed; the fourth is the write test below) |
| A `deep` section filter gets the whole map; `ids_among` refuses it | |
| A write between the levels and the pages | fails without the snapshot |

**Independent comparison with the `8a43fb0` code itself.** The diagnosis
worktree (`8a43fb0` code, only documentation added) and this branch ran
Home in separate processes, each with its own code, on the same synthetic
corpora; the whole answers (minus `lists_at`) were written to files and
compared byte for byte: **36 of 36 identical** (4 corpora x 3 states x 3
rounds), plus the small corpus in both states.

## Benchmark (synthetic, same machine, alternating order, one process per run)

Corpora: 3,000 postings, 2,806 reviews of 2,106 read postings, each review
carrying eight requirements (as real ones do: 18,011 requirements over 2,367
assessments), 10% of the read postings out of date by title; the share of
read postings Home can show varied from 5% to 100% (12% is the real
profile's 244 / 2,127). Medians of 3 rounds [range].

| Share | State | Base wall | Step 1 wall | `assess` base -> step 1 | Derivation base -> step 1 | CPU base -> step 1 |
|---|---|---|---|---|---|---|
| 5% | first Home miss | 2.28 s [2.23-2.61] | **0.66 s** [0.51-0.72] | 2,856 -> 210 | 2.01 -> 0.22 s | 1.77 -> 0.31 s |
| 12% | first Home miss | 2.76 s [2.40-2.80] | **0.78 s** [0.51-0.84] | 2,856 -> 506 | 2.56 -> 0.23 s | 2.30 -> 0.33 s |
| 50% | first Home miss | 2.68 s [2.43-2.89] | 1.80 s [1.45-1.97] | 2,856 -> 1,803 | 2.39 -> 1.39 s | 2.22 -> 1.42 s |
| 100% | first Home miss | 2.71 s [2.44-3.12] | 2.89 s [2.48-2.95] | 2,856 -> 2,856 | 2.42 -> 2.36 s | 2.33 -> 2.38 s |
| 5% | a later Home miss | 0.08 s | 0.23 s | 0 -> 210 | 0 -> 0.14 s | |
| 12% | a later Home miss | 0.09 s | **0.44 s** | 0 -> 506 | 0 -> 0.37 s | |
| 50% | a later Home miss | 0.12 s | 1.53 s | 0 -> 1,803 | 0 -> 1.38 s | |
| 100% | a later Home miss | 0.11 s | 2.13 s | 0 -> 2,856 | 0 -> 1.96 s | |
| any | whole map already remembered | 0.09-0.13 s | 0.11-0.20 s | 0 -> 0 | 0 | |

(`assess` counts include reposts, which share a reading and get a level of
their own; reviews read = assessments. Peak working set 112 -> 80 MB at 5-12%.
Bytes read include the OS cache.)

- **The gain is fewer assessments, not caching**: on a first miss the
  derivation does 7-18% of the work at 5-12% share; CPU falls with it.
- **The cost moved: a later Home miss** (the memo dropped by a write the
  server accepted, a save, a hide, or the 600 s bound, while the levels key
  is unchanged) used to read the whole map remembered by the first miss for
  free, and now derives its subset again. Found by the independent review
  (finding 1) and measured above.
- With the whole map already remembered (after Find jobs' deep sort, say),
  both are equal: Home reads it.

**Estimate for the real profile (hypothesis, not measured):** 2,367
assessments took 7.0-10.2 s warm; about 250-500 (the admitted postings and
their reposts) would take about 0.7-2 s. First Home miss after a restart:
8.6-11.8 s warm -> about 2-3 s; 25 s -> about 12-15 s on a first read (the
cold SQL of the pages, the grouped total and the open count remains). A later
Home miss: about +0.7-2 s compared with today (which then paid 0 for levels).

## Independent review

A separate agent read the change and tried to break it, writing its own
tests in a scratch folder (not in the repository):

| Finding | Severity | Outcome |
|---|---|---|
| Restricted population safe: page reads levels only in ORDER BY, election never by level, admitted set covers every returned row, parameter order right, FROM a subset of the page's, time clauses only drop rows, `newest_per_job(subset)` picks the same review, cache never written | n/a | confirmed, no counterexample |
| 1. Later Home misses cost more: (new 40, 40 assessments; old 51, 0) | MAJOR (performance) | **reproduced and measured** (table above). Fixing it needs a separate memo of derived levels: a new cache. **Owner decision** |
| 2. `ids_among` could scan the whole section population for each 500-id chunk (`j.id IN` tested after the `job` lookup) | MAJOR (performance risk) | **fixed**: the test is now `jm.job_id IN (...)`, evaluated in the driving covering index before `job` is read (same result: the join is `j.id = jm.job_id`); plan checked |
| 3. A write between `ids_among` and the pages could leave a row without its level | NIT (not visible: the recount after a moved token covers it outside a run) | **fixed**: one read snapshot; a regression test that fails without it |

## Gates and suites

Static gates on the branch: ruff check and ruff format clean; mypy shows the
same 3 errors as `8a43fb0` (`export.py:253`, `ranking.py:327`,
`ranking.py:352`), none in changed files; frontend check clean (60 modules);
localisation and punctuation gates clean.

| Suite | Result | Exit code |
|---|---|---|
| `tests/unit tests/integration` | 13,636 passed, 8 skipped (corrected 2026-11-17: first counted as 13,603) | 0 |
| `tests/browser` | 645 passed | 0 |
| `tests/integration/test_home_deep_levels_within.py` | 24 passed | 0 |
| same file with the old-behaviour plugin | 4 failed, 20 passed (expected) | 1 |

The suites ran one at a time, never alongside another pytest session, on
synthetic temporary databases only.

## Isolation of the real install

- No real database was opened, copied or read; `launch.py` was not run; no
  collection, rescore, deep reading, provider or network call.
- Every benchmark script (`guard.py` in the scratch folder) refuses any path
  under the live install or outside the scratch folder before opening
  anything, with no fallback; its self-test runs on synthetic paths first.
  The comparison script was also called once with the live data folder as
  its argument to show the refusal: it exited before importing or opening
  anything.
- The tests use pytest temporary folders only.

## Residual risks

- **Later Home misses** (finding 1): a regression of about +0.4 s at the
  real share on the synthetic corpus, estimated +0.7-2 s on the real profile,
  on every Home miss after the first while the levels key holds. **Removed by
  Step 1b** (the owner chose B, below).
- The gain shrinks as the share of read postings Home can show grows: at
  100% it is nil (and a first miss reads 26 MB more for the `ids_among`
  queries). The real share was 12%.
- The snapshot holds a WAL read transaction for the length of the levels and
  the pages (seconds on a cold disk); writers are not blocked (WAL), the
  checkpoint only waits for it to end.
- The real figures are estimates until the real validation.

## Decision (the owner chose B on 2026-11-16, implemented as Step 1b)

Keep the derived subset for later Home misses?

- **A. Accept as is.** First Home miss much cheaper (the slow one after a
  restart or a collection), later misses about +0.7-2 s (est.) until something
  computes the whole map.
- **B. Authorise a memo of derived levels**, keyed by the same
  `_levels_key`, separate from `_deep_levels_cache`, adding the postings a
  later miss lacks and never presented as the whole map: later misses back to
  about 0 for levels. A new in-memory cache (no persistence, no TTL, the
  existing key): by the rules of this phase, the owner's decision.

## Step 1b: the memo of parts (owner decision B, 2026-11-16)

Step 1 never stored what it derived, so every Home miss after the first
(the 600 s memo expired, a save, a hide) derived the same part again. Step 1b
keeps it, separately, without ever deriving the whole map on Home's first miss.

### 1. Structure

`app._deep_levels_part = (key, levels, asked)` on each profile's `JobsApi`
(each profile has its own, so nothing is shared between profiles):

- `key`: the whole map's `_levels_key` at derivation time;
- `levels`: `job_id -> (level, stale, state)`, exactly the whole map's entry;
- `asked`: every posting asked under this key, with or without a level, so a
  posting without a level is remembered as asked and never derived again.

Memory only, never persisted, never written into `_deep_levels_cache` or
`_deep_relations_cache`, never read by `deep_levels()` (Find jobs and every
other consumer of the whole map are unchanged).

### 2. Validity

The same key as the whole map, nothing weaker: the readings' count and newest
`created_at`, the reading contract, both profile context digests (intent,
levels, confirmed evidence and its revisions), the population token (any
posting or score written) and the search configuration. Another key starts
the memo empty: there is one key at a time, so no old generation is kept.
When `deep_levels()` derives the whole map under the same key, the memo goes
(the whole map answers every part). Home's own memo (600 s), the freshness
rules and the API answers are unchanged.

The population Home asks for is NOT memorised: every miss still finds it
with the sections' real filters (`ids_among`), so time, filters, marks and
availability move it as before; only the levels of postings already asked are
reused.

### 3. Incremental use (`deep_levels_within`)

1. The whole map, when remembered under the current key: used as in Step 1.
2. Else the memo under the current key (or a new empty one).
3. The postings asked and not in `asked` are the only ones derived
   (`_review_rows` for them alone).
4. They are added (levels and `asked`) only if the memo still holds the key
   they were derived under; the answer is the requested postings' entries.

### 4. Concurrency and snapshots

- The key and the derivation read ONE snapshot: Home's (Step 1), or one
  `deep_levels_within` opens itself for a caller without one (review NIT 3).
  So what is derived matches the key it is filed under, and a write between
  them waits for the next key.
- The lock (`_LEVELS_LOCK`) is held only to read and to add, never during the
  derivation.
- A derivation finished after another call installed another key is NOT
  added (it still answers its own call).
- Two Homes asking the same postings at once may both derive them (the same
  result twice, never a wrong one); overlapping sets add their own postings.
- A reader of an older snapshot can take the memo back to its key: the newer
  entries are lost, which costs time, never correctness.

### 5. Benchmark (synthetic, A = `8a43fb0`, B = Step 1 `1727033`, C = Step 1b)

Same corpora as Step 1 (3,000 postings, 2,806 reviews of 2,106 read
postings, eight requirements per review, 10% out of date by title), every
run on a fresh copy of the corpus, one process per run, the order rotated,
3 rounds. Medians, seconds, with `deep_assess.assess` calls.

| Share Home can show | First miss A / B / C | assess | Later miss (memo expired) A / B / C | assess B / C |
|---|---|---|---|---|
| 0% | 3.50 / 0.61 / 0.57 | 2856 / 0 / 0 | 0.09 / 0.07 / 0.10 | 0 / 0 |
| 5% | 2.64 / 0.68 / 0.56 | 2856 / 210 / 210 | 0.09 / 0.23 / 0.10 | 210 / 0 |
| 12% | 2.47 / 0.96 / 0.86 | 2856 / 506 / 506 | 0.09 / 0.54 / 0.05 | 506 / 0 |
| 50% | 2.58 / 1.91 / 1.69 | 2856 / 1803 / 1803 | 0.07 / 1.50 / 0.09 | 1803 / 0 |
| 100% | 3.85 / 5.21 / 4.69 | 2856 / 2856 / 2856 | 0.16 / 4.79 / 0.19 | 2856 / 0 |

Other states at 12% (A / B / C seconds; assessments of C):

| State | A | B | C | C assess |
|---|---|---|---|---|
| after a save and a hide | 0.10 | 0.60 | 0.13 | 0 |
| during a collection, memo expired | 0.06 | 0.56 | 0.07 | 0 |
| after a catalogue change (key moves) | 2.23 | 0.56 | 0.60 | 502 |
| after a reading changes (key moves) | 2.30 | 0.47 | 0.47 | 506 |
| whole map already remembered | 0.11 | 0.07 | 0.11 | 0 |
| two profiles, each Home twice | 0.15 | 0.67 | 0.07 | 0 |

- The first-miss gain of Step 1 is kept (12%: 2.47 -> 0.86 s, 2.9x here,
  3.5x in the Step 1 run), and a later miss under the same key derives
  nothing: 0.05-0.19 s, the `8a43fb0` cost, at every share.
- When the key moves (a reading, the catalogue, a score), C derives the part
  again, as B does; `8a43fb0` derived the whole map.
- At 100% the machine was noisy (one state rerun three times: A 5.6-8.2 s,
  B 4.9-6.7 s, C 4.0-7.4 s, identical answers and 2,856 assessments each): no
  difference between the three beyond the noise.
- Memory: the memo holds 506 entries at 12% (21.6 KB pickled), 2,856 at 100%
  (124 KB). Peak process memory did not move (C 79-80 MB against B 79-80 MB
  at 12%; 113 MB for all three at 100%).

### 6. Equivalence

Every run wrote Home's whole answer (and, for the two-step states, the first
answer too; for two profiles, both profiles' answers), minus `lists_at`, the
moment the lists were counted. **120 of 120 comparisons byte-identical, A = B
and A = C** (5 shares x 8 states x 3 rounds): first miss, later miss, after a
save and a hide, after a catalogue change, after a reading change, during a
collection, with the whole map remembered, and two profiles.

### 7. Independent review

A reviewer read the diff and the code it depends on, ran nothing.

| Finding | Severity | Outcome |
|---|---|---|
| Is `_levels_key` enough for parts? Exactly as much as for the whole map: each posting's level depends only on its own readings, and everything `_review_rows` reads moves the key | (question) | confirmed |
| The key has two existing blind spots: a review replaced in place (`DeepRepo.save` upsert) in the same second as another save keeps COUNT and MAX; `backfill_digests` writes `intent_digest` without moving it | MINOR | **not changed**: `8a43fb0`'s Home read the whole-map cache under the same key, so the time and the postings exposed are not larger; the review of 2026-11-17 found one new effect, mixed epochs (7b). A trigger-kept reading revision in the stamp would close it; that is a migration, the owner's decision (residual risks) |
| A reader of an older snapshot resets the memo to its key | NIT | time only; documented in code |
| Correctness relies on the caller holding a snapshot | NIT | **fixed**: `deep_levels_within` opens one when the caller has none; a test fails without it |
| A test's last assertion was answered by the whole map, not the memo | NIT | **fixed** |
| A `deep` section filter reads the whole map outside Home's snapshot | NIT | existing, unchanged, no section uses it |

### 7b. Final integration review (2026-11-17)

A second reviewer read the diff `8a43fb0..fe5f250` and the code it depends
on before this document, and ran only the two new test files (43 passed).
No BLOCKER or MAJOR. Proven from the code: `ids_among` is a superset of every
page (the page's own conditions minus the representative clause); pages read
a level only for rows that passed their own conditions; the reading chosen
per posting with a subset equals the whole map's, reposts and shared content
included; a part is never read as the whole map; nothing derived under an
older key is stored under a newer one.

| Finding | Severity | Outcome |
|---|---|---|
| Under the key's blind spots the part memo can mix epochs: postings asked before an unseen event keep the old level, postings first asked after it (the admitted set grows) get the new one, in the same answer. `8a43fb0` derived the whole map in one pass, so all postings sat on one side of the event. Exposure time and the affected postings are not larger (both end only when the key moves; the part goes when Find jobs derives the whole map) | MINOR | **documented, not changed**: the previous claim "exactly that exposure, no new one" was wrong and is corrected here and in the residual risks. Closing it needs the key to see the blind spots (a trigger-kept revision: a migration, the owner's decision) |
| `COMMIT` in `finally` hid the real error when SQLite had rolled the transaction back itself (I/O error, interrupt): the caller saw "no transaction is active" | MINOR | **fixed** in `deep_levels_within` and in Home's snapshot (`COMMIT` only while a transaction is open); two tests failed before and pass after |
| The read transaction covers the key, the population, the missing derivations and three pages: a collection writing meanwhile cannot reset the WAL until it ends | MINOR | documented; bounded by Home's own time (under a second at 12% here) |
| `total` and `open_jobs` are counted before the snapshot | NIT | as in `8a43fb0`; `_home_lists_checked` covers a moved token |
| A whole map stored under Home's key may be derived from a newer state than the key (its key and rows are separate reads) | NIT | as in `8a43fb0`; only the blind spots can make it visible |
| Readers on older and newer snapshots can reset each other's memo | NIT | time only (already above) |
| `ids_among` would fail loudly with the shortlist lane's tracked exemption and no `ja` join; an `INDEXED BY` status hint would make its `IN` a scan | NIT | latent, no Home section reaches either; not changed |
| The `deep` section filter reads the whole map outside the snapshot | NIT | as in `8a43fb0`, no section uses it |

### 8. Tests

`tests/integration/test_home_deep_levels_memo.py`, 21 tests (19, and 2 from the final review): a later miss,
the 600 s memo, a save and a hide derive nothing; a wider population derives
only the newcomers; overlapping sets; a posting without a level is not
derived again; a replaced or removed reading, evidence, a retitled advert, a
score and the reading contract each start the memo again; the whole map is
never written from a part, supersedes it, and leaves a part under another key
alone; two profiles; a derivation under an old key is not added under a new
one; concurrent overlapping calls; a sequence of states with the memo warm;
a write between the key and the derivation. Every answer is compared with the
whole map's.

They fail where they should:

| Mutation | Fails |
|---|---|
| Step 1's `deep_levels_within` (no memo) | 15 of the first 18 (every test that reads the memo) |
| the memo ignores the key | the 6 key tests |
| a derivation is added under any key | the old-key test |
| no snapshot of its own | the write-between test |

Gates on the Step 1b tree, each run once, sequentially, in its own temporary
directory:

| Gate | Result | Exit |
|---|---|---|
| focused: memo (19) + Step 1 (24) | 43 passed | 0 |
| ruff check, ruff format (818 files) | clean | 0 |
| mypy | the 3 existing errors (`export.py:253`, `ranking.py:327`, `ranking.py:352`), no new one | 1 (as on `8a43fb0`) |
| punctuation, frontend, localisation | clean | 0 |
| unit + integration | 13,655 passed, 8 skipped (corrected 2026-11-17: first counted as 13,603) | 0 |
| browser, first run | 644 passed, 1 failed | 1 |
| browser, second run | 645 passed | 0 |

The browser failure was `test_keyboard_flows.py::test_the_age_limit_is_operated_with_the_keyboard`
(Tab reached the undated switch before it was disabled), not the known
onboarding flake. It was not taken as a pass: the test file then passed 5 of
5 alone and the whole browser suite passed again. Step 1b changes only
`web/deep_api.py`; that test exercises the Find jobs age filter's tab order
in the frontend, which reads no deep level. Recorded as a new intermittent
timing failure, to watch.

### 9. Residual risks

- The key's existing blind spots (review finding above): a reading replaced
  in place in the same second as another save, or a digest backfill, is not
  seen until something else moves the key, as in `8a43fb0` and Find jobs
  today. New with the memo: one Home answer can then mix postings read
  before and after such an event (7b).
- Any posting or score written moves the key, so during a collection or a
  recalculation the memo restarts often; that costs what Step 1 cost, never
  more.
- Two simultaneous Home misses may derive the same postings twice.
- The real figures are still estimates until a real validation.

## Validation on the real install (needs authorisation)

Read-only, the Phase 4 harness (mode=ro, authorizer, guards), no other suite
running: Home first miss and a later miss (memo expired), base and Step 1b,
alternating, with the assessment count, and the whole answer hashed for
both.
