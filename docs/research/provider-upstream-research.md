# Provider upstream research (2026-10-16)

> **Snapshot, integrated 2026-10-20 as evidence, not as a plan.** It describes
> Career Agent and Career-Ops as they were on 2026-10-16. Two of its findings
> were implemented afterwards and the sections about them are history now:
> Gupy is read again at its new address (`7fe33c6`), and Pinpoint is a native
> family (`1bfc40b`, `d3b37c6`, `6840262`). The other candidates (Breezy HR,
> BambooHR, Hacker News Who is hiring, the per-account Workable widget, the
> Ashby embed fallback) are NOT built. Re-check any finding against the code
> before acting on it. `scripts/poc/provider_poc.py` is a research probe, not a
> collector: it reads robots.txt first, pauses between requests, writes nothing
> by default, and makes live requests only when run by hand.

Research branch `claude/provider-upstream-research`, cut from `main` at
`5421d43`. When written, nothing was integrated: no adapter changed, no database opened,
no catalogue row edited, no collection run, no model called. This document is
the input to a later, official Phase B.

Upstream reference: [career-ops-hq/career-ops](https://github.com/career-ops-hq/career-ops),
MIT, read at commit `55a5165` (2026-10-16). 110 provider modules plus two
Playwright scripts outside `providers/`.

Appendices (same directory, `provider-upstream-research/`):

* `appendix-a-careerops-inventory.md`: all 112 upstream rows, with endpoint,
  region, Brazil relevance, auth, pagination, fields, risk, effort, tier and
  test file. Tiers there are INTRINSIC (how good the source is for a candidate
  in Brazil); the tiers in this document are INCREMENTAL (what adding it would
  change for Career Agent, given what it already has).
* `appendix-b-careerops-discovery-and-contracts.md`: upstream ATS detection,
  provider contract, safety helpers, and Gupy / Greenhouse / Pinpoint / JazzHR
  in detail, with file:line references.
* `appendix-c-careeragent-inventory.md`: every Career Agent source, its state
  and the code that labels it on screen, with file:line references.

POC script: `scripts/poc/provider_poc.py` (stdlib only, at most 8 tenants per
run, robots.txt read first, 2 s between requests, prints counts only).

---

## 1. Career Agent today

66 catalogue rows, 28 registered adapters (appendix C has the full table).

| State | Sources |
|---|---|
| Implemented, employer boards | Greenhouse, Lever, Ashby, Workday, Recruiterflow, Teamtailor, Rippling, Recruitee, Comeet |
| Implemented, feeds | Get on Board (owner-run), Avlis, WWR, Remote OK (owner-run), Speedrun, Programathor, Himalayas, Jobicy, 4 Day Week, Jobgether (metadata only), Remotive, Working Nomads, Arbeitnow, Workable, Dynamite Jobs |
| Implemented, blocked upstream | Gupy (`collection_blocker`: old feed host answers nginx 404 since 2026-10-02) |
| Quota-limited | Jooble (500 requests per key for life; the key is a US key) |
| Built, switched off | Torre (`ROBOTS_DISALLOWS_API`); upstream retired it too on 2026-10-08 |
| Experimental | LinkedIn via python-jobspy, opt-in only |
| Discovery index, stores no posting | RemoteSource |
| No adapter, shown in the UI | Company career pages (manual `import-job` only), Pinpoint, JazzHR, Virtustant, Arc.dev, Crossover, OpenTalent |
| Forbidden / blocked / nothing published | 25 rows; see appendix C |

## 2. Upstream inventory, filtered by geography

Of 112 upstream rows: Brazil relevance HIGH 15, MEDIUM 31, LOW 63, n/a 3.
Remote/global HIGH 22. So **46 are geographically relevant (HIGH or MEDIUM)**
and 63 are local boards Career Agent should ignore (single-country government
boards, single large local employers, Asian and German local ATSs).

Of the 46 relevant ones, **27 Career Agent already has** in some form. The
interesting set is the remaining 19, below.

## 3. Matrix: Career Agent x Career-Ops

`CA` = Career Agent. Tier here is incremental for CA.

### 3.1 Already in both (compare, do not rebuild)

| Source | CA | Upstream | Difference worth a look |
|---|---|---|---|
| Greenhouse | full body, closes unseen | `content=true`, body capped at 4,000 chars | CA is ahead. See section 10. |
| Lever, Ashby | full | full; Ashby embed fallback when posting API is off on a tenant | Ashby fallback: check whether any CA Ashby board 404s for that reason. |
| Workable | global index / XML feed, 429 history | per-account widget `apply.workable.com/api/v1/widget/accounts/<slug>?details=true` | The widget is a per-board path that does not touch the rate-limited global index. Phase B candidate for registered Workable boards. |
| Gupy | blocked | `portal.gupy.io/api/job-search/jobs` | **Same backend, new host.** See section 10 and POC 6.1. |
| Get on Board, Himalayas, Remotive, Jobicy, WWR, Working Nomads, Remote OK, 4 Day Week, Speedrun | collected, CA reads bodies and hiring scopes | upstream drops most bodies (keeps title/url/location) | CA is ahead. Nothing to adopt. |
| Recruitee, Teamtailor, Rippling, Comeet, Workday | collected | collected | Upstream reads a Workday instance list (`wd1..wd12`) when only the instance is missing; CA refuses a partial identity. Keep CA's rule. |
| Arbeitnow | collected | Tier 3 upstream | nothing |
| Torre | off | retired | nothing |

### 3.2 Upstream has it, CA does not (the candidates)

| Provider | Upstream file | Acquisition | Board-wide or tenant | BR relevance | Remote | Auth | Desc in list | Risk | Effort | CA tier |
|---|---|---|---|---|---|---|---|---|---|---|
| Pinpoint | `providers/pinpoint.mjs` | public JSON `<slug>.pinpointhq.com/postings.json` | tenant | MEDIUM | MEDIUM-HIGH | none | yes, four HTML sections | L | S | **1** |
| Breezy HR | `providers/breezy.mjs` | public JSON `<tenant>.breezy.hr/json` | tenant | MEDIUM | HIGH | none | no (detail page) | L | S | **1** |
| BambooHR | `providers/bamboohr.mjs` | public JSON `<tenant>.bamboohr.com/careers/list` + `/careers/<id>/detail` | tenant | MEDIUM | MEDIUM | none | no | L | S-M | 2 |
| Hacker News Who is hiring | `providers/hackernews.mjs` | Algolia API, one thread a month | board-wide | MEDIUM | HIGH | none | yes (comment = posting) | M | M | 2 |
| Agentic Engineering Jobs | `providers/agentic-jobs.mjs` | documented REST + OpenAPI | board-wide | HIGH | HIGH | none | yes | M | S | 2 (blocked today, see 6.4) |
| JazzHR | `providers/jazzhr.mjs` | tenant HTML + JSON-LD detail; vendor sitemaps | both | LOW-MEDIUM | MEDIUM | none | detail only | M | M | 2-later |
| Getro | `providers/getro.mjs` | POST search API, collection id from `__NEXT_DATA__`, Referer + browser UA | board-wide per VC | MEDIUM | MEDIUM | none | no | M | M | 3 |
| Consider | `providers/consider.mjs` | POST, session cookie + CSRF primer, browser UA | board-wide per VC | MEDIUM | MEDIUM | session | no | H | M | 3 |
| Gem | `providers/gem.mjs` | public GraphQL batch | tenant | MEDIUM | MEDIUM | none | yes | M | M | 2-later |
| Zoho Recruit | `providers/zoho-recruit.mjs` | SSR parse | tenant | MEDIUM | MEDIUM | none | yes | M | M | 3 |
| HiringRoom | `providers/hiringroom.mjs` | SSR / JSON-LD, two layouts | tenant | MEDIUM (LATAM, mostly local on-site) | LOW-MEDIUM | none | portal layout only | M | M | 3 |
| Personio | `providers/personio.mjs` | SSR payload + `/xml` | tenant | LOW | MEDIUM | none | no | M | M | 3 (CA recorded UNREADABLE policy) |
| Jobvite | `providers/jobvite.mjs` | XML feed | tenant | MEDIUM | MEDIUM | none | yes | M | M | 3 |
| Generalist World | `providers/generalist-world.mjs` | SSR, one page | board-wide | MEDIUM | MEDIUM | none | teaser | M | L | 3 (RevOps fit, teaser only) |
| Landing.jobs | `providers/landingjobs.mjs` | JSON | board-wide | MEDIUM (Portuguese-speaking EU) | MEDIUM | none | no | L | L | 3 |
| NoDesk, Jobspresso | RSS | RSS | board-wide | MEDIUM | HIGH | none | no | L | L | 3 (small, no location) |
| Startup Jobs | `providers/startup-jobs.mjs` | RSS, 50 newest | board-wide | MEDIUM | HIGH | none | 170-char excerpt | M | L | **4** (POC 6.5) |
| CryptocurrencyJobs | RSS | RSS | board-wide | HIGH | HIGH | none | yes | L | L | 3 (Web3 niche only) |
| SmartRecruiters | `providers/smartrecruiters.mjs` | public API | tenant | MEDIUM | MEDIUM | none | detail | M | M | stays FORBIDDEN in CA (API host robots `Disallow: /`) until re-read |

### 3.3 Skip

All 46 upstream Tier 4 rows (appendix A section a), plus: Torre and EchoJobs
(retired), OCC (Mexico on-site, Cloudflare geo challenge), Tech in Asia, WTTJ
(CA records FORBIDDEN; upstream scrapes an Algolia key each run), Built In (CA
FORBIDDEN; upstream spoofs UA), the Asian / German / Nordic government and
single-employer boards, Startup Jobs (excerpt, no origin pointer).

## 4. Geographic filter, applied

* HIGH and implementable: Gupy (repoint), Pinpoint and Breezy (through tenant
  discovery), HN (free text, worldwide share measured), Agentic Jobs (when its
  API answers).
* MEDIUM: BambooHR, JazzHR (measured: 1,030 of 22,195 slugs in one vendor
  sitemap say `remote`, 4.6%; LATAM/Brazil 15, 0.07%), Gem, Getro, Consider.
* LOW for this candidate: HiringRoom (its 18 discovered tenants are Argentine
  and Peruvian employers such as an industrial group, banks, a newspaper; mostly
  on-site), Landing.jobs (Portugal relocation), Personio (DACH).

## 5. The "NOT RUN YET" sources, answered one by one

| CA source | Upstream equivalent | Mechanism | Public | Auth | Scope | Geography | Brazil/LATAM value | Adapt? | Tier |
|---|---|---|---|---|---|---|---|---|---|
| Jooble | none | partner API, key per country | key | yes | board-wide | US key | low with this key | no | stays quota-limited |
| Company career pages | `discover-ats.mjs` (host-only detection, name-guessing across 11 vendors) | ATS detection | yes | none | per company | any | high when it lands on a known ATS | **yes, as ATS discovery** (section 8) | 1 (as discovery) |
| Pinpoint | `pinpoint.mjs` | `postings.json` | yes | none | tenant | UK-centred, with LATAM tenants | medium | **yes** (POC 6.2) | 1 |
| RemoteSource | none | CA's own discovery index | yes | none | board-wide index | global remote | already working | already built | label fix only |
| JazzHR | `jazzhr.mjs` | tenant HTML, opt-in JSON-LD detail; CA knows vendor sitemaps | yes | none | both | mostly US on-site | low (measured, POC 6.3) | later | 2-later |
| OpenTalent | none | n/a | n/a | n/a | n/a | n/a | low (CA: not worth building) | no | 4 |
| Virtustant | none | page markup only (JSON-LD 200-char excerpt) | yes | none | single employer | LATAM staffing | medium, single employer | no upstream help | 3 |
| Arc.dev | none | n/a | n/a | n/a | n/a | global remote | possibly high | no upstream help; needs its own preflight | unassessed |
| Crossover | none | n/a | n/a | n/a | n/a | worldwide contractors | possibly high | no upstream help; needs its own preflight | unassessed |
| Gupy | `gupy.mjs` | `portal.gupy.io/api/job-search/jobs` | yes | none | board-wide | Brazil | very high | **yes, repoint** (POC 6.1) | 1 |
| Greenhouse | `greenhouse.mjs` | boards API | yes | none | tenant | global | high | CA already better | n/a |

**Which can leave "NOT RUN YET": Gupy (repoint), Pinpoint (new adapter),
Company career pages (as ATS discovery) and RemoteSource (it already runs; the
label is wrong, section 9).** JazzHR can follow but adds little for this
candidate. Virtustant, Arc.dev, Crossover and OpenTalent get no help from
upstream; Arc.dev and Crossover deserve their own preflight because both are
worldwide-contractor marketplaces.

## 6. POCs (all on 2026-10-16, from this machine, honest User-Agent)

Total live requests this session: about 45, all GET, all bounded, nothing
persisted outside the session scratchpad. No browser UA, no cookie, no session.

### 6.1 Gupy, `portal.gupy.io`

* `portal.gupy.io/robots.txt`: `User-agent: *` / `Disallow:` (empty), i.e.
  first-party ALLOW. The old host served no robots file at all.
* `GET /api/job-search/jobs?limit=10&offset=0` with **no keyword**: 200,
  `pagination.total = 84,988`. With `workplaceType=remote`: total **1,921**.
* Record shape is the old feed's: `id, name, description` (whole advert,
  ~2,000 chars on the sample), `careerPageName, careerPageUrl, type,
  publishedDate, applicationDeadline, workplaceType, city, state, jobUrl
  (https://<employer>.gupy.io/job/...), disabilities`.
