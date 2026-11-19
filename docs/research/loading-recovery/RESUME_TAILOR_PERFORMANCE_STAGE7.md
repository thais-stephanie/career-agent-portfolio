# Resume Tailor load, Stage 7: one derivation for both lists

Branch `claude/resume-tailor-perf-stage7`, base `origin/main` `8ec7263`.
Written on 2026-11-18. Published on 2026-11-18 (section 7). The diagnosis that led here is
`RESUME_TAILOR_LOAD_DIAGNOSIS.md`.

## 1. What the Tailor asks

Opening the Tailor sends two `/api/jobs` requests at once:

- the saved list: `saved_only=true&group_duplicates=1&limit=24&narrowings=later`;
- the best list: `group_duplicates=1&limit=8&narrowings=later`.

Both filter and sort by the deep level of each posting, so each one calls
`deep_api.deep_levels`, which derives the whole map (`_whole_map`) when its
memo is empty.

## 2. Real profile before the change (code `9636ecf`, read only)

Three rounds on the live install, read only by construction (`mode=ro`, a
SQLite authorizer refusing writes, TEMP in memory, synthetic warm-up, a
synthetic self-test first). No server, no collection, no rescore, no LLM.
Zero errors, refusals or denials. The same answer every time.

| Scenario | Time |
|---|---|
| first list in a new process | 5.65 to 5.73 s (one round 17.2 s, with a 9.8 s cold `_serving`) |
| `deep_levels` inside it | 4.4 to 7.1 s, 2,367 readings assessed, about 75% of the list |
| second list, same process | 0.36 to 3.37 s |
| repeat, caches valid | 0.17 to 1.22 s |
| only the totals cache dropped (H1) | saved 0.19 to 0.90 s, best 0.60 to 2.14 s |
| only the level caches dropped (H2) | 3.3 to 8.3 s |
| **both at once, as the Tailor asks (H3)** | **10.3 to 11.8 s, 4,734 readings assessed** |
| both again | 0.92 to 1.05 s |

Reading:

- H1 (totals and facets the Tailor never shows) costs about 0.4 to 1.3 s.
  Small: not implemented.
- H2 (the deep levels) dominates any first list.
- H3: the two requests derived the same map side by side, twice the work, and
  each waited for its own derivation under contention. Both lists were ready
  only after 10 to 12 s.

## 3. The change

`deep_api._whole_map` now runs one derivation per key at a time:

- a request that finds the map remembered under its key returns it at once,
  even while another key derives;
- a request whose key is already being derived waits for that derivation,
  then reads the remembered map;
- a request of another key derives beside it, as before;
- if the derivation fails, its waiters are released and derive themselves;
- Home's parts (`deep_levels_within`) are untouched and never wait;
- each profile has its own app, so profiles never wait for each other.

It reuses the existing memo and its key. No new cache, no TTL, no migration,
no change to what is derived or how it is scored. The derivation body moved
unchanged into `_derive_whole_map`.

Known limits, accepted:

- `wait()` has no timeout: a derivation that hangs holds its waiters, as the
  first request would have hung on its own before;
- between two different keys the last writer of the memo wins, as before.

## 4. Tests and review

`tests/integration/test_deep_levels_single_flight.py`, 9 tests, synthetic and
deterministic (the first derivation is held on an Event; the tests count the
requests that really wait, nothing depends on sleeping):

- the Tailor's two lists at once derive the map once, with the same cards as alone;
- concurrent calls of one key share one derivation;
- another key derives beside and never waits;
- a remembered hit never waits while another key derives;
- a failed derivation releases its waiters;
- Home's parts never wait;
- two profiles never wait for each other;
- a later request derives nothing;
- a plain namespace app (`evaluation/ranking.py`) still derives.

Against `8ec7263`, three of them fail; against the first design, five fail.

An independent review of the first design found one should-fix: a remembered
hit waited behind another key's derivation, and moving keys were serialised.
The per-key design (`ab01cdc`) fixed it; the re-review found no blocker.

Gates on `fb4da1e`, run one after another:

