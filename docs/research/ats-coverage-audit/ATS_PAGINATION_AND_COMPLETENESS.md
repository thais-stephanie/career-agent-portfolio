# ATS pagination and completeness

Audit of 2026-11-04, branch `claude/ats-coverage-audit` (base `ba53276`).
Scope: Ashby, Greenhouse, Lever, Workable, BambooHR. Every claim below is
marked **observed** (read in code, data or a response), **inferred** (follows
from observations) or **hypothesis** (not tested).

## 1. The shared runner (all four board families)

`pipeline/collect.py` reads one board at a time through `JobProvider.list_postings`.
These rules hold for Ashby, Greenhouse, Lever and BambooHR alike (observed in
code and pinned by `tests/integration/test_disappearance_guard.py`,
`test_collect.py` and `test_bamboohr_collect.py`):

| Situation | Outcome on the board | Closes postings? |
|---|---|---|
| list request raises (`FetchError`: 404, 5xx, timeout, malformed body) | board FAILED, counted in `stats.failures` | **no** |
| `validate_listing` refuses the body (BambooHR: length ≠ `meta.totalCount`) | board FAILED | **no** |
| a detail fetch fails (BambooHR only) | posting stays in the seen set | **no** for that posting |
| deadline or request budget expires inside a board | nothing written for the board | **no** |
| a successful list that misses ≥ 50 % of ≥ 20 previously open postings | board HELD (`suspicious_since`) | **no**, until a second pass confirms |
| a successful list | absent postings closed (`close_absent`) | yes |
| 404 several times in a row over a minimum span | board retired (`active = 0`) | **no**: retirement closes nothing |

The run reports **COMPLETE / PARTIAL / FAILED** per source in `source_health`
(observed: `collect:lever` = `PARTIAL / SOME_FAILED` on 2026-11-03, because
`aircall` answered 404). Per-board counters are in `pipeline_run.stats_json.by_board`.

Retry and back-off (observed, `net/fetcher.py`): 3 attempts, back-off starting at
1 s, 1 s between requests, 10 s connect and 30 s read timeouts, and 404 never
retried. A whole refresh also carries a deadline and a request cap. If either
ends work mid-board, that board writes nothing and resumes on the next pass
(observed in code). One board cannot starve another of its own pagination,
because none of the four families paginates. A large board costs one request,
not many.

## 2. Contract per ATS

