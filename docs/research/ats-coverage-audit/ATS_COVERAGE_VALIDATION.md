# ATS coverage audit: validation

## 1. Preflight (2026-11-04)

- `main` = `origin/main` = `ba53276`. The worktree `C:\Users\thais\ca-ats-audit`
  is on the new branch `claude/ats-coverage-audit`. The main worktree was not
  modified (only the untracked `jobspy_resultados.csv`, untouched).
- No Python process was running and no collector, pytest, refresh or Deep
  Reading was active. The maintenance, collect and profile locks were all free,
  and both WAL files were empty.
- Real databases were opened only with `mode=ro`. No profile, score, history or
  backup was touched, and no model was called.

## 2. Datasets used

| Dataset | Use |
|---|---|
| `data/shared/catalogue.db` (read-only) | boards, jobs, payloads, discovery leads, `source_health` |
| `data/personal.db`, table `pipeline_run` (read-only) | per-board counters, holds and failures of the 2026-10-26 and 2026-11-03 passes |
| 131,760 aggregator rows (URL + latest payload) | published links into the five families |
| public list endpoints | the seeded confirmation sample below |

The scratch scripts and their raw JSON lived in the session scratchpad and were
deleted afterwards. The CSV in this folder holds every measured value.

## 3. Public requests (70 in total, hard caps per script)

Selection was fixed before the first request: `random.Random(20261104)` over
sorted populations.

| Script | Cap | Spent | What |
|---|---|---|---|
| main sample | 80 | 43 | 17 registered boards (6 Greenhouse, 6 Ashby, 5 Lever) + `lever:jobgether` + 2 BambooHR. Also 13 unregistered boards (6 Ashby, 4 Greenhouse, 3 Lever) + 2 encoded-name Ashby boards. Plus 5 Lever paging pages and 2 Lever EU probes |
| follow-up | 20 | 16 | `meta.total` on 10 Greenhouse boards; `apply.workable.com/robots.txt`; 5 Workable account widgets |
| miss classification | 10 | 10 | re-listed the 10 registered boards with absent postings |
| encoded-name check | 1 | 1 | `posting-api/job-board/Flock%20Safety` |

All requests used the product's `HttpFetcher`: its own User-Agent, 2 s between
requests, 2 attempts, no cache. Planned requests by host (69):

- `api.ashbyhq.com`: 18
- `boards-api.greenhouse.io`: 23
- `api.lever.co`: 19
- `api.eu.lever.co`: 1
- `*.bamboohr.com`: 2
- `apply.workable.com`: 6

The fetcher counted 70. The extra one is a retry inside the main sample, which
logs no host.

`robots.txt` on `apply.workable.com` disallows nothing. The three JSON APIs are
the public board APIs the adapters already use. No CAPTCHA was met, nothing
needed authentication, and nothing was crawled or enumerated.

Stopping and expansion rules, fixed in advance:

- **Stop** at the cap, or on a 429 or a refusal from a host.
- **Expand** a sample only to settle a specific contradiction:
  - the 10 re-lists, to separate refresh lag from loss;
  - the paging probe, to test a hidden cap.
- **Insufficient evidence** when a result has more than one explanation the
  data cannot separate. The 17 "older, never seen" postings and the Paragon 404
  ended there.

## 4. Code changes

1. **`providers/ashby.py`: board recogniser accepts `%XX` escapes** in the
   board name and keeps them as published.
   - Before: `identify_board_url("https://jobs.ashbyhq.com/Flock%20Safety/<uuid>/application")`
     returned `None` (the new test failed).
   - After: it returns `("ashby", "Flock%20Safety", <uuid>)`.
   - Test: a new case in `tests/unit/test_board_recognisers.py`.
   - Profile-neutral, no data change, no board registered.
   - The tie test (`test_no_two_families_claim_one_url`) still passes.
2. **`docs/research/retrieval-source-expansion/RETRIEVAL_SOURCE_EXPANSION_VALIDATION.md`:**
   one en dash replaced. `tests/unit/test_punctuation_guard.py` **fails on
   `main` (`ba53276`)** because of a line written during the 2026-11-04
   integration. Found by this audit's regression run, fixed here.

No change to collection, closing, deduplication, schema, configuration or any board.

## 5. Tests

All runs used `PYTHONPATH=<worktree>/src`, while no collector was running.

| Run | Result |
|---|---|
| `tests/unit/test_board_recognisers.py` before the fix | 1 failed (the new encoded-name case) |
| recognisers + `test_ashby.py` + `test_bamboohr_collect.py` after the fix | all passed |
| first full `tests/unit tests/integration` run (`-x`) | stopped at `test_punctuation_guard.py`: the pre-existing en dash on `main` |
| full `tests/unit tests/integration` after both changes | **exit 0**: 13,153 tests collected, all passed except the suite's own skips |
| `ruff check src tests` | clean |

The browser suite was not run. The change is a URL pattern with no interface
path, and no test the browser suite runs touches the Ashby board recogniser.

## 6. Result

- The five ATS were audited and their pagination contracts documented.
- **No pagination or completeness defect was demonstrated.** No hidden Lever
  cap, Greenhouse totals exact, no incomplete pass closed anything.
- The demonstrated problems are quantified within the samples:
  - a discovery gap of 934 unique postings on 13 boards;
  - a Workable staleness of ≥ 31,922 inferred open rows (15 % measured);
  - one recognition defect, fixed;
  - one latent Lever EU defect, documented.
- Improvements are ranked in `ATS_COVERAGE_OPPORTUNITIES.md`.
- No real data was changed.
