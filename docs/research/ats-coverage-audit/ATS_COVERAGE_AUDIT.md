# ATS coverage and discovery audit

Audit of 2026-11-04 on branch `claude/ats-coverage-audit` (base `ba53276`, which
equals `origin/main`). Scope: Ashby, Greenhouse, Lever, Workable and BambooHR.
No new ATS was added, no board was activated and no real database was written.
Companion files:

- `ATS_PAGINATION_AND_COMPLETENESS.md`: the contract of each ATS and the checklist answers.
- `ATS_BOARD_DISCOVERY_GAPS.csv`: one row per board examined, with the measured values.
- `ATS_COVERAGE_OPPORTUNITIES.md`: the ranked improvements.
- `ATS_COVERAGE_VALIDATION.md`: tests, requests, datasets and code changes.

Every statement is tagged **observed**, **inferred** or **hypothesis**. Numbers
from the seeded samples are measured on the sample only. Population figures are
labelled **estimate**.

## 1. Current architecture (observed)

| ATS | Adapter | How boards enter | Boards (active / retired / with error) | Open rows |
|---|---|---|---|---|
| Greenhouse | `providers/greenhouse.py`, `boards-api` JSON | `config/companies.yaml` (122 `domain_slug`), employer-name probe in refresh (22), embedded board (1) | 145 (138 / 7 / 7) | 12,581 |
| Ashby | `providers/ashby.py`, `posting-api` JSON | config (127), name probe (11), careers-page links (2) | 140 (136 / 4 / 4) | 7,371 |
| Lever | `providers/lever.py`, `v0/postings` JSON | config (29), name probe (11), careers-page link (1) | 41 (40 / 1 / 2) | 12,126 |
| BambooHR | `providers/bamboohr.py`, `careers/list` + detail | config only. Discovery leads wait for the owner (`PENDING_OWNER_APPROVAL`) | 2 (2 / 0 / 0) | 10 |
| Workable | `providers/workable_xml.py`, Jobs by Workable XML | the feed itself: 5,865 company boards, created as the feed names them | 5,865 (all active) | 139,349 |

The shared runner, the closing rules, retry and back-off are described in
`ATS_PAGINATION_AND_COMPLETENESS.md` §1. Discovery runs on two paths, and both
were observed in code:

- `employer_boards`, during refresh, probes guessable families only (Ashby,
  Greenhouse, Lever) by employer name. On record: 44 boards registered and 276
  leads failed with "no board listed this employer's postings" (all 276 are
  `employer_name_probe`).
- `BoardDiscovery` (CLI `discover-remotesource`) reads aggregator apply links. It
  has **one index source** (`remotesource`) and no walks on record. **It never
  sees the speedrun origin URLs**, although speedrun is the aggregator that
  names the most unregistered boards (§3).

No adapter offers incremental collection: every pass re-reads the board.
Workable alone asks `If-Modified-Since` first.

## 2. Methods

1. **Local first.** Read-only queries (`mode=ro`) on `catalogue.db` and on
   `personal.db` (`pipeline_run` only), plus a code read of the five adapters
   and the runner.
2. **Discovery from published links.** Every stored payload and URL of
   131,760 aggregator rows went through the product's own
   `registry.identify_board_url`. No new parser was written for this.
3. **Bounded public confirmation.** Sampling was fixed before any request: seed
   `20261104`, a hard request cap, 2 s between requests, 2 attempts, no cache and
   no write. 70 public requests were made in total (§ Validation).
4. Location was screened by a **documented regex on the employer-asserted
   location** (`BR_EXPLICIT` when the location names Brazil or a Brazilian city;
   `BR_POTENTIAL` for LATAM, Americas, worldwide or unqualified remote, or Brazil
   named only in the text; `INCOMPATIBLE_OR_ELSEWHERE` otherwise;
   `INSUFFICIENT` when there is nothing to read). **This is screening, not
   eligibility.** The product's eligibility reader was not run, and no profile
   was used.

## 3. Coverage by ATS

### 3.1 Registered boards: measured coverage (17 boards, 1,926 postings at origin)

Seeded sample: 6 Greenhouse, 6 Ashby and 5 Lever boards, compared about 24 h
after the last pass (2026-11-03):

| Outcome at origin | Postings | Share |
|---|---|---|
| held open in the catalogue | 1,840 | 95.5 % |
| published after the last pass (refresh lag, class A by timing, not a defect) | 61 | 3.2 % |
| closed in the catalogue and reposted with the same id (reopened on the next pass) | 8 | 0.4 % |
| dated before the last pass but never seen (class B **candidate**) | 17 | 0.9 % |

