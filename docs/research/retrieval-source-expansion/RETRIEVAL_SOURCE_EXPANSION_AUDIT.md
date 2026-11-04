# Retrieval source expansion: audit (2026-11-03)

Phase A of the retrieval source expansion, written before any code. Branch
`claude/retrieval-source-expansion`, cut from `main` at `c2c4021`. Companion
documents: [the feasibility matrix](RETRIEVAL_SOURCE_FEASIBILITY_MATRIX.md) and
[the validation report](RETRIEVAL_SOURCE_EXPANSION_VALIDATION.md).

Every number about the corpus below was read from the live catalogue
(`data/shared/catalogue.db`) opened **read-only** (`mode=ro`); nothing was
written to it.

## 1. What collection is today

**One architecture, three adapter shapes** (`providers/base.py`):

| Shape | `RetrievalMode` | Examples | Boards come from |
|---|---|---|---|
| Employer ATS board, one employer per board | `BOARD_EXHAUSTIVE` | Ashby, Greenhouse, Lever, Workday, Recruitee, Rippling, Teamtailor, Comeet, Pinpoint, Recruiterflow | `config/companies.yaml` (synced additively into `source_board` on launch and at every refresh) and evidence-based discovery |
| Aggregator feed, many employers | `AGGREGATOR_FEED` | Workable XML, 4 Day Week, Himalayas, Jobgether, Speedrun, Gupy, Get on Board, Arbeitnow, Dynamite Jobs, Remotive, Jobicy, WWR, Working Nomads, Remote OK, Programathor | one catalogue row each |
| Query-driven | `QUERY_DRIVEN` | Jooble (quota), LinkedIn via JobSpy (owner opt-in), Torre (built, refused) | the targeted plan |

An adapter converts **shape, never meaning** (`test_provider_neutrality.py`
enforces it by import inspection). A new ATS family is one module plus one
entry per registry dictionary (`providers/registry.py`: factories, field maps,
capabilities, kinds, retrieval modes, by-company, URL and board recognisers),
a `config/sources.yaml` entry, a catalogue row and tenants in
`config/companies.yaml`. Pinpoint (`1bfc40b`, 2026-10-16) is the reference
example and was followed here exactly. No parallel architecture is needed or
was built.

**Corpus on 2026-11-03:** 281,589 jobs (265,772 open). By provider: Workable
139,349; 4 Day Week 46,241; Lever 21,451; Jobgether 16,993; Greenhouse 16,492;
Gupy 10,291; Ashby 9,400; Himalayas 8,969; then 19 smaller sources down to
Avlis 6. `config/companies.yaml` registers 290 boards (Ashby 125, Greenhouse
117, Lever 29, Pinpoint 4, Workday 3, Teamtailor 3, Recruiterflow 2, Rippling,
Recruitee, Comeet 1 each).

## 2. How a source is registered and shown

* **`config/source_catalogue.yaml`** says by what mechanism (`status`) and how
  usable (`coverage`) each source is, with the dated first-party
  `authorization`. `LIVE_INTEGRATION`, `OPERATIONAL` and `PARTIAL` are claims
  the loader verifies against the registry and the corpus (`sources/
  catalogue.py`), downgrading to `PLANNED` until postings exist. A
  `policy_review` block records terms, robots and technical blocks separately,
  and `policy_problems` checks it against ADR-0018 (an explicit prohibition in
  the terms or robots.txt for the route used is obeyed; a technical block is
  never bypassed and decides nothing).
* **Settings & Sources** renders the catalogue. A board family is ONE source
  in a refresh plan however many boards it reads (`web/source_refresh.py`);
  its stage is derived from the registry (`sources/matrix._stage_for`), so the
  web layer names no vendor.

## 3. Refresh, cooldown, maintenance, limits

* One orchestration for every button (`web/source_refresh.py`), plans frozen at
  the press; due = never refreshed, older than a day, or failed and past its
  wait.
* Board families run through `pipeline/collect.Collector`: list, then (for a
  family without the advert in the list) one detail request per posting,
  **fetched before the write transaction** so the network never holds the
  database lock. One board failing never stops the run (FetchError and any
  adapter exception are recorded per board). A board whose list answers is
  the only evidence that closes postings; a posting the list carries and the
  detail will not serve is counted and never closed.
* `net/fetcher.HttpFetcher`: honest User-Agent, 10 s connect / 30 s read
  timeouts, a 1 s per-host delay, 3 attempts with backoff for 429/5xx and
  connection errors, 404 terminal (`NOT_FOUND`), 401/403 terminal
  (`UNAUTHORISED`), a 200 that is not JSON is `MALFORMED` (never an empty
  board). Request caps and deadlines end work as "deferred", not failed.
* Dead boards: a 404 starts a dated streak; a board is switched off after 3
  consecutive 404s over at least 24 h (`SourceBoardRepo.mark_not_found`).
* Pacing exceptions per provider: `poll_interval_seconds` (Remotive, Jobicy);
  feeds with their own page limits and kept cursors (see `docs/SOURCES.md`).