* The two quirks CA documented on the old host reproduce exactly:
  `limit=100` returns `total = 100`, and `offset=10000` answers 400.
* Conclusion: the 2026-10-02 blocker is a host and path move, not a closed
  source. Upstream always sends a keyword; CA's keyword-free, sliced walk
  works on the new path. Phase B: change `API_HOST` and path, re-record the
  permission, re-capture fixtures, clear `collection_blocker`.

### 6.2 Pinpoint

* Tenant discovery: one Common Crawl index query (`CC-MAIN-2026-34`,
  `url=pinpointhq.com&matchType=domain`) named **45 tenants**, including
  SafetyWing, inDrive, Lingoda, Digital Science, Newfire Global. This removes
  the catalogue's only blocker ("no employer-published tenant").
* Liveness: a real tenant serves `robots.txt` allowing `/postings.json`; a
  non-existent tenant serves `Disallow: /` and `postings.json` 404.
* `postings.json`: `{data: [...]}`, no pagination, one request per tenant.
  Fields per posting: `id, title, url, path, description,
  key_responsibilities, skills_knowledge_expertise, benefits` (HTML, the whole
  advert in four sections), `employment_type`, `workplace_type`
  (`remote|hybrid|onsite`, STRUCTURED), `compensation_minimum/maximum/
  currency/frequency` + `compensation_visible`, `deadline_at`,
  `job.department`, `location.{name, city, province, postal_code}`.
  **No published date** and **no country code**: `location.name` is free text
  (`Brazil (Sao Paulo)`, `Remote`, `United Kingdom`).
