# Workable closing simulation

## 1. What data exists

| Needed for a historical replay | Available? |
|---|---|
| per-posting presence in each past feed | **no**: the update writes nothing for unchanged postings, and the staging copy is deleted after each read (audit §4.2) |
| size and churn of each past feed | yes: `pipeline_run` counters for 7 complete reads |
| presence today | yes: copies S1 and S2 of the feed, made for this study (ids only, scratchpad, deleted afterwards) |
| ground truth for an absence | yes, on a sample: Workable's own posting page |

**A replay of past reads is therefore impossible.** This study uses three
things instead:

1. today's real presence (S1, S2) against the real open set;
2. a seeded check of absent and present postings against the employer's
   posting;
3. synthetic scenarios, labelled as such, for the safety rules.

Nothing synthetic is used as an error estimate.

## 2. Real replay: the open set against S1

Input: the 139,349 open Workable rows, read-only, and S1 (107,920 unique). The
size of the last counted read is set to 107,427 (2026-11-03). The model is
`scripts/workable_lifecycle_sim.py`, with `N = 2`, `T = 24 h`, a 97 % size
guard and a 15 % churn guard.

The third read is a **projection**: the same absences seen again in a new feed
version 24 hours later. It is not an observation. Confirmation answers in the
projection are taken as "gone", which is what the sample in §3 found.

| Policy | S1 | Projection +24 h | Closed | Requests |
|---|---|---|---|---|
| A | counted | counted | 0 | 0 |
| B, default guards | **held** (churn 25.5 % > 15 %) | held | 0 | 0 |
| CS, default guards | held | held | 0 | 0 |
| C, default guards | held | held | 0 | 0 |
| B, churn guard lifted (backlog run) | counted (35,593 suspected) | counted | 35,593 | 0 |
| CS, churn guard lifted | counted | counted, sample of 50 | 35,593 | 50 postings, about 73 requests |
| C, churn guard lifted, cap 300 per read | counted | counted | 300 (35,293 still waiting) | 300 postings, about 435 requests |

Readings:

- **The churn guard stops the backlog, as designed.** A quarter of the open
  rows missing at once is exactly what a broken feed would look like. The
  backlog has to be a separate step, explicit and approved (plan §5,
  stage 2), not something the steady-state rule does by itself.
- **Full C at 300 postings per read takes about 119 reads to drain the
  backlog.** At the recorded cadence that is months. CS drains it in one read.

## 3. Real check against the employer (seeded)

Selection fixed before the first request: `random.Random(20261105)` over sorted
open rows. Strata by presence in S1 and by `last_seen_at`. Cap 160 requests,
2 s spacing, stop on 429 or 403. Protocol in the options document (C).

| Stratum (pool) | Sampled | Gone (`/oops`) | Gone (404) | Published | Unknown |
|---|---|---|---|---|---|
| absent, last written 2026-11-03 (1,203) | 25 | 11 | 14 | **0** | 0 |
| absent, last written 2026-10-19..26 (5,774) | 20 | 11 | 9 | **0** | 0 |
| absent, last written 2026-10-10..16 (28,616) | 20 | 14 | 6 | **0** | 0 |
| **present** in S1 (control, 103,756) | 20 | 0 | 0 | **20** | 0 |

- **134 requests** in all. Every one was answered, with no 429, 5xx or
  timeout.
- Gone took 1.45 requests on average: `/oops` needs one request, the account
  check two.
- The control sample includes postings dated 2023-11 and 2024-03 that are still
  published. Age is not closure.

Measured, with honest bounds:

- **False absence** (absent from the feed but still published): 0 of 65. The
  95 % upper bound is about 4.6 % (rule of three).
- **False presence** (in the feed but gone at the employer): 0 of 20. The
  upper bound is about 15 %.
- No false closure is claimed or ruled out beyond these bounds. A rate of 1 %
  would most likely not have shown.

## 4. Real hourly comparison: S1 against S2

