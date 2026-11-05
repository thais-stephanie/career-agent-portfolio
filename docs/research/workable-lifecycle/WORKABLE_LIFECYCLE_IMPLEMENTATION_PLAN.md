# Workable lifecycle: implementation plan

Nothing here is implemented. Each stage needs the owner's approval before it
starts, because it changes closing semantics for one source.

## 1. Recommended policy

**CS (C-sampled)**: policy B decides, and a seeded sample of policy C's employer
check acts as a running audit and a circuit breaker. The B/C rules are
specified in `scripts/workable_lifecycle_sim.py`; the parameters are below.

| Parameter | Value | Why (data) |
|---|---|---|
| Complete read | `finish()` reached, not malformed, not cut, not 304 | the existing definition of an update (audit §1) |
| Counted once per | feed `Last-Modified` | the feed is regenerated hourly; the same version read twice is one observation |
| `N` misses | **2** | **One miss is not enough, as measured.** 9 rows missing from version S1 were back and published an hour later in S2 (simulation §4). A second miss in a distinct version also protects against **one bad feed version**. A well-formed feed generated up to 3 % short passes the size guard (the largest real change is 0.46 %), and only a second, different version can expose it |
| `T` min hours from first miss | **24** | Two versions an hour apart may share a generation fault. The recorded cadence is one complete read every 0.1 to 8 days, so `T = 24 h` usually adds no delay beyond the second read |
| Size guard | hold if unique postings < **97 %** of the last counted read | real consecutive changes ≤ 0.46 %: more than six times that margin |
| Churn guard | hold if first misses > **15 %** of open rows | the worst real gap (8 days) produced about 10 % departures; 15 % lets an 8-day gap through and stops anything larger |
| Audit sample | **50** due postings per counted read, seeded, before closing | at 1.45 requests per posting that is about 73 requests (2.5 min at 2 s spacing) |
| Circuit breaker | if any sampled posting answers **published**, close nothing in that read and report it | 0 of 65 answered published. One published answer is already outside what was measured |
| Unknown answers | 5xx, timeout: retry next read; 429 or 403 stops the sample, and that read closes nothing | an unknown is never evidence |

Why not full C (confirm every closure)?

- The backlog is 35,593 rows at 1.45 requests each: about 51,600 requests, or
  about 29 hours at 2 s spacing.
- The steady state is about 1,900 requests a day (about 63 min).
- Two undocumented endpoints would become a dependency of every closure.
- The sample shows no case where confirmation changes the decision.

The audit sample keeps measuring that, at about 4 % of the cost. If the
breaker ever trips, the owner can switch to full C for that period with the
same code (`confirm_cap` raised).

## 2. Evidence and limits

| Evidence | Strength |
|---|---|
| 65/65 absent gone, 20/20 present published (seeded, 134 requests) | upper bound of a "still published" rate among absent rows: about 4.6 % at 95 % confidence (rule of three, 3/65). Strata: 25 seen on 2026-11-03, 20 on 2026-10-19..26, 20 on 2026-10-10..16 |
| feed size over 7 complete reads | 22 days, one season, no incident observed |
| hourly versions S1/S2 | 9 rows back after one miss, all published; 15 of 15 departures gone (42 requests) |
| per-posting presence history | **none exists**: the update does not record presence |

Limits:

- Multi-day flapping (absent for one read, back the next) cannot be measured
  from existing data. Stage 1 below measures it before anything closes.
- The two `apply.workable.com` endpoints are undocumented. They are used only
  by the audit sample, never as a closing requirement.
- The sample is small for rare events: a 1 % false-absence rate would most
  likely not show in 65.

## 3. Changes needed

### 3.1 Code (one source only)

1. **Reopen fix (precondition):** `_own_rows()` excludes closed rows, or
   `_unchanged()` returns False for a closed row, so a returning posting goes
   through `upsert_seen` and reopens. The strict xfail in
   `tests/integration/test_workable_lifecycle_preconditions.py` turns into a
   normal test.
2. **Presence record:** after a complete read, the set of referenced ids is
   known (the staging table). Record per row the feed version in which it was
   last present, the miss count and the first-miss time, in one set-based
   statement per page, without touching `last_seen_at` semantics for other
   sources.
3. **Decision step:** runs only after `finish()` on a non-304 read. It applies
   the guards, counts misses, takes the audit sample and closes through the
   existing `UPDATE` shape (`closed_at`, `collection_status = CLOSED`). It never
   runs for any other provider.
4. **Run stats:** misses counted, closures, holds with their reason, sample
   answers and requests, all recorded in `pipeline_run.stats_json`. This is
   the audit trail.

### 3.2 Schema (needs approval)

One additive migration. Two shapes:

- **(a) recommended:** a table `workable_presence(job_id PRIMARY KEY,
  last_present_version, misses, first_miss_at, last_decision, decided_at)` in
  the catalogue. It is additive and drops cleanly, and it leaves `job` and its
  triggers alone.
- **(b)** columns on `job`. Rejected: every update of `job` bumps
  `population_revision` (trigger `trg_population_job_update`), so a
  per-read miss counter on 140,000 rows would invalidate caches on every read.

`SUSPECTED_STALE` stays internal (misses = 1 in the table). No new
`collection_status` value, so nothing in the interface or scoring has to learn
a new state.

### 3.3 Compatibility with the existing closing paths