* Measured on 6 tenants: 175 postings, 175 with description, 34 with a visible
  salary, 0 demo-trap rows. inDrive: 140 postings across ~25 countries
  including Mexico, Peru, Colombia, Brazil; Newfire: 16, all remote, Colombia,
  Argentina, Costa Rica among them.
* Upstream warning to keep: some abandoned trial tenants answer 200 with two
  canned postings (marker: a doubled YouTube embed). Validate a tenant against
  that marker before registering it. `answers_for_unknown_identifiers` is
  False for a truly unknown slug, so the marker check is the only trap.
* Upstream reads only title, url and location. CA would read everything above.

### 6.3 JazzHR

* `ratehubinc.applytojob.com/apply`: 11 postings parsed from the list page;
  2 detail pages carried JSON-LD `JobPosting` with description and
  `datePosted`. Works with an honest UA (upstream sends a browser-like UA).
* Vendor sitemap `app.jazz.co/feeds/google/xml/0`: 3.97 MB, 22,195 posting
  URLs, 4,534 tenants. Slug words: `remote` 1,030, `latam` 8, `brazil` 4,
  `global` 24, `anywhere` 12, `worldwide` 0. The sitemap sits behind
  Cloudflare (`__cf_bm` cookie) but answered a plain GET.
* Conclusion: confirms CA's 2026-09-11 classification. A slug prefilter on
  `remote` would cut detail fetches 20x. Low density for Brazil; later.

