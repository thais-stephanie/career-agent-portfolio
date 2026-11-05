# Workable closing policy options

Three policies, compared on the evidence in `WORKABLE_LIFECYCLE_AUDIT.md` and
`WORKABLE_CLOSURE_SIMULATION.md`. A fourth one, close on age, is rejected at
the end. The rules of B and C are written out as a pure model in
`scripts/workable_lifecycle_sim.py`. Its synthetic scenarios are in
`tests/unit/test_workable_lifecycle_sim.py`.

Definitions shared by B and C:

- **Complete read:** the feed was read to its end (`finish()` reached), it was
  not malformed or cut, and it was not a 304. Each feed version, identified by
  its `Last-Modified`, counts once.
- **Miss:** a posting open in the catalogue is absent from a complete read.
- **Guard:** the read counts no miss at all when either is true:
  - the feed holds fewer than 97 % of the unique postings of the last counted
    read;
  - more than 15 % of the open postings would gain their first miss.

  Both thresholds come from the measured range (section 3).

## A. Keep the current behaviour

Absence never closes a Workable posting.

| Criterion | Assessment |
|---|---|
| False closures | none, by construction |
| Delay to detect a real closure | never detected. About 35,600 open rows (25 %) are already missing from the feed, and about 1,300 more leave every day |
| Operating cost | none |
| Extra requests | none |
| External dependency | none |
| Maintenance | none |
| On failures | safe |
| Reposted under a new id | the old id stays open forever beside the new one |
| Several profiles | every profile sees the same stale rows, in lists, counts and Deep Reading candidates |
| Reversible | n/a |

## B. N misses in complete feeds, with guards

Close after `N` misses in distinct complete reads that span at least `T` hours
since the first miss. No confirmation.

| Criterion | Assessment |
|---|---|
| False closures | a posting can leave one version and come back. 9 rows did between S1 and S2, all published (simulation §4). With `N = 2` over distinct versions and `T = 24 h`, those reset instead of closing. A posting that stays out of the feed while still published would be a false closure B cannot see. 0 of 65 were, on the sample (§3) |
| Delay | `T` at least, in practice N reads. The recorded cadence is one read every 0.1 to 8 days |
| Operating cost | one set difference per read (about 140,000 ids, in memory, seconds) |
| Extra requests | none |
| External dependency | the feed only |
| Maintenance | low: two counters and the guard |
| On failures | an incomplete read counts nothing; the guard holds a shrunken or churned feed |
| Reposted under a new id | the old id closes after N; the new id is a new row (ADR-0008, no merge) |
| Several profiles | the catalogue is shared, so one decision applies to all profiles. No profile data changes |
| Reversible | yes: a closed posting reopens when it is seen again. The current update path does **not** do this today (audit §4.3), so it must be fixed first |

## C. N misses, then confirmation by the employer's posting

As B, but a due posting is checked against Workable's public posting before it
closes:

1. `GET https://apply.workable.com/j/<code>` without following redirects:
   - `302 /oops` means **gone**;
   - `301 /<account>/j/<code>` gives the account;
2. `GET https://apply.workable.com/api/v2/accounts/<account>/jobs/<code>`:
   - `200` with `state = published` means **still published**;
   - `404` or another state means **gone**;
   - a timeout, 429 or 5xx means **unknown**.

Gone closes the posting. Still published keeps it open and restarts its count.
Unknown keeps it suspected for the next read. A cap per read bounds the
requests.

| Criterion | Assessment |
|---|---|
| False closures | only where Workable's own posting page says gone while the employer still hires. None was observed in the sample |
| Delay | as B, plus the confirmation queue: at most `cap` postings per read |
| Operating cost | 1 or 2 requests per due posting, spaced 2 s apart |
| Extra requests | steady state about 1,300 postings a day, about 2,500 requests (about 85 min at 2 s spacing). The one-off backlog is about 35,600 postings (simulation §5) |
| External dependency | two undocumented public endpoints on `apply.workable.com`. `robots.txt` allows them, but they can change without notice |
| Maintenance | medium: an endpoint contract, a per-read budget, a queue |
| On failures | unknown never closes. A 429 or 403 stops the queue for that read |
| Reposted under a new id | the old posting answers gone and closes; the new id is a new row |
| Several profiles | as B |
| Reversible | as B, and each decision records the employer's answer |

## D. Close on age or on `last_seen_at`: rejected

- `last_seen_at` does not mean "last in the feed" for Workable. The update
  skips unchanged postings and never touches them (audit §4.2). 50,857 rows
  last stamped on 2026-10-10 are still in today's feed.
- `posted_at` does not mean closed: 36,514 rows older than 120 days are still
  in today's feed.

Either rule would close tens of thousands of live postings.

## Choice

The choice, with the reasons and the data behind it, is in
`WORKABLE_LIFECYCLE_IMPLEMENTATION_PLAN.md`.