The other direction: 84 rows open in the catalogue had already left the origin
(class F, closed on the next pass).

- **FULL_JD** on 1,926 of 1,926, a valid date on 1,926, an HTTPS vendor URL on
  1,926, a location on every row (observed).
- **No posting was lost to pagination.** Every board answered with one complete
  list, and Greenhouse `meta.total` matched on 10 of 10 boards (observed).
- The 17 "older, never seen" postings (Veeva 8, Spotify 4, Flix 2, Okta,
  Glossier and Vanta 1 each) are **INSUFFICIENT_EVIDENCE**.
  - **Hypothesis**: the date field is creation time (Lever `createdAt`), and
    these postings were published later or re-listed.
  - Proving it would need history the catalogue does not keep.
- Location screening of the sampled registered boards: BR_EXPLICIT 9 (Oyster 8,
  Spotify 1), BR_POTENTIAL 65.

BambooHR (2 boards, 10 postings): complete, with `totalCount` enforced. FULL_JD
comes from the detail request, so the list alone carries none. The catalogue
holds the full text (observed 2026-11-04).

### 3.2 Workable (DATA_QUALITY_GAP, the largest finding)

The feed is read completely: 107,427 distinct postings on 2026-11-03, after
60,703 repeats were removed from 168,130 records. But **absence never closes a
Workable posting**, so the catalogue holds 139,349 open rows, **none ever
closed**.

- Inferred: at least **31,922 open rows (about 23 %)** were not in the latest
  complete feed.
- Observed: 41,220 open rows are dated more than 120 days ago.
- Measured on 4 accounts against their public widget: **10 of 67 open rows
  (15 %)** are no longer listed by the employer, and 1 listed posting is
  missing.

Details are in `ATS_PAGINATION_AND_COMPLETENESS.md` §5.

### 3.3 Unregistered boards (DISCOVERY_GAP)

Published apply links in aggregator payloads name **154 boards of supported
families that are not registered**:

| Family | Boards | Open aggregator rows pointing at them |
|---|---|---|
| Ashby | 110 | 593 |
| Greenhouse | 28 | 444 |
| Lever | 16 | 145 |
| Workable | 1 | 1 |
| BambooHR | 0 | 0 |

1,170 of the 1,228 references come from **speedrun**, whose own run reports
`beyond_reach = 12,939` postings it cannot serve. The rest come from 4 Day Week
(49), Remote OK (5), Jobicy (3) and Dynamite Jobs (1).

Seeded sample of 13 of the 154 boards (6 Ashby, 4 Greenhouse, 3 Lever), all
answering with complete lists:

| | Count |
|---|---|
| postings at origin | 1,075 |
| already in the catalogue through an aggregator row | 141 |
| **unique and missing** | **934** |
| missing with FULL_JD | 934 |
| BR_EXPLICIT among the missing | **0** |
| BR_POTENTIAL among the missing | 40 (4.3 %) |
| elsewhere or incompatible | 894 |
| aggregator rows pointing at postings the board no longer lists (class F) | 11 of 152 |

The volume is concentrated: Shield AI alone has 561 missing, and the median is
17 per board. **Estimate**, not measured: registering all 154 boards would add
between about 2,600 postings (median × boards) and about 7,800 (the sample's
missing/covered ratio × 1,183 open references). The sample suggests few of
them would be open to Brazil. Most are US companies in speedrun's portfolio.

Two further boards were examined on purpose, as recognition cases (§5.1):
Flock Safety (26 at origin, 15 missing) and Hippocratic AI (50, 43 missing),
with 0 BR_EXPLICIT.

## 4. Classes A to F within the samples

| Class | Meaning | Measured in the samples |
|---|---|---|
| A | absent because the board is not registered | 934 (13 boards) + 58 (2 encoded-name boards) |
| B | absent through pagination or collection | **0 demonstrated**. 17 candidates of unexplained timing (INSUFFICIENT_EVIDENCE) |
| C | collected but not recognised as a duplicate | indicator only: 3,424 of 8,341 open `lever:jobgether` rows share an exact title with an open Jobgether-provider row (§6). Not identity |
| D | present without FULL_JD | 0 of 1,926 (registered sample). 3 of 32,088 open first-party rows catalogue-wide (Ashby 1, Greenhouse 2, Workable 1) |
| E | present but filtered later by eligibility or matching | not measured: no profile was used, by design |
| F | closed or unavailable at origin | 84 (registered sample, next-pass closure). 10 of 67 Workable (never closed). 18 aggregator rows pointing at gone postings |