- ruff, format, frontend, localisation and punctuation checks: pass;
- mypy: the 3 errors that predate this branch (`ranking.py` twice, `export.py`);
- unit and integration: 13,772 tests, 13,764 passed, 8 skipped, none failed;
- browser: 645 tests, 645 passed, none skipped or failed.

The first run of these suites read "about 570" browser tests. That count was
wrong, not the coverage: the command added `-q` to the `-q` already in
`addopts`, and `-qq` prints no summary line, so the count was rebuilt from
progress dots, some of them on lines shared with server logs. Before
publishing, both suites ran again with `-o addopts="--strict-markers"` and
JUnit XML. The executed tests equal `--collect-only` per test function; the
only differing ids are random parameters that `test_mvp_repo` draws at each
collection. The 8 skips need data a public checkout does not hold (the
programme ledger, the private golden corpus, operational data).

## 5. Real measurement after the change (code B)

Same harness, same three rounds, same live install, read only. Code B is
`9636ecf` with only this branch's `deep_api.py`, so the code matches the live
schema 61. Zero errors, refusals or denials.

| Scenario | Before (A) | After (B) |
|---|---|---|
| **both at once, both ready** | **10.3 to 11.8 s** | **4.9 to 5.9 s** |
| saved list ready, at once | 9.4 to 10.6 s | 4.2 to 5.0 s |
| readings assessed, at once | 4,734 | 2,367 |
| first list alone, new process | 5.65 to 5.73 s | 4.93 to 5.57 s |
| both again | 0.92 to 1.05 s | 1.02 to 1.27 s |

- Each list gives the same answer before and after (the same card ids in the
  same order, and the same freshness): saved `8de4919c59c46cf7`, best
  `77d0e532629f0550`.
- A sequential first list is unchanged, as expected: there was nothing to share.
- The first round of each run had a cold `_serving` (9.8 s before, 10.9 s
  after) and is not counted in the ranges above.
- Integrity before and after each run: the SHA-256 of the 24 database and WAL
  files unchanged, schema and counters unchanged; only SHM mtimes moved.

## 6. What remains

- A first opening still pays one derivation of the whole map, about 4 to 6 s
  warm. Restricting it to the postings a list can return (H2, as Step 1 did
  for Home) is a separate step, 7b, larger because a list can sort by level.
  Not started.
- H1 stays unimplemented unless a cold measurement shows the totals matter more.
- Cold start was not measured (`COLD_START_PROTOCOL.md`).

## 7. Publication

On 2026-11-18, `origin/main` moved by fast-forward from `8ec7263` to
`1d96e6b924e6fddcade9cb8297b62a79cbf9160e`, pushed from the branch
`claude/release-candidate-tailor-stage7`. `git ls-remote` before and after:
only `refs/heads/main` (and the remote `HEAD` that names it) moved.

Published commits: `35992f0`, `ab01cdc`, `fb4da1e` (code and tests), `82a95b5`,
`1d96e6b` (documents). Files: `src/career_agent/web/deep_api.py`,
`tests/integration/test_deep_levels_single_flight.py` and five documents in
this folder. No migration, no geography (`23d7051` and `fe8d946` are not
ancestors), no change to freshness, scores, eligibility, filters or the HTTP
contract. The `deep_api.py` measured as code B is byte-identical to the
published one. Stage 7 does not depend on Step 3: `deep_api.py` is the same
in `9636ecf` and `8ec7263`, and the list path does not call rescore planning.

Concurrency review before publishing, no blocker: one flight per key, other
keys derive beside, a remembered hit never waits, a failure releases its
waiters, profiles and Home's parts never wait, a plain namespace app works,
the lock is never held across a derivation or a wait, and the derivation
never re-enters `_whole_map`. Accepted limits are in section 3.

This section and the corrected counts in section 4 were written after the
gates, in a separate local commit that was not pushed.

Not done, each needs its own authorization:

- the install: the live checkout is still the local `main` `fe8d946` on
  schema 61, and the published code needs schema 63
  (`STEP3_OPERATIONAL_UPGRADE.md`, blocked on the geography decision);
- Step 7b (section 6);
- the cold-start run, whose harness uses this worktree's `.venv`
  (`COLD_START_PROTOCOL.md`, section 8).
