# Workable lifecycle audit

Date: 2026-11-04. Branch `claude/workable-lifecycle`, cut from
`claude/ats-coverage-audit` (`7fe9db6`). Evidence base: that audit
(`docs/research/ats-coverage-audit/`), the code, the real databases opened
read-only, two fresh copies of the public feed kept in the session scratchpad
(ids only), and a seeded check of single postings against Workable's public
pages. Nothing was written to any real database.

Labels used below: **Proven** (measured or read in code), **Inferred** (follows
from proven facts), **Hypothesis** (plausible, not tested).

## 1. How the source works today

| Piece | What it does |
|---|---|
| `providers/workable_xml.py` | Downloads `https://www.workable.com/boards/workable.xml` (about 820 MiB, one file, no paging, no filter). Parses it on disk into `stage.sqlite`, one row per `referencenumber`, keeping the changed fields of repeated references as variants. Yields pages of 200 records. |
| `providers/workable_feed.py` | `FeedSession` in `data/shared/workable/`: `state.json` holds `processed_last_modified` (the `Last-Modified` of the last feed read to its end), `checked_at`, and a `pending` entry with a cursor for a cut read. `finish()` deletes the download and the staging. |
| `pipeline/workable_update.py` | One shared, stoppable update behind a lock: `If-Modified-Since` first, then `WorkableCollector(skip_unchanged=True)`. |
| `pipeline/workable_collect.py` | Per posting: identity rules (external id held by another provider, canonical URL, content hash for the same employer and title), otherwise one company and one board per employer and `JobRepo.upsert_seen`. "No absence closes a posting." |

Run outcomes, as recorded in `pipeline_run` (stage `collect-workable`):

- `OK` with `deferred_reason = None` and `feed_not_modified = False`: the feed
  was read to its end. This is the only **complete read**.
- `OK` with `feed_not_modified = True` (304) or a receipt (fresh, busy, disk):
  nothing read.
- `OK` with `deferred_reason` set (time budget, Stop): a pending read, carried
  on next time from the cursor.
- `FAILED`: a cut transfer or malformed XML. Complete records are kept, the
  session is abandoned, nothing counts as done.

Identity: `job.provider = 'workable'`, `external_id = 'workable-' +
referencenumber`, `url = https://apply.workable.com/j/<referencenumber>`. The
reference number is the posting's Workable shortcode (**Proven**: the short
link resolves to `/<account>/j/<same code>`). The board is one per employer
**name** (`_slugify(company)`), not the Workable account slug.

## 2. Complete reads on record