### 6.4 Agentic Engineering Jobs

* robots: `User-agent: *` `Disallow: /api/`; named AI agents (ClaudeBot,
  OAI-SearchBot, ChatGPT-User, Perplexity-User) get `Allow: /api/v1/`.
  `Content-Signal: ai-input=yes`. A generic collector is therefore DISALLOWED
  on the API while the agent is allowed: the inverse of Get on Board.
* `GET /api/v1/jobs` and `?page=1`: 500 `INTERNAL_ERROR` twice;
  `/api/v1/openapi.json` 200.
* Conclusion: right niche (AI / agents / automation), wrong day and an open
  permission question for the owner. Re-check later.

### 6.5 Startup Jobs

* robots allows the feed. `?workplace=remote`: 50 items, all with pubDate,
  average description 170 characters, links to startup.jobs pages, no origin
  ATS URL. 1 of 50 mentions LATAM.
* Conclusion: an excerpt aggregator without an origin pointer; Tier 4 for CA.

### 6.6 Hacker News "Who is hiring"

* `hn.algolia.com/robots.txt` 404 (silence). October 2026 thread: 266
  top-level posts; first line mentions remote 140, worldwide/anywhere/global/
  international 24, LATAM/Brazil/Americas 7, `Remote (US...)` 42; 110 carry a
  pay figure.
