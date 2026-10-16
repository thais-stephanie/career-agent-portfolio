# Release hardening, 2026-10-16

What this round changed, what it measured and what it left for the owner.
No model call, no Deep Reading, no preference or evidence was changed.

## A. Salary correctness (merged before this branch, 801896d)

- The pay reader keeps a stated period, infers one only from the amount's
  size when none is stated (`SALARY_HEURISTIC`), and prefers a monthly reading
  for LATAM currencies (`LATAM_DEFAULT`). The card says when a period was
  inferred.
- The semantic reader identity now hashes the lineage of every component the
  score depends on (`readers-14+semantic/<digest>`), so a reader bump can no
  longer leave stale scores serving.
- Production recalculation: 187,332 postings, 0 errors, identical to the
  rehearsal, 0 eligibility changes on clean rows.

## B. Sources

| Item | Result |
|---|---|
| Gupy | Feed moved to `portal.gupy.io/api/job-search/jobs`; contract unchanged (85,236 open, 1,922 remote). Bounded live collect into a temporary database: 868 new, second run 0 new. A body without `data` is a contract error, never zero jobs. |
| Greenhouse | Healthy: 126 of 132 boards collected, 12,532 postings since the catalogue reset. Six boards answer NOT_FOUND every run (companies that removed the board); they are no longer counted as failures. |
| Status vocabulary | One vocabulary on every source row; Jooble is QUOTA_EXHAUSTED; RemoteSource is discovery only. |
| Careers page to ATS | Links to eight board families are recognised from an employer's page (no generic scraper). |
| Pinpoint | New provider; four tenants validated through Common Crawl candidates, robots.txt read first, demo board refused. |
| Recruitee | Already live. |
| BambooHR | POC: 4 postings, no description and no date; not built. |
| Breezy | Common Crawl index unavailable (502/504) during the round; not evaluated. |
| Arc.dev, Crossover, OpenTalent, Virtustant | Preflight only; stay PLANNED (server-rendered or script-rendered pages, no API). |
| JazzHR | Backlog. |
| HN Who is Hiring | Not built (optional). |
| Metadata vs advert | An aggregator's "Anywhere" beside an advert line `Remote - North America` is UNRESOLVED (`DESCRIPTION_LOCATION_ELSEWHERE`); 1 of 6,954 eligible postings moves. Reader identity `readers-15`. |
| Duplicates | An aggregator's own row folds into the employer's posting it names (sighting kept): 130 rows on a production copy (Speedrun 125, Jobicy 5). |

## C. Ranking audit (no tuning)

- Eligibility is a gate; it moves the score only through the work-model
  component (at most 4 points).
- The responsibilities component saturates at 25 of 25 with one PRIMARY
  signal, because `CENTRAL_PIECES` is 4. A policy, not a bug; recorded.
- The `automation_integration` component scores 0 on every posting under the
  owner's configuration.
- No stale score was found.
- Concentration: of 181 GOOD/STRONG postings, Pavago 44, Jobgether 28 and
  HireLatam 8 (44%); 85 carry agency wording. No recruiter penalty was added
  (out of scope); the duplicate fold above removes the aggregator copies.

## Left for the owner

- Five unpushed commits carry a personal e-mail as author. Pushing needs a
  history rewrite the owner approves.
- Disk: superseded backups, the m1d2 pre-retention copy, `data/models` and the
  recovery folder hold personal data and were not deleted.