| ATS | Endpoint | Pagination | Total in the response | Completeness check today |
|---|---|---|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true` | **none**: one request returns the whole board | `meta.total` | **not checked** (the adapter ignores `meta`) |
| Lever | `api.lever.co/v0/postings/<site>?mode=json` | none used. `skip`/`limit` exist but are optional | none | none beyond the disappearance hold |
| Ashby | `api.ashbyhq.com/posting-api/job-board/<board>` | none: `limit`/`offset`/`page`/`cursor` ignored (measured earlier on a 751-posting board) | none | none beyond the disappearance hold |
| BambooHR | `<tenant>.bamboohr.com/careers/list` + `/careers/<id>/detail` | none | `meta.totalCount` | **checked**: a mismatch refuses the board (since `ba53276`) |
| Workable | Jobs by Workable XML `www.workable.com/boards/workable.xml` (~820 MiB, regenerated hourly), read whole, staged on disk, then served in pages of 200 to the writer with a resumable cursor | **internal** pages over a full download | `xml_jobs − feed_duplicates` | the download is all or nothing per record: a truncated transfer keeps the complete records and is reported as not an update. **No absence closes a posting.** |

### Measured on 2026-11-04 (public requests, see `ATS_COVERAGE_VALIDATION.md`)

- **Greenhouse `meta.total` = rows on 10 of 10 boards** checked, from 4 to
  2,572 postings (Anduril), with no repeated id. No truncation was observed. A
  `meta.total` check like BambooHR's would cost nothing, but there is no
  demonstrated defect for it to fix. Recommended as an observability guard,
  not a fix (opportunity O6).
- **Lever without paging versus paging**, on the largest Lever board
  (`jobgether`): see §4. The answer decides whether Lever has a hidden cap.
- **Ashby**: no total in the contract, so completeness cannot be proven per
  response. Only the disappearance hold protects against a short answer
  (observed limitation).
- **BambooHR**: `totalCount` matched the rows on both tenants (observed
  2026-11-03 and 2026-11-04).

## 3. Answers to the checklist

| Question | Greenhouse | Lever | Ashby | BambooHR | Workable |
|---|---|---|---|---|---|
| All results retrieved? | yes, 10/10 boards matched `meta.total` | see §4 | cannot be proven: no total | yes, count enforced | yes for the feed itself (`claimed_total = postings_seen`, 107,427 on 2026-11-03) |
| Page limits respected? | n/a | n/a | n/a | n/a | internal page 200, writer-side |
| Premature stop? | none observed | none observed | none observed | refused on mismatch | `stopped_early=false` on the last two full reads |
| Repeated cursors / loops? | n/a | n/a | n/a | n/a | n/a for the XML. The legacy JSON API (offline tests only) has a repeated-token guard (`repeated_itself`) |
| Duplicate pages → duplicate rows? | n/a | n/a | n/a | n/a | the feed repeats itself (60,703 duplicate records of 168,130). They are de-duplicated before writing (`feed_duplicates`) |
| Hidden result cap? | none at 2,572 | §4 | none known (751 measured earlier) | none known | none: the whole file |
| Mid-run failure → PARTIAL? | yes (`source_health`) | yes (observed PARTIAL 2026-11-03) | yes | yes | yes (`continues=true`, pending update) |
| COMPLETE / PARTIAL / FAILED distinguished? | yes | yes | yes | yes | yes |
| Incomplete pass closes postings? | no | no | no | no | **never closes at all** (§5) |
| Safe resume? | yes: the next pass re-reads the board | yes | yes | yes | yes: the cursor in `workable_feed.py` |
| Large boards starve others? | no | no | no | no | n/a (one feed) |
| Incremental collection? | none in the contract: every pass re-reads the board | none | none | none | `If-Modified-Since` → 304 skips an unchanged feed, and unchanged records are not rewritten |

## 4. Lever: hidden cap and board turnover on `jobgether`

Observed in `pipeline_run`:

| Pass | Postings observed | New | Seen again |
|---|---|---|---|
| 2026-10-26 | 4,620 | 1,376 | 3,244 |
| 2026-11-03 | 3,721 | **3,721** | **0** |

The 2026-11-03 pass shared **no id** with the 2026-10-26 pass. The
disappearance guard held the board (`would_have_closed = 4,620`) and closed
nothing, as designed (observed). The catalogue therefore holds 8,341 open rows
for a board that listed 3,721.

Measured on 2026-11-04, with requests minutes apart:

| Read | Rows | Unique ids |
|---|---|---|
| one request, no paging (what the adapter does) | 4,640 | 4,640 |
| `skip`/`limit=1000` paging to the end (5 pages) | 4,611 | 4,611 |

The unpaged answer was the **larger** one, so there is **no hidden cap** on the
unpaged list at 4,640 postings (observed). The 29-row difference is the board
changing between reads (inferred: the board turned over completely in eight
days). Paging would add four requests and a torn-read risk, and it would gain
nothing. **No PAGINATION_DEFECT.**

The same read against the catalogue: 8,341 open rows, 1,786 ids not yet held
(new since the last pass) and 5,487 open rows no longer listed. These are the
held 4,620 plus postings already gone from the 2026-11-03 list. The board belongs
to Jobgether, which is itself an aggregator. It republishes other employers'
openings under fresh Lever ids, and Jobgether is also collected through its own
provider. That makes this board a DUPLICATION_GAP candidate, not a coverage
problem (see `ATS_COVERAGE_AUDIT.md` §6).

## 5. Workable: absence never closes, so open rows accumulate

Observed:

- `pipeline/workable_collect.py` and `workable_update.py`: "No absence closes a
  posting". This is deliberate, because the feed is Jobs by Workable inventory,
  not every account's board.
- 139,349 Workable rows are open, and **0 have ever been closed**. The last two
  complete feed reads listed 107,902 (2026-10-26) and 107,427 (2026-11-03)
  distinct postings.
- 41,220 open Workable rows have a `posted_at` older than 120 days.

Inferred: at least **31,922 open Workable rows (≈ 23 %) were not in the latest
complete feed**. This assumes each feed posting maps to at most one row, which
the identity rules guarantee.

Measured on a seeded sample of 5 accounts with ≥ 10 open rows, against each
account's public widget (`apply.workable.com/api/v1/widget/accounts/<slug>`;
`robots.txt` allows it):

| Account | Open in catalogue | On the widget | On both | Open but no longer listed | Listed but missing |
|---|---|---|---|---|---|
| growth-resourcing | 23 | 23 | 22 | 1 | 1 |
| mm-coaching-limited | 13 | 9 | 9 | 4 | 0 |
| the-rusty-taco | 19 | 19 | 19 | 0 | 0 |
| alder-group | 12 | 7 | 7 | 5 | 0 |
| paragon-cyber-solutions | 15 | widget 404 | n/a | n/a | n/a |

On the four accounts that answered, **10 of 67 open rows (15 %) are no longer
listed by the employer**, and **1 listed posting is missing** from the catalogue.
The paragon 404 may be an identifier mismatch (the XML company slug is not
guaranteed to be the account slug), so it counts as INSUFFICIENT_EVIDENCE, not
as stale.

Correction to an earlier note: `RETRIEVAL_SOURCE_FEASIBILITY_MATRIX.md` (Paired)
says the 52 stale rows close "on the next complete feed". The code does not
close them, so they stay open.

This is a **DATA_QUALITY_GAP**, not a pagination defect. Fixing it changes
closing semantics for one source, which the brief reserves for the owner
(opportunity O1).

## 6. Changes made in this audit

None to collection logic. No defect was demonstrated in pagination or in the
closing rules: every incomplete pass observed closed nothing. The one
demonstrated, deterministic and small defect sits in **URL recognition** (Ashby
board names with an encoded space), and it is handled in
`ATS_COVERAGE_VALIDATION.md` §4.