* Conclusion: ~25-30 genuinely worldwide postings a month from AI startups,
  free text, the comment is the whole posting (ADR-0002 quotes work). Identity
  must be the HN item id; most posts link the employer's ATS, which folds into
  existing families. Tier 2.

### 6.7 HiringRoom

* Common Crawl named 18 tenants, all Argentine / Peruvian / Chilean / Ecuadorian
  local employers (industry, banks, retail, a newspaper, one tech consultancy).
* `nubiral.hiringroom.com/robots.txt` answers 200 with the app's HTML instead
  of a robots file, i.e. UNREADABLE under ADR-0018. Not probed further.
* Conclusion: LATAM yes, remote-from-Brazil rarely. Tier 3.

## 7. Recommended order for Phase B

| # | Item | Why | Estimate |
|---|---|---|---|
| 1 | **Gupy repoint to `portal.gupy.io/api/job-search/jobs`** | The largest Brazilian source back, same contract; 84,988 postings, 1,921 remote | 0.5-1 day: host + path constant, robots re-read recorded in the catalogue, new fixtures from one capture, clear `collection_blocker`, tests |
| 2 | **Source-status vocabulary** (section 9) | Stops "Not run yet" lying about seven sources; needed before adding more | 1 day: map `maturity` + `health` to one state, i18n keys, browser test |
| 3 | **ATS discovery from careers pages** (section 8) | Turns "Company career pages" into boards on families CA already collects | 1-2 days: widen `site_detect` to all CA families, reuse `identify_board_url`, fixtures per family |
| 4 | **Pinpoint adapter** | POC proven, full body + structured workplace + salary, 45 tenants known | 1 day: adapter, demo-trap guard, catalogue row, fixtures, registry |
| 5 | **Common Crawl tenant harvest** (owner decision first) | One index query per family names tenants that guessing never finds | 1-2 days: bounded, resumable lead table reused from `board_discovery_lead`, every lead validated by the family's own listing |
| 6 | **Breezy adapter** | Simplest ATS JSON; agencies placing LATAM talent use it | 1 day incl. detail fetch for the body |
| 7 | **Hacker News Who is hiring** | Monthly worldwide AI-startup postings | 1-1.5 days: free-text location reading needs care |
| 8 | **Workable per-account widget** for registered Workable boards | Avoids the rate-limited global index | 0.5-1 day, measure first |
| 9 | BambooHR | Common SMB ATS, simple JSON, 302 on unknown tenants | 1 day |
| 10 | Agentic Engineering Jobs | Exact niche; blocked on the API failing and a permission question | 0.5 day once both clear |