## 5. Demonstrated defects

### 5.1 Ashby board recogniser refused percent-encoded board names (FIXED here)

- Observed: 25 aggregator rows link to
  `jobs.ashbyhq.com/Flock%20Safety/<uuid>/application` and
  `.../Hippocratic%20AI/...`.
- The board recogniser accepted only `[A-Za-z0-9_.-]`, so
  `identify_board_url` returned None, and the boards could not be discovered.
- The posting recogniser did accept these URLs, so identity was not affected.
- The posting API answers to the encoded form: `Flock%20Safety` returns 26
  postings, and so does the decoded form.
- The fix accepts `%XX` escapes and keeps the identifier exactly as published.
  The test failed before the change and passes after it. Nothing was registered.

### 5.2 Lever EU-region boards cannot be read (documented, not fixed)

- Observed: `jobs.eu.lever.co/lovehoneygroup` appears in aggregator payloads.
  `api.lever.co/v0/postings/lovehoneygroup` answers **404**, while
  `api.eu.lever.co/v0/postings/lovehoneygroup` lists **39** postings.
- The Lever posting recogniser already reads `jobs.eu.lever.co` URLs as a Lever
  board. A board registered from such a URL would therefore 404 on every pass
  and be retired. **Latent**: no such board is registered today.
- Supporting the EU API is a connector change, so it is left to the owner
  (opportunity O3).

### 5.3 Workable never closes (DATA_QUALITY_GAP, §3.2)

This is a design decision with a measured cost, not a bug. Changing it is
reserved for the owner (O1).

### 5.4 Not defects (checked and cleared)

- No hidden Lever cap: 4,640 unpaged versus 4,611 paged.
- No Greenhouse truncation: 10 of 10 boards matched `meta.total`.
- No incomplete pass closed anything: the `aircall` 404 and the `jobgether`
  hold each closed nothing.
- No duplicate ids within a response.
- Retired boards close nothing, by design (2026-10-20). Only `cybereason` still
  has 2 open rows on a retired board.

## 6. Deduplication (ADR-0008 respected, nothing merged)

- First-party identity is exact: `(provider, external_id)`. Cross-source
  identity is exact too: an aggregator row folds into the first-party row only
  when its URL resolves through `identify_posting_url`, or through speedrun's
  origin pointer (observed: 335 speedrun sightings folded). Every speedrun
  reference to a **registered** board resolved to an existing first-party row
  (observed, 23 boards).
- `lever:jobgether` is a **re-poster**. Jobgether publishes other employers'
  openings on its own Lever board under new ids. The board turned over
  completely between 2026-10-26 and 2026-11-03, with 0 ids seen again. Jobgether
  is also collected through its own provider.
  - The exact-title overlap above is an **indicator** that the same openings
    enter twice.
  - Proving identity would need a published link from the re-post to the
    original, which these postings do not carry (hypothesis).
  - Nothing was changed. Whether this board belongs in the catalogue is an
    owner decision (O4).
- 13,210 groups of open first-party rows share a company and an exact title.
  These are mostly real multi-location postings. Merging them would be the
  fuzzy merging that ADR-0008 forbids, so they were not counted as duplicates.

## 7. Data quality

| Check (open first-party rows) | Greenhouse | Ashby | Lever | BambooHR | Workable |
|---|---|---|---|---|---|
| description shorter than 400 characters | 2 | 1 | 0 | 0 | 1 |
| no `posted_at` | 1 | 0 | 0 | 0 | 0 |
| no location | 0 | 0 | 0 | 0 | 0 |
| URL not HTTPS | 38 | 0 | 0 | 0 | 0 |

The location is stored as the employer wrote it, and none of the five adapters
turns "Remote" into a worldwide scope (observed in code and in the sample).
Greenhouse `wonderschool`, which is not registered, lists 10 of 41 postings
without a date.

## 8. Limitations

- Samples are small (17 + 13 + 2 boards, 4 Workable accounts), so population
  numbers are estimates.
- Geography is a regex screening of the employer's location, not the product's
  eligibility reading.
- Application URLs were checked for structure (an HTTPS vendor URL with a
  posting id), not opened one by one.
- Discovery was measured only from links the catalogue already stores.
  Companies that no aggregator links to are invisible to this method, by
  design: no crawling, no tenant enumeration.
- Workable's "not in the latest feed" is a lower bound inferred from counters.
  The staged feed is not kept on disk, so it could not be joined row by row.