- The board-based `DisappearancePolicy` and `close_absent` are untouched.
  Workable boards are per-employer shells with no list of their own, so the
  feed decision is per posting, not per board.
- Reopening uses `upsert_seen` as every other source does.
- Closing marks the job dirty through the existing trigger. `rescore` counts it
  as closed without scoring it (audit §4.4).
- Profile data (`job_match`, Deep Reading, applications, review decisions) is
  never written. An application on a closed posting keeps its status; the
  interface already reads `closed_at` to show it.

## 4. Tests required

- The scenarios already modelled in `tests/unit/test_workable_lifecycle_sim.py`
  (16), repeated against the real collector with the mock transport of
  `tests/integration/test_workable_update.py`:
  - temporary disappearance;
  - confirmed closure;
  - reopening;
  - truncated feed;
  - timeout, 429 and 5xx;
  - empty feed;
  - sudden drop;
  - churn with a stable size;
  - unknown employer answer;
  - employer still publishing;
  - repost under a new id;
  - the same version read twice;
  - the confirmation cap.
- The reopen precondition test (today a strict xfail).
- No other provider's row changes in any Workable test.
- Profile tables unchanged by a closure (row counts and contents before and
  after).
- The whole unit, integration and browser suites before any merge.

## 5. Rollout

| Stage | What runs | Closes | Exit criterion |
|---|---|---|---|
| 0 | reopen fix + presence table, behind a setting that is off | nothing | tests pass, rehearsal on a catalogue copy |
| 1 **shadow** | presence and decisions recorded for at least **3 complete reads over at least 7 days**, plus the audit sample on each | nothing | 0 published answers among sampled would-close postings; the measured flap rate (one miss, then present again) is reported; no guard tripped without an explained cause |
| 2 **backlog** | on a catalogue copy first, then the real catalogue: close the rows the shadow marked due, after one fresh backup | the backlog (about 35,000, an estimate) | counts match the copy; integrity check clean; profile counts unchanged |
| 3 **steady** | the policy on every complete read | about 1,300 a day | weekly report: closures, holds, sample answers, reopenings |

## 6. Rollback

- Every closure is recorded with its read version in `pipeline_run` and
  `workable_presence`. To roll back, reopen exactly those rows (`closed_at =
  NULL`, the earlier `collection_status`). No history was deleted, so nothing
  needs restoring.
- Turning the setting off returns to policy A at once.
- Stage 2 starts only after one verified full backup, under the housekeeping
  rules.

## 7. Approval criteria

The owner approves each stage. Stage 2 also needs all of the following:

1. Stage 1 shows **0 published answers** among sampled due postings, from at
   least 150 sampled across the shadow reads (upper bound about 2 %).
2. The measured flap rate is low enough for `N = 2`: under 1 % of one-miss
   rows return. Otherwise raise `N` to the smallest value at which the shadow
   shows no return.
3. No hold that was not explained.
4. The reopen test passes; no other provider is affected.

## 8. Closing questions

| # | Question | Answer |
|---|---|---|
| 1 | Can closed Workable postings be identified safely enough? | **Yes**, with two complete versions at least 24 h apart and a seeded employer check. A single absence is **not** safe: 9 live postings came back an hour later |
| 2 | How many could be handled with reliable evidence? | Today 35,593 rows (25.5 % of open Workable rows) are missing from the feed. The sample put 65 of 65 at gone (upper bound for live ones about 4.6 %). The 9 rows that came back show that this set changes from hour to hour |
| 3 | Which policy minimises false closures? | **Full C** (every closure confirmed by the employer) in principle. **CS** (B plus a seeded audit sample and a breaker) at about 4 % of C's cost, with no false closure observed that C would have prevented |
| 4 | What does it cost to run? | CS: about 73 requests (2.5 min) per counted read, plus a set difference. C: about 1,900 requests a day and a backlog of about 51,600 |
| 5 | What data is still missing for a safe rollout? | Per-posting presence across several complete reads: how often a posting misses one read and returns, over days rather than hours. The reopen fix must also be in place first |
| 6 | Smallest further experiment? | Stage 1 shadow: record presence and the decisions B would take over at least 3 complete reads and 7 days, closing nothing, with the 50-posting audit sample on each read |
| 7 | Ready to build, or still observing? | Ready to build stages 0 and 1, which close nothing. Closing (stages 2 and 3) waits for the stage 1 criteria in §7 |

## 9. Recommendation

**READY FOR CONTROLLED IMPLEMENTATION**, limited to stages 0 and 1 (the
reopen fix, the presence record, the shadow decisions). Both close nothing.

Why not KEEP CURRENT BEHAVIOR: a quarter of the open Workable rows are
measured as gone at the source.

- 65 of 65 sampled absent rows answered gone, and 15 of 15 hourly departures
  did.
- 417 Deep Reading reviews and 3 tracked applications already sit on such
  rows.
- The catalogue's open count overstates Workable by about 35,600.

Why not NEEDS MORE EVIDENCE as a blocker:

- The evidence still missing is multi-day flapping, which no existing data can
  provide. Only recording presence can, and that is stage 1, which closes
  nothing.
- The rules are specified and tested (21 synthetic and characterisation cases).
- The guards come from 7 real complete reads.
- The one demonstrated hazard, a live posting missing from one version, is
  handled by `N = 2` over distinct versions and by the reopen fix.

Closing any posting (stage 2 backlog, stage 3 steady state) stays a separate
decision for the owner, after the stage 1 report.