S2: `Last-Modified: Wed, 04 Nov 2026 23:16:17 GMT`, the next hourly version,
107,955 unique. The first attempt was **cut after 485 MB**
(`RemoteProtocolError`). The fetcher removed the partial file, and the retry
three minutes later succeeded. That makes one cut in three downloads here,
and three in ten recorded runs.

| One hour, S1 to S2 | Postings |
|---|---|
| left the feed | 42 (41 of them open in the catalogue) |
| joined the feed | 77 |
| **open rows absent from S1 and present again in S2** | **9** |

Employer check (seeded, 42 requests, all answered):

| Group | Checked | Published | Gone |
|---|---|---|---|
| back in S2 after missing from S1 | 9 of 9 | **9** | 0 |
| left between S1 and S2 | 15 of 41 | 0 | **15** |

Readings:

- **Proven: a live posting can be missing from one complete feed version and
  back in the next.** All 9 were published at the employer an hour later.
  - Their last writes date from 2026-10-12 to 2026-10-26, so how long each was
    missing before S1 is unknown.
  - Under one miss they would have been closed. With today's update path they
    would have stayed closed (audit §4.3).
  - They are 9 of 35,593 absent rows, or 0.025 %. That is consistent with
    0 of 65 in §3.
- Departures between versions an hour apart were all real (15 of 15).

`N = 2` over distinct versions, with `T = 24 h`, would have reset all 9
without closing any.

## 5. Cost

| | Requests | Time at 2 s spacing |
|---|---|---|
| B | 0 | 0 |
| CS, per counted read (50 postings) | about 73 | about 2.5 min |
| C, steady state (about 1,300 departures a day) | about 1,900 a day | about 63 min a day |
| C, the backlog of 35,593 | about 51,600 | about 29 h |
| decision step itself | 0 | seconds: a set difference over about 140,000 ids |

## 6. Synthetic scenarios (labelled synthetic)

`tests/unit/test_workable_lifecycle_sim.py` has 19 cases, all passing. They
test rules, not rates:

- one absence never closes;
- a temporary disappearance recovers;
- a closure needs N misses, T hours and, under C, a "gone";
- a closed posting reopens with its history;
- truncated, timed-out and 304 reads change nothing;
- an empty feed is held;
- a sudden drop is held;
- churn with a stable size is held;
- an unknown employer answer keeps the posting suspected;
- an employer still publishing wins over the feed;
- a repost under a new id gives two postings;
- the same version counts once;
- A never closes;
- the confirmation cap defers the rest;
- under CS, the batch closes only when the whole sample answers gone.

`tests/integration/test_workable_lifecycle_preconditions.py` runs against the
real collector with a mock transport. It proves two facts about today's
code: presence is not stamped, and a returning closed posting is not reopened
(strict xfail).

## 7. Measured, as the brief asked

| Measure | Value | Source |
|---|---|---|
| rows that would be flagged suspected at S1 | 35,593 | real |
| rows that would close | 0 by the steady rule (guard); 35,593 by an approved backlog run under B or CS | real + projection |
| rows that reappeared | 9 of 35,593 within one hour, all still published | real |
| cases needing confirmation | under C, all 35,593; under CS, 50 per read | real |
| verifiable false closures | **0 of 65** sampled absent rows still published | real, sample |
| time to close | at least `T = 24 h` and a second complete read; recorded cadence 0.1 to 8 days | real cadence |
| extra requests | §5 | real rate per posting |
| inconclusive | 0 of 85 sampled | real, sample |

## 8. Public requests made by this study (188)

| What | Requests |
|---|---|
| feed S1 | 1 |
| feed S2: one 304, one cut transfer, one complete | 3 |
| contract probes: short link and account API on 4 postings | 8 |
| seeded check (§3) | 134 |
| hourly reappearance and departure check (§4) | 42 |

- All requests used the product's User-Agent, with 2 s spacing for the page
  checks, and stopped on 429 or 403. No 429, 403 or 5xx was met.
- `robots.txt` on `apply.workable.com` disallows nothing (audit of
  2026-11-04).
- The feed copies and check results lived only in the session scratchpad, and
  were deleted at the end.
