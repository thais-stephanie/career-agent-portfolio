# Home load order, Phase 4 Step 4: measured, no change recommended

Code: the published `origin/main` (`9636ecf`, Steps 1c and 2). **Verdict: NO
CHANGE RECOMMENDED.** The frontend was not changed, and no Step 4 branch was created.

## 1. The order today

- `home.js` asks `/api/home` and `/api/firstrun` at once. The shell is drawn
  once firstrun answers, and the dashboard once Home answers.
- `main.js` asks `/api/health` as the page opens. The rail shares the
  in-flight Home read (`api.getHome`) and asks `/api/sources` only after Home.
- `home.js` asks `/api/home/outdated` only after it has drawn the dashboard,
  and only when Home's answer leaves the count `null`.

The Phase 4 diagnosis (section 12.5) found that starting sources and outdated
beside Home ended the page sooner (13.2 to 13.8 s down to 9.1 to 9.7 s), at a
time when Home took 8 to 10 s. Steps 1c and 2 changed the weights: Home is
1 to 2.5 s and sources 0.33 s alone. The recommendation had to be measured again.

## 2. Method

The read-only harness is `realload.py`, derived from the Step 2 harness. Every
guard is kept:

- `mode=ro`, an authorizer denying writes, TEMP in memory;
- guards on the product's `connect` and `attach`, explicit paths;
- a warm-up on a synthetic corpus only.

Its self-test passed on synthetic data, and each order ran there without
error before the real run. One opening per new process, from a fresh API
(no memo), with the handlers in threads as `ThreadingHTTPServer` runs them.
Three rounds of four orders, the order rotated (ABCD, DCBA, BADC). No
`launch.py`, POST, collection, rescore or LLM.

| Order | With Home | After Home answers |
|---|---|---|
| A (today) | health, firstrun | sources, outdated |
| B | health, firstrun, sources | outdated |
| C | health, firstrun, outdated | sources |
| D | health, firstrun, sources, outdated | |

## 3. Results on the real install (seconds from the opening, median [range])

| Order | shell (firstrun) | dashboard (Home) | sources | outdated | page end |
|---|---|---|---|---|---|
| A | 0.06 [0.04..0.49] | 1.55 [1.25..5.27] | 2.23 [1.95..9.71] | 3.21 [2.98..10.12] | 3.21 [2.98..10.12] |
| B | 0.05 [0.03..0.06] | 1.81 [1.50..2.17] | **0.74** [0.64..1.04] | 3.33 [2.90..3.69] | 3.33 [2.90..3.69] |
| C | 0.06 [0.06..0.11] | 2.71 [1.99..2.85] | 4.07 [2.78..5.11] | 3.77 [2.62..4.79] | 4.07 [2.79..5.11] |
| D | 0.06 [0.06..0.09] | 2.75 [2.43..4.18] | 1.58 [1.17..2.00] | 3.24 [2.75..4.64] | 3.24 [2.75..4.64] |

A's largest values come from its first round, the first read after the idle
period. They are not a cold-disk figure. Without that round, A is: dashboard
1.25 to 1.55, sources 1.95 to 2.23, page end 2.98 to 3.21.

Every answer was identical across the 12 openings, one hash per endpoint, and
there were no errors, refusals or denials. The machine showed 4 to 52% CPU
from other sessions and about 5 GB of free RAM.

## 4. Reading

- **The page ends when outdated ends** in every order: about 3 s. Outdated alone
  takes 1.4 to 1.7 s after Home. Beside Home it takes 2.6 to 4.8 s, because the
  handlers share one interpreter and Home is CPU-bound Python.
- **Starting outdated with Home (C, D) delays the dashboard** by about 1 s
  (1.3-1.6 s against 2.0-4.2 s) and does not end the page sooner.
- **Starting sources with Home (B)** shows the source indicators about 1.3 s
  sooner. It delays the dashboard by 0.3 to 0.6 s and leaves the end the same.
- The rule of the brief is not to sacrifice the dashboard for a secondary
  metric. Order A gives the fastest dashboard and the same page end.

## 5. Recommendation

Keep the current order. The page end is bound by the outdated count itself
(Step 3, section 7). Shortening it is a backend decision (covering indexes),
not a frontend one. The remaining frontend option is B, only if the owner
values the source indicators over 0.3 to 0.6 s of dashboard. That is a
product preference: NEEDS OWNER DECISION if wanted, not recommended.

The critical path of Home, warm, on the published code: shell 0.05 s,
dashboard about 1.3 to 1.6 s, everything about 3.0 to 3.2 s.

## 6. Integrity

Before and after the real run:

- the SHA-256 of the 24 database and WAL files is identical;
- the logical state is identical: schema, ledger, counters, rowids;
- WALs are at 0 bytes;
- only the three SHM mtimes moved;
- the CSV is intact;
- no project process remained and port 8765 is free.