Ten runs; seven complete reads. Unique postings per complete feed (after the
feed's own repeats):

| Feed `Last-Modified` | Records | Unique | New rows | Changed | Unchanged |
|---|---|---|---|---|---|
| 2026-10-12 07:16 | 166,465 | 106,925 | 35,416 | 3,286 | 68,223 |
| 2026-10-16 15:17 | 167,658 | 107,597 | 8,711 | 7,552 | 91,334 |
| 2026-10-19 13:17 | 167,985 | 107,712 | 2,062 | 5,132 | 100,518 |
| 2026-10-19 16:16 | 168,140 | 107,757 | 479 | 4,429 | 102,849 |
| 2026-10-23 03:15 | 169,395 | 107,969 | 6,945 | 7,018 | 94,006 |
| 2026-10-26 15:17 | 169,206 | 107,902 | 3,724 | 5,558 | 98,620 |
| 2026-11-03 00:15 | 168,130 | 107,427 | 10,235 | 8,478 | 88,714 |

The three failed runs (2026-10-10, 2026-10-16, 2026-10-18) were cut transfers.
The first one stored 71,777 postings from a partial file. All three closed
nothing, as the code promises.

**Proven: the feed is very stable in size.** Over 22 days the unique count
moved between 106,925 and 107,969. The largest change between two
consecutive complete reads was 0.46 %. **Inferred: it churns steadily.** About
1,300 new postings appear per day, so with a stable size about as many leave.
The 2026-11-03 read came 8 days after the previous one and brought 10,235 new
rows, with the size down by 475. So about 10,700 postings left in 8 days
(10 % of the feed).

## 3. Today's feed against the catalogue

Copy S1 of the feed: `Last-Modified: Wed, 04 Nov 2026 22:17:23 GMT`. 864,562,570
bytes, downloaded in 55 s with the product's fetcher (1 request). 169,333
records, **107,920 unique**. Only the ids, URL, company, date and country were
kept, in the scratchpad.

| | Rows |
|---|---|
| Workable rows open in the catalogue | 139,349 |
| present in S1 | 103,756 |
| **absent from S1** | **35,593 (25.5 %)** |
| in S1 but not in the catalogue (new since 2026-11-03) | 4,164 |

The audit's inferred floor of 31,922 is confirmed and exceeded. The extra rows
are postings that left after the 2026-11-03 read.

Absent rows by the last day the update wrote them (`last_seen_at`):

| `last_seen_at` | Present | Absent |
|---|---|---|
| 2026-10-10 | 50,857 | 12,600 |
| 2026-10-12 | 17,009 | 12,098 |
| 2026-10-16 | 6,056 | 3,918 |
| 2026-10-19 | 2,002 | 1,484 |
| 2026-10-23 | 6,434 | 2,592 |
| 2026-10-26 | 3,888 | 1,698 |
| 2026-11-03 | 17,510 | 1,203 |

Absent rows by the age of `posted_at`:

| Age | Present | Absent |
|---|---|---|
| < 30 days | 32,057 | 12,587 |
| 30 to 59 | 17,188 | 10,704 |
| 60 to 119 | 17,997 | 7,272 |
| ≥ 120 | 36,514 | 5,030 |

Other measurements:

- The absent rows belong to 3,803 of 5,862 employers. On 518 employers, every
  open row is absent (2,370 rows).
- Profile data tied to absent rows (personal database):
  - 35,593 `job_match` rows;
  - 417 of 2,127 Deep Reading reviews;
  - 3 of 6 `job_application` rows (all `DISCOVERED`).

## 4. What the code guarantees, and what it does not

### 4.1 Proven guarantees

- A cut transfer, malformed XML, a Stop or a time budget never counts as an
  update (`finish()` is not reached) and closes nothing.
- A 304 means "the feed is the one already read to its end".
- Identity is by external id, then by exact URL, then by exact content hash
  for the same employer and title. Nothing fuzzy (ADR-0008).

### 4.2 `last_seen_at` is not presence (Proven)

`WorkableCollector(skip_unchanged=True)` counts a posting the feed repeats
unchanged as `jobs_unchanged` and writes nothing. `last_seen_at` therefore
means "first stored or last changed", not "last listed". On the real catalogue,
`updated_at = last_seen_at` on all 139,349 rows, and 50,857 rows stamped on
2026-10-10 are still in today's feed. Test:
`tests/integration/test_workable_lifecycle_preconditions.py::test_an_unchanged_posting_is_not_stamped_as_seen`.

The staging copy is deleted by `finish()`, so **no per-posting presence
history exists**: only one copy per complete read, and only in counts.

### 4.3 A returning closed posting would stay closed (Proven, latent)

`_own_rows()` reads every Workable row, closed or not. A posting that comes
back unchanged is skipped as "unchanged", so `upsert_seen`, the only path that
clears `closed_at`, never runs. Harmless today because nothing closes. Under
any closing policy it would make a closure permanent. Test (strict xfail):
`test_a_closed_posting_that_comes_back_unchanged_is_reopened`.

### 4.4 A closure is cheap downstream (Proven)

- Closing is an `UPDATE` of `closed_at` and `collection_status`.
- The trigger `trg_job_dirty_update` marks the job dirty, and `rescore` counts
  dirty closed rows as closed without scoring them.
- `job_match`, Deep Reading, applications and review decisions live in the
  profile databases and are not touched.
- Lists and counts filter `closed_at IS NULL`.

## 5. Answers

| # | Question | Answer | Kind |
|---|---|---|---|
| 1 | Is the feed the whole public catalogue of Workable jobs? | No. It is the Jobs by Workable inventory (the code says so in `coverage_note`). A sampled employer listed a posting the catalogue lacked (audit §5). Whether every public job is in it is not known | Proven no / Hypothesis on extent |
| 2 | What really guarantees that a missing posting closed? | Nothing in the feed itself. Workable's own posting page is the guarantee: the short link answers `302 /oops`, or the account API answers 404. 65 of 65 sampled absent postings answered gone; 20 of 20 present ones answered published | Proven on the sample |
| 3 | Can a live posting drop out of the feed for a while? | **Yes.** 9 open rows missing from version S1 were back in the next hourly version and published at the employer. That is rare: 9 of 35,593 absent rows, and 0 of 65 in the seeded sample. Multi-day flapping cannot be measured from existing data | Proven (rare) |
| 4 | Is there paging or segmentation that could fake an absence? | No paging. One file, one parse. The only false-absence path is a cut or malformed file, already refused. A feed **generated** short but well formed (a vendor-side bug) is the risk the guards address | Proven / Inferred |
| 5 | Can the feed change sharply between reads? | Not in 7 complete reads: the size moved by at most 0.46 % between consecutive reads. Churn is steady, about 1.2 % of the feed per day | Proven for the period |
| 6 | Is the per-employer widget better evidence? | The per-posting check is better than the widget. It needs no account slug guessing, because the short link names the account. It answers per posting, and it separates gone (`/oops`, 404) from unknown (5xx, timeouts) | Proven |
| 7 | Accounts without a widget, or with another slug? | The catalogue's board slug comes from the company name and can differ from the account slug (paragon, audit §5). The short link resolves the true account, so no widget is needed. A 5xx, timeout or 429 is unknown and closes nothing | Proven |
| 8 | A posting closed and later reopened? | Seen again under the same reference: it must reopen, keeping its history. Today's update path cannot do that (4.3), so it must be fixed first. Reposted under a new reference: a new row, the old one closes (no fuzzy merge) | Proven / Design |