Not recommended now: JazzHR (low density, measured), Getro and Consider (need
browser-like UA, Referer or a session primer: Tier 3 by this charter's rule),
Gem, Zoho, HiringRoom, Personio, Jobvite, the RSS niche boards.

Rough total for items 1-7: **7-10 working days**.

## 8. ATS discovery instead of a generic scraper

Upstream's detection is **host-only**: it never reads careers-page HTML. It
recognises 13 vendors by URL host (`ats-vendor.mjs`) and ~45 providers via their
own `detect()`, and finds tenants by guessing a slug from the company name
across 11 vendors in a fixed order, accepting the first board with at least one
job. Workday is never guessed. Only 404/410 means "not there"; SmartRecruiters
(200 empty for any slug), BambooHR (302 to www) and Pinpoint demo tenants are
the documented liars.

CA is stricter and in one place weaker:

* `scan-career-sites` reads the employer's own page (robots first), but
  `providers/site_detect.py` recognises only **Recruitee, Rippling, Teamtailor,
  Workday** on a careers page.
* `identify_board_url` already names a board from a posting URL for Ashby,
  Comeet, Greenhouse, Lever, Recruitee, Rippling, Teamtailor, Workable and
  Workday.

Proposal for "Company career pages" (no generic HTML understanding):

```
careers URL (employer-published)
  -> fetch page (robots first)            existing scan-career-sites
  -> collect every href / iframe src / script src on the page
  -> identify_board_url(each)             existing, extend families
  -> family.validate_board(identity)      existing contract
  -> register source_board                existing load path
  -> collect                              existing
```

The change is to feed `identify_board_url` the links a careers page carries
instead of only the four page markers, and to add recognisers for Pinpoint,
Breezy, BambooHR, JazzHR, SmartRecruiters (recognise, not collect) and Gupy
(`<employer>.gupy.io`). A page that names no known ATS stays NO_SIGNAL, which
is the honest outcome; there is no fallback scraper.

**Common Crawl as a tenant index** is the complementary idea: one CDX query per
family domain lists tenant hosts that real pages linked. It is a third-party
index of the public web, not a guess, and every lead would still have to pass
the family's own listing. Whether a host observed in a public crawl counts as
an "employer-published identity" under ADR-0013 / ADR-0018 is an **owner
decision**; this research only shows that it works (Pinpoint 45 tenants,
HiringRoom 18; the Breezy query returned nothing at the 4,000-row limit and
needs paging).

## 9. Source status vocabulary (documented, not implemented)

Today one chip, "Not run yet / Ainda nao rodou" (`sources/health.py:147`), is
shown both for a working adapter that never ran and for a source with no
adapter (a row without a provider looks up `""` and is NOT_RUN forever,
`health.py:282-294`). The group heading "Possible, nothing from it yet"
(`catalogue.py:154`) has no Portuguese key. `Source.maturity` already
distinguishes "No connector yet" from "Connector written, never run" and the UI
never reads it.

Proposed states, each derived from facts CA already stores:

| State | Derived from | PT label (proposal) |
|---|---|---|
| NOT_IMPLEMENTED | catalogue row, no provider | Ainda nao implementada |
| PLANNED | NOT_IMPLEMENTED + permitted + `unblocked_by` set | Planejada |
| EXPERIMENTAL | opt-in flag (LinkedIn) | Experimental |
| NOT_RUN_YET | provider exists, no run in the ledger | Implementada, ainda nao rodou |
| RUNNING | run row with `finished_at` NULL and progress | Rodando agora |
| SUCCESS_NEW | last run finished, new postings > 0 | Concluida, com vagas novas |
| SUCCESS_ZERO_NEW | finished, fetched > 0, new = 0, some not duplicates | Concluida, nenhuma vaga nova |
| ALL_DUPLICATES | finished, every parsed posting was a known sighting | Concluida, so vagas ja conhecidas |
| PARTIAL_FAILURE | finished with some boards/pages failed | Concluida com falhas parciais |
| FAILED | run failed | Falhou |
| DISABLED | switched off in code or by the owner (Torre) | Desativada |
| BLOCKED_UPSTREAM | `collection_blocker` (Gupy today) | Fonte fora do ar |
| QUOTA_EXHAUSTED | ledger says remaining = 0 (Jooble) | Cota esgotada |
| DISCOVERY_ONLY | discovery index (RemoteSource) | Usada so para descobrir empresas |

Two additions to the charter's list: BLOCKED_UPSTREAM (different from FAILED:
nothing on our side is wrong) and DISCOVERY_ONLY (RemoteSource never stores a
posting, so "zero new postings" would be a lie in either direction).