* Pytest never runs beside live collectors (owner rule); collection uses no
  LLM anywhere.

## 4. Identity, canonical URL, deduplication

* Collection key: `(provider, external_id)`. Per-tenant families prefix the
  tenant (`pinpoint-<tenant>-<id>`) because ids are per-tenant integers.
* **Cross-source identity is deterministic, never fuzzy (ADR-0008).** Three
  rules, in order, in every feed collector (Workable, 4 Day Week, Gupy,
  Himalayas and the rest): (1) another provider already holds this
  `external_id`; (2) another provider holds a job at the same
  `canonical_posting_url` (scheme and host lowercased, query, fragment and
  trailing slash dropped; `pipeline/url_identity.py`); (3) the same text hash
  from the same company for the same title. A match is recorded as a SIGHTING
  (`job_discovery_source`) on the held job, never as a second job (ADR-0013).
  On top of that, each family recognises its own posting URLs
  (`_URL_RECOGNISERS`), which Speedrun, We Work Remotely, Himalayas, board
  discovery and `pipeline/fold_sighted.py` use to turn an aggregator's apply
  link into an exact `(provider, external_id)`. Ties are refused. A collector
  that already holds its own row stops at rule 1 for it, so a row that arrived
  before the employer's board was collected stays separate unless
  `fold_sighted` (origin-pointer aggregators only: Speedrun) folds it.
* **Reposts under a different job id** that no rule joins are NOT merged:
  they are grouped for presentation by `(company_id, title)` (migration 0014,
  `ScoredJobQuery._siblings_for`). The P2 finding (a repost of one role under
  a different job id) is consistent with this: the exposure checks keyed on
  `job_id` only, and nothing below the presentation layer claims two rows are
  one vacancy unless a deterministic key says so.
* Measured now: HireBoost's "Paid Media Manager" is two open rows (Jobgether
  and Himalayas, same company, same title, no origin link, and the two
  aggregators disagree on location: "Latin America" against a "United States"
  restriction). The `(company, title)` grouping shows them as one card; the
  data keeps both, which is right because the two readings differ.
* Measured now: the employers on the new BambooHR boards already reach the
  corpus through aggregators, with the BambooHR link only inside the
  description, never in the URL rules 1 and 2 compare: Enterprise Electrical
  has about 120 open Workable rows (it posts there too), First Intuition 10
  open 4 Day Week rows, and posting `freudigmanbillings/83` ("Senior
  Admissions Test Prep Instructor" on the employer's board) is linked from a
  Workable row titled "Tutor, SAT/ACT, Full Time" by the company "TPAPT".
  Reposts with the same title are grouped once the BambooHR rows share the
  aggregators' company row, which is why `config/companies.yaml` reuses the
  slugs `first-intuition` and `enterprise-electrical`. A different title
  (TPAPT's) is not joined, and TPAPT is not asserted to be Freudigman &
  Billings: a link in a description is not proof of one employer.

## 5. Discovery source versus original source

`ProviderKind` separates the employer's own record (ATS) from a republication
(AGGREGATOR). Postings keep `url` (the canonical employer posting for an ATS),
the archived payload verbatim, `posted_at` only from the source's own date
(RFC 3339 UTC through `to_rfc3339_utc`; never the discovery date, which is
`first_seen_at`), and field provenance through `ProviderFieldMap`. The
agency/recruiter case (Recruiterflow agencies, LatamCent) records the
recruiter as the company and never invents the unnamed client.

## 6. What the ten entries resolve to (identities deduplicated first)

| Entry | Identity | Type | Existing coverage |
|---|---|---|---|
| DailyRemote | dailyremote.com (Rapidr, Inc.) | aggregator | none; catalogue row added (BLOCKED) |
| BambooHR | per-tenant ATS family | ATS | none before; built here |
| Breezy HR | per-tenant ATS family | ATS | none; catalogue row added (PLANNED) |
| Torre | torre.ai / search.torre.co | marketplace | adapter built and refused since 2026-09-07 |
| Built In LA | builtinla.com, one of the Built In network (builtin.com and its city sites) | aggregator | catalogue row `builtin`, BLOCKED since 2026-09-11 |
| DailyRemote/HireBoost | the same two identities as rows 1 and 8 | question | answered in the matrix |
| Paired | Paired (paired.so), a recruiting firm; board = Workable account `pairedrecruiting` | ATS via Workable | Workable XML, 116 open rows |
| HireBoost | a small LATAM recruiting agency; not a platform | employer/agency | Jobgether 3 + Himalayas 2 rows |
| Near / Hire With Near | jobs.hirewithnear.com (Next.js, Bullhorn-shaped data) | agency | catalogue row `hirewithnear`, BLOCKED since 2026-10-09 |
| LatamCent | latamcent.com (WordPress WP Job Manager) whose apply links are the Ashby board `latamcent` | agency via Ashby | none before; one registry row here |

So ten entries are **eight identities** and needed **one new family**
(BambooHR) and **one registry row on an existing family** (Ashby). Everything
else is already covered, blocked by a first-party statement, or waiting for a
published tenant.