## 10. Gupy and Greenhouse, conceptual differential

| Aspect | CA Gupy | Upstream Gupy | Note for Phase B |
|---|---|---|---|
| Host / path | `employability-portal.gupy.io/api/v1/jobs` (dead) | `portal.gupy.io/api/job-search/jobs` | repoint (6.1) |
| Query | keyword-free, sliced workplaceType > type > state > city | one sweep per keyword | keep CA's; verified keyword-free works |
| Pagination | 100/page, offset cap 10,000, totals from `limit=10` probe | same cap, short page ends walk | identical behaviour on the new host |
| Tenant discovery | `careerPageId` from records | none | same |
| Canonical URL | `jobUrl` on `*.gupy.io` | same | same |
| Description | whole advert in list | same | same |
| Closed jobs | collector closes nothing; auto-update never runs Gupy | none | gap in both; a sliced full walk could close per slice once complete |
| Dedupe | external id, URL, content hash + board + title | URL | CA ahead |
| Confidential postings | not filtered | dropped by employer label `Confidencial` | consider: an anonymous employer still a real posting; decide in Phase B |

| Aspect | CA Greenhouse | Upstream Greenhouse | Note |
|---|---|---|---|
| Endpoint | `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | same | same |
| Body | whole | capped at 4,000 chars | CA ahead |
| Date | `first_published` | `first_published` | same |
| Closed | close unseen after a successful pass, hold drops >= 50% on >= 20 open | dead-board retirement after three 404s | CA ahead; upstream's "3 misses, 30 days" retirement is worth copying for boards that 404 |
| Discovery | guessed token, posting URL, employer-lead probe; `gh_jid` proves family not board | name guess; `gh_jid` followed through the embed redirect to find the board | **investigate**: following the embed redirect would resolve the board for `gh_jid` URLs on employer domains, which CA currently refuses |
| Dedupe | `(provider, external_id)` | URL | CA ahead |

## 11. Licensing and attribution

Career-Ops is MIT (Copyright (c) 2026 Santiago Fernandez de Valderrama). This
branch copies no upstream code: endpoints, field names and behaviours are facts
re-observed live. If Phase B adapts substantial code (for example the
`_salary-text` separator rule or the Pinpoint demo-trap marker logic), add the
MIT notice to `THIRD_PARTY_NOTICES.md` and `licenses/`, and cite the upstream
file and commit in the adapter docstring. Do not adopt `_profile-keywords.mjs`:
it turns the candidate's target roles into search keywords, which ADR-0019
forbids at the ingestion layer.

## 12. Risks

* Gupy moved hosts once without notice; it can move again. Keep the blocker
  mechanism and the one-request liveness probe.
* Pinpoint demo tenants return plausible fake postings; register nothing
  without the marker check.
* JazzHR's sitemap host is behind Cloudflare; today it answered a plain GET.
* Agentic Engineering Jobs: robots allows named AI agents and disallows `*` on
  the API. Which rule binds this product's collector is an owner reading of
  ADR-0018.
* Common Crawl discovery is lagged by weeks and incomplete; it finds tenants,
  never proves a board is current. The family's own listing proves that.
* None of the incremental sources changes eligibility logic; all of them land
  in `structured_geography` / hiring-scope readers that already exist.
