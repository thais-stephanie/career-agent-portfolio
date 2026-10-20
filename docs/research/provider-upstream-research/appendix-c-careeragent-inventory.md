# Career Agent: job source and provider inventory

Read-only research on a separate worktree (branch
`claude/provider-upstream-research`, HEAD `5421d43`). Only source code, config
and docs were read. No database was opened, no collector or CLI was run, no
network request was made, no test was run.

Paths below are relative to the worktree root. `cat.yaml:N` means
`config/source_catalogue.yaml` line N.

## 0. Where the truth lives, and what is missing

| Question | File |
|---|---|
| Which sources exist, permission, declared status/coverage | `config/source_catalogue.yaml` (66 rows, `reviewed_on: 2026-09-04`, many rows re-reviewed later) |
| Verification of those claims against registry + DB | `src/career_agent/sources/catalogue.py::resolve` (567-684) |
| The state chip the UI shows per source | `src/career_agent/sources/health.py::SourceHealth.state` (111-156) |
| The "whose move is it" matrix (CLI/doc only, NOT the UI) | `src/career_agent/sources/matrix.py::_state_for` (346-387) |
| Which adapters exist | `src/career_agent/providers/registry.py::_FACTORIES` (132-161) - 28 registered names |
| Provider HTTP settings (read by nothing, documentation mirror) | `config/sources.yaml` (says so itself, lines 7-30) |
| Board registry that drives `collect` | `config/companies.yaml` (289 companies; boards: ashby 128, greenhouse 123, lever 30, workday 3, teamtailor 3, recruiterflow 2, comeet 1, recruitee 1, rippling 1) |
| Seed/regression boards | `config/companies.seed.yaml`; `config/company_candidates.yaml` is `candidates: []` |
| Prose | `docs/SOURCES.md`, `docs/product/source-leads-2026-10-14.md`, `docs/AUTO_UPDATE.md` |

NOT PRESENT in this worktree (referenced by code comments and by the private
project CLAUDE.md, but absent here): `docs/product/source-matrix.md`,
`docs/product/source-capability-matrix.md`,
`docs/product/career-ops-provider-matrix.md`, the older
`docs/product/source-leads-2026-09-10.md`, `docs/product/collection-policy.md`,
and ADR-0013/0018/0019 (only ADR-0032..0037 are in `docs/decisions/`). The
"measured counts" below therefore come from the catalogue `reason` text, the
comments in `config/sources.yaml`, adapter docstrings and the docs that do
exist. They are dated observations, not current corpus counts.

Status vocabulary used in the table (my classification, derived from code):

* implemented = adapter registered, catalogue LIVE_INTEGRATION, inventory proven at least once
* partially = implemented, but the source is a window / bounded walk / query-scoped / metadata-only
* configured = adapter exists, switched off for policy or budget reasons
* quota-exhausted = disabled because of a request budget
* blocked-or-forbidden = the vendor's terms or robots forbid it (catalogue BLOCKED/UNSUPPORTED)
* blocked-provider = technical/availability block recorded in `collection_blocker`
* UI-only-not-implemented = catalogue row, no adapter (the UI still renders it, see section 1)
* experimental = opt-in local override, off by default
* nothing-published = permitted but no openings published
* abandoned = classified and not worth building
* lead-only = only in a leads doc, not in the catalogue

Catalogue declared values are written as `status / coverage`. Permission,
Maturity and ContentDepth are DERIVED (catalogue.py 158-238, 360-410), not
declared: permission = FORBIDDEN for BLOCKED/UNSUPPORTED, UNDECLARED for
UNDOCUMENTED, else PERMITTED; maturity = NOT_BUILT if the row has no
`provider`, COLLECTING if the DB holds open postings, else BUILT; content
depth = FULL_ADVERT / EXCERPT_ONLY from the adapter's
`obtains_full_description`, UNKNOWN without a provider.

## 1. Inventory table

Legend for the "decl" columns: `obtains_full_description` = FD, `publishes_hiring_scope` = HS,
kind ATS/AGG, retrieval mode BOARD / FEED / QUERY.

### 1a. ATS board families (one board per employer, walked by `collect`)

| id (cat. line) | display name | adapter | kind / mode | my status | catalogue decl. -> derived perm / maturity / depth | how run | last measured (cited) | notes |
|---|---|---|---|---|---|---|---|---|
| greenhouse (61) | Greenhouse | `providers/greenhouse.py` | ATS / BOARD; FD yes, HS no; guessable yes | implemented (PARTIAL by design: registry-limited) | LIVE / PARTIAL -> PERMITTED / COLLECTING / FULL_ADVERT | `load-registry` then `collect` (or `collect --board greenhouse:<token>`) | 122 boards attempted, 121 responded, 1 NOT_FOUND, 9 empty, 11,134 postings full text (cat.yaml reason, 2026-09-10) | Growth = more registered boards; `?pay_transparency=true` not sent. 123 boards in companies.yaml. |
| lever (89) | Lever | `providers/lever.py` | ATS / BOARD; FD yes; guessable yes | implemented | LIVE / OPERATIONAL -> PERMITTED / COLLECTING / FULL_ADVERT | `collect` | no count in catalogue | 30 boards in companies.yaml. |
| ashby (106) | Ashby | `providers/ashby.py` | ATS / BOARD; FD yes; guessable yes | implemented | LIVE / OPERATIONAL | `collect` | LiveKit call 2026-09-10 (cat.yaml) | `includeCompensation=true` sent. 128 boards in companies.yaml. |
| workday (132) | Workday (CxS) | `providers/workday.py` | ATS / BOARD; FD yes (detail fetch); guessable NO | implemented | LIVE / OPERATIONAL | `collect`, `collect --provider workday --uncollected --max-boards N` | 73 Purple + 9 TFA postings, TFA Pre-Service 0 (cat.yaml, 2026-09-10) | Identity `tenant.shard/site`, never guessed; boards come from `scan-career-sites` or RemoteSource discovery. 3 boards in companies.yaml. |
| recruiterflow (1031) | Recruiterflow (LATAM agencies) | `providers/recruiterflow.py` | ATS / BOARD; FD yes (list then detail JSON-LD); guessable yes | implemented (PARTIAL) | LIVE / PARTIAL | `collect` | South 68, HireLatam 89 (config/sources.yaml comment, 2026-09-09) | Robots names Anthropic/Claude with Allow. 2 boards. |
| teamtailor (1282) | Teamtailor | `providers/teamtailor.py` | ATS / BOARD (`{origin}/jobs.rss`); FD yes; guessable yes | implemented | LIVE / OPERATIONAL | `collect --board teamtailor:<slug>` | career.teamtailor.com 12, PayFit 9, CS Puzzle 3 (cat.yaml, 2026-09-11) | Unregistered slug answers 404. |
| rippling (1309) | Rippling ATS | `providers/rippling.py` | ATS / BOARD (list then detail); FD yes; guessable yes | implemented | LIVE / OPERATIONAL | `collect --board rippling:<slug>` | 649 rows (540 office, 93 remote, 16 hybrid) for `rippling` (cat.yaml, 2026-09-11) | 1 board. |
| recruitee (1336) | Recruitee (Tellent) | `providers/recruitee.py` | ATS / BOARD (`/api/offers/`); guessable NO; `answers_for_unknown_identifiers = True` | implemented | LIVE / OPERATIONAL | `collect` | Tellent custom board 9 offers (cat.yaml) | Host answers for any slug, so a board must list the posting it came for. Registered as a board family (registry.py:416). |
| comeet (1605) | Comeet (Spark Hire Recruit) | `providers/comeet.py` | ATS / BOARD; FD yes; guessable NO | implemented | LIVE / OPERATIONAL | `collect --board comeet:tripleten/98.008` | TripleTen 335 rows = 47 vacancies, 23 rows in Brazil (cat.yaml, 2026-09-11) | Token read from hosted page at run time, never persisted. |
| gupy (396) | Gupy | `providers/gupy.py` + `pipeline/gupy_collect.py` | declared kind ATS but mode FEED (not in `_BY_COMPANY`, so not a `collect` board family) | blocked-provider (implemented, upstream gone) | LIVE / PARTIAL + `collection_blocker` -> PERMITTED (undeclared in fact) / COLLECTING / FULL_ADVERT | `collect-gupy --max-pages N` (manual only) | feed 81,310 postings; partitions remote 2,115 / hybrid 5,970 / on-site 72,682 (gupy.py docstring, 2026-09-09) | Feed stopped serving on 2026-10-02 (cat.yaml:424 `collection_blocker`). See section 3. |

### 1b. Aggregator feeds and boards (collected as a whole feed)

| id (cat. line) | display name | adapter | kind / mode | my status | catalogue decl. -> derived | how run | last measured (cited) | notes |
|---|---|---|---|---|---|---|---|---|
| weworkremotely (183) | We Work Remotely | `providers/wwr.py` | AGG / FEED (RSS by category); FD yes; HS YES (`region`) | partially | LIVE / PARTIAL | `collect-wwr` | 25 new, then 0 new / 25 seen; avg 5,053 chars (cat.yaml, 2026-09-05) | A window of ~25 per feed. |
| remoteok (267) | Remote OK | `providers/remoteok.py` | AGG / FEED; FD yes; HS no | partially | LIVE / PARTIAL | `collect-remoteok` (owner-run only: `matrix.OWNER_RUN_ONLY`) | no count in this repo's docs | Robots names ClaudeBot `Disallow: /`; agent must not run it. Salaries have no currency, never read. |
| a16z_speedrun (341) | a16z Speedrun Talent Network | `providers/speedrun.py` | AGG / FEED (list then detail); FD yes | partially | LIVE / PARTIAL | `collect-speedrun --max-pages N` | 100 postings, 82 new, 18 held; 10,050 servable of 18,479 (page 201 ceiling) (cat.yaml, 2026-09-05) | Continues 4 pages per refresh (docs/SOURCES.md). |
| programathor (465) | Programathor | `providers/programathor.py` | AGG / FEED (listing then JSON-LD per page); FD yes | partially | LIVE / PARTIAL | `collect-programathor --max-pages N` | ~half of postings answer HTTP 500 (cat.yaml) | BRL salary with period. |
| himalayas (651) | Himalayas | `providers/himalayas.py` | AGG / FEED (cursor) + targeted search endpoint; FD yes; HS YES | partially | LIVE / PARTIAL | `collect-himalayas --max-pages N` | feed total "above a hundred thousand"; bounded walk (cat.yaml) | Since 2026-09-25 also asks `/jobs/api/search` with a bounded query plan. |
| jobicy (693) | Jobicy | `providers/jobicy.py` | AGG / QUERY; FD yes; HS YES (`jobGeo`) | partially | LIVE / PARTIAL | `collect-jobicy` (at most once an hour) | 200 rows, 165 new, 35 folded (cat.yaml, 2026-09-09) | `Anywhere` stored as not stated. |
| fourdayweek (748) | 4 Day Week | `providers/fourdayweek.py` | AGG / FEED; FD yes; HS YES | partially | LIVE / PARTIAL | `collect-fourdayweek --max-pages N` | ~256 pages of 100, bounded at 300 (docs/SOURCES.md) | Link-back is the only condition. |
| jobgether (788) | Jobgether | `providers/jobgether.py` | AGG / QUERY; FD NO (metadata only); HS YES | partially (metadata-only leads) | LIVE / PARTIAL -> depth EXCERPT_ONLY | `collect-jobgether --max-requests N`; `enrich-leads` folds leads into employer postings | 250 rows a question (cat.yaml) | Terms forbid page extraction; only the AI-agent endpoint is read. |
| remotive (836) | Remotive | `providers/remotive.py` | AGG / FEED; FD yes; HS YES | partially | LIVE / PARTIAL | `collect-remotive` (max 4 polls a day) | 18 postings = whole public window (cat.yaml, 2026-09-11) | Specific grant beats robots `Disallow: /api/*`. |
| workingnomads (882) | Working Nomads | `providers/workingnomads.py` | AGG / FEED; FD yes; HS no | partially | LIVE / PARTIAL | `collect-workingnomads` | 32 postings, `?limit=200` same 32 (cat.yaml, 2026-09-07) | |
| arbeitnow (915) | Arbeitnow | `providers/arbeitnow.py` | AGG / FEED; FD yes; HS no | partially | LIVE / PARTIAL | `collect-arbeitnow --max-pages N` | 250 rows a page (cat.yaml) | Three hosts (.com, .co.uk, .fr). |
| getonbrd (968) | Get on Board | `providers/getonbrd.py` | AGG / FEED (category feeds); FD yes | implemented | LIVE / OPERATIONAL | `collect-getonbrd --max-pages N` (owner-run only) | 409 postings, 133 companies, 0 failures (cat.yaml, 2026-09-07) | Robots names ClaudeBot `Disallow: /`. |
| workable (1005) | Workable | `providers/workable_xml.py` (registered as `workable`, registry.py:91), legacy `providers/workable.py`, `pipeline/workable_collect.py`, `pipeline/workable_update.py` | AGG / FEED (one XML file); FD yes | partially | LIVE / PARTIAL | `collect-workable` (`--max-pages` deprecated); auto-update step `workable_update.py` | largest XML transfer 156,564 complete records in 776,858,449 bytes, transfers end incomplete ~62 s (cat.yaml); 71,777 stored then transfer failed (docs/checkpoints/first-run-flow-2026-10-10.md:17); search API `totalSize` 169,980 (config/sources.yaml) | XML replaced the rate-limited search API. Never closes on absence. |
| avlistalent (1171) | Avlis Talent | `providers/avlis.py` (provider `avlis`) | declared ATS, mode FEED; FD yes | implemented | LIVE / OPERATIONAL | `collect-avlis` | six postings (config/sources.yaml, 2026-09-09) | One request, no per-posting URL. |
| dynamitejobs (1570) | Dynamite Jobs | `providers/dynamitejobs.py` | AGG / FEED (sitemaps then JSON-LD); FD yes; HS YES | partially | LIVE / PARTIAL | `collect-dynamitejobs --max-postings N` | none stated | |
| jooble (217) | Jooble | `providers/jooble.py`, `providers/jooble_quota.py` | AGG / QUERY; FD NO (snippet); `full_description_in_list` no | quota-exhausted / configured | LIVE / PLANNED -> PERMITTED / BUILT / EXCERPT_ONLY; health state DISABLED_QUOTA (`matrix.QUOTA_DISABLED`) | no collector; `jooble-probe --keywords .. --location ..` (spends lifetime quota); needs `JOOBLE_DOMAIN` + key | one request 2026-09-06: 20 rows, totalCount 2,579 (cat.yaml) | 500 requests per key for life, key bound to one country (the configured key is US). `config/sources.yaml` `enabled: false`. |
| torre (1461) | Torre | `providers/torre.py` (registered) | AGG / QUERY; FD no | configured-and-disabled (hard gate) | SEARCH_LINK / BLOCKED, row has NO `provider` -> FORBIDDEN / NOT_BUILT (sic) | none; `ROBOTS_DISALLOWS_API = True` (torre.py:119) raises before any URL is built | caps at 20 rows a query (cat.yaml) | Adapter exists but the catalogue row does not name it, so derived maturity says NOT_BUILT. |
| linkedin_br (558) | LinkedIn | `providers/linkedin_jobspy.py` (provider `linkedin`), `pipeline/linkedin_collect.py`, `sources/experimental.py` | AGG / QUERY via python-jobspy; FD yes | experimental (off by default) | SEARCH_LINK / BLOCKED + `experimental_override: {provider: linkedin, via: python-jobspy, default: disabled}` | `collect-linkedin` only for an opted-in profile; Python 3.12 only | 24 searches x 25 cards default, <= ~72 requests, <= 60 page reads (docs/SOURCES.md) | Opt-in stored in `candidate_state` (`source_experimental.*`), versioned warning. |
| JobSpy: indeed (1789) | Indeed | none (JobSpy route reviewed, not built) | - | blocked-or-forbidden | SEARCH_LINK / BLOCKED with `policy_review` 2026-10-08 (terms + robots, HIGH) | search link only | - | REEVALUABLE. `policy_problems` (catalogue.py:490-557) forbids an adapter for it. |
| JobSpy: glassdoor (1714) | Glassdoor | none | - | blocked-or-forbidden | SEARCH_LINK / BLOCKED, `policy_review` (robots `Disallow: /graph`, terms GAP 403) | link only | - | REEVALUABLE. |
| JobSpy: google_jobs (1887) | Google (jobs in search) | none | - | blocked-or-forbidden | SEARCH_LINK / BLOCKED, `policy_review` (terms + robots `/search`) | link only | - | REEVALUABLE. |

### 1c. Discovery index (finds boards, stores no postings)

| id (cat. line) | display name | code | my status | catalogue decl. | how run | measured | notes |
|---|---|---|---|---|---|---|---|
| remotesource (1634) | RemoteSource | `providers/remotesource.py` (no `JobProvider`), `pipeline/board_discovery.py` | implemented as discovery; UI shows it as a not-run source | SEARCH_LINK / PLANNED, no `provider` -> PERMITTED / NOT_BUILT | `discover-remotesource --max-employers N [--execute]`, then `collect` | 33,163 posting URLs, 2,305 employers, 1,809 not in registry; origin sample Workday 49, Greenhouse 55, Lever 19, Ashby 19, SmartRecruiters 4, iCIMS 2, Rippling 2 (cat.yaml, 2026-09-11) | `unblocked_by: Nothing: the discovery walk is the integration.` Yet health state = NOT_RUN. |

### 1d. Catalogue rows with no adapter (permitted, unbuilt, or manual)

All of these render in the UI with the chip "Not run yet / Ainda nao rodou"
and under the heading "Possible, nothing from it yet" (section 2).

| id (cat. line) | display name | status / coverage | my status | why (cat.yaml `reason` / `unblocked_by`) |
|---|---|---|---|---|
| company_career_pages (377) | Company career pages | MANUAL_IMPORT / PLANNED | UI-only (manual import works) | "Not one source but thousands"; path is `import-job --company --title --description-file` (cli_local.py:2602) and `pipeline/manual_import.py`. |
| pinpoint (1497) | Pinpoint | SEARCH_LINK / PLANNED | UI-only-not-implemented | "Family unbuilt for want of one published tenant identity"; needs one employer-published `{tenant}.pinpointhq.com`. No code mentions Pinpoint. |
| jazzhr (1670) | JazzHR (applytojob.com) | SEARCH_LINK / PLANNED | UI-only-not-implemented (deferred) | "Permitted and unbuilt; a large, mostly US on-site family"; needs a family adapter reading JSON-LD/HTML over five sitemaps. |
| virtustant (2025) | Virtustant | SEARCH_LINK / PLANNED | UI-only-not-implemented | PERMITTED_NOT_BUILT; advert only in page markup; "a sitemap-plus-JSON-LD adapter in the Dynamite Jobs mould". |
| arc_dev (2109) | Arc.dev remote jobs | SEARCH_LINK / PLANNED | UI-only-not-implemented | "Permitted at a ten-second delay; unbuilt, and organised by occupation". |
| crossover (2147) | Crossover | SEARCH_LINK / PLANNED | UI-only-not-implemented (low value) | "one marketplace with no documented feed". |
| opentalent (1955) | OpenTalent | SEARCH_LINK / PLANNED | abandoned ("not worth building") | aggregator with no origin pointer and a wrong employer field. |

### 1e. Forbidden by terms or robots (no adapter; permission FORBIDDEN; health state DISABLED)

| id (line) | name | coverage | basis |
|---|---|---|---|
| wellfound (317) | Wellfound | BLOCKED | ToS forbids harvesting/scraping |
| vagas (516) | Vagas.com | BLOCKED | robots names our agent, `Disallow: /` |
| indeed_br (532) | Indeed Brasil | BLOCKED | terms; feed deprecated |
| trampos (620) | Trampos.co | BLOCKED | terms forbid crawler copying |
| sine (634) | Emprega Brasil / SINE | BLOCKED | gov.br login |
| hirewithnear (1082) | Hire With Near | BLOCKED | terms forbid scraping, `policy_review` 2026-10-09 |
| remoterocketship (1247) | Remote Rocketship | BLOCKED | robots `Disallow: /api`, paid API |
| wttj (1356) | Welcome to the Jungle | BLOCKED | terms; 89,320 Algolia hits measured |
| remote_com (1375) | Remote.com Jobs | BLOCKED | terms; 5,817 jobs measured |
| recruitcrm (1394) | RecruitCRM | BLOCKED | robots names Claude agents on /jobs/, /apply/ |
| builtin (1697) | Built In | BLOCKED | terms, 2026-09-11 |
| remotejobsglobal (2130) | Remote Jobs Global | BLOCKED | terms |
| smartrecruiters (2167) | SmartRecruiters | BLOCKED | API host robots (one named partner) |
| torre (1461), linkedin_br (558), indeed (1789), glassdoor (1714), google_jobs (1887) | see 1b | BLOCKED | see 1b |
| catho (588) | Catho | UNSUPPORTED (derived FORBIDDEN) | robots blocks search; terms 403 |
| infojobs_br (605) | InfoJobs Brasil | UNSUPPORTED (derived FORBIDDEN) | robots blocks detail; terms 404 |

### 1f. Technical / availability block (`collection_blocker`; health state BLOCKED_PROVIDER)

| id (line) | name | coverage | blocker |
|---|---|---|---|
| gupy (396) | Gupy | PARTIAL | feed stopped serving 2026-10-02 |
| elite_software_automation (1408) | Elite Software Automation | UNDOCUMENTED | Vercel Security Checkpoint |
| jobbol (1422) | Jobbol | UNDOCUMENTED | domain did not resolve |
| personio (1524) | Personio | UNDOCUMENTED | Vercel checkpoint, no tenant known |
| workatastartup (1550) | YC Work at a Startup | UNDOCUMENTED | 406, needs YC account |
| inhire (1976) | InHire | UNDOCUMENTED | ClaudeBot disallow (2026-09-11); the 2026-10-14 leads doc says that disallow is gone but the API robots is 403 and the page is a JS shell; row is stale |
| hiringcafe (2058) | HiringCafe | UNDOCUMENTED | Cloudflare JS challenge everywhere |
| getro (2086) | Getro network boards | UNDOCUMENTED | official API needs network admin credential |

### 1g. Permitted, nothing published (health state NOTHING_PUBLISHED)

somewhere (1214), ace_flow (1435), lemon_io (1448), bairesdev (2004).

### 1h. Leads not in the catalogue (`docs/product/source-leads-2026-10-14.md`)

| lead | finding (2026-10-14) | status |
|---|---|---|
| Remotar (remotar.com.br) | terms forbid robot/crawler copies | lead-only, forbidden |
| JobFluent | robots `ClaudeBot`/`Claude-User` `Disallow: /`; `/api/*` closed to all | lead-only |
| EU Remote Jobs | home and `sitemap.rss` 403 | lead-only, technical block |
| Gupy per company | `<company>.gupy.io/robots.txt` points to `job-boards.api.gupy.io/production/job-board-content?jobBoardName=google&subdomain=<company>` -> XML sitemap of posting pages; ambev 271 URLs, localiza 156 KB sitemap, boticario 403, magazineluiza 404; posting page has schema.org JobPosting + whole advert; terms not found | lead-only, owner decision |
| InHire | withdrawn: no public access found | lead-only |

## 2. How the UI decides the source label (and the NOT RUN YET conflation)

### 2.1 The data path

`GET /api/sources` -> `JobsApi.sources` (`src/career_agent/web/api.py:959-1063`):

1. `health(conn, catalogue_path=...)` (`sources/health.py:247-299`) calls
   `resolve(conn, path)` (`sources/catalogue.py:567-684`) and wraps every
   catalogue row in a `SourceHealth` with DB observations (pipeline_run
   attempts keyed by provider from the stage name, `source_board` facts, `job`
   counts per provider).
2. Each row is sent as `{**entry.as_dict(), refresh_mode, can_refresh, ...}`.
   `as_dict` (health.py:158-182) merges `Source.as_dict()` (catalogue.py:427-467:
   `coverage`, `coverage_label`, `coverage_plain`, `reason_plain`,
   `downgraded_because`, `permission`, `maturity`, `maturity_label`,
   `content_depth`, ...) with `state` and `note`.
3. `states` is a tally of `entry.state` across ALL catalogue rows
   (api.py:1003-1006).
4. `refresh` is built only for rows with an effective provider
   (`_refresh_progress`, api.py:1090-1119: `if not stage_for: return []`).

### 2.2 Two different label systems on the same panel

`src/career_agent/web/static/js/sources.js`:

**(A) Group headings = Coverage plain label (server-side English).**
`render` (sources.js:261-265) groups each row by
`source.state === 'BLOCKED_PROVIDER' ? source.state : source.coverage`.
The heading chip (sources.js:357-369) is
`key === 'BLOCKED_PROVIDER' ? t('source.state.BLOCKED_PROVIDER') : rows[0].coverage_plain || rows[0].coverage_label`
with `title = coverage_label`. `coverage_plain` comes from
`Coverage.plain_label` (catalogue.py:126-155):

| Coverage | plain_label (EN only, no PT key exists) | label (tooltip) |
|---|---|---|
| OPERATIONAL | Jobs are coming in | Operational |
| PARTIAL | Jobs are coming in, with a gap | Partially operational |
| BLOCKED | We are not allowed to fetch these | Blocked by terms or robots |
| UNSUPPORTED | We have no way in | Unsupported |
| UNDOCUMENTED | Reachable, but nobody has said we may | Reachable, terms undeclared |
| NOTHING_PUBLISHED | They do not publish open jobs | Permitted, nothing published |
| PLANNED | **Possible, nothing from it yet** (catalogue.py:154) | Planned, not demonstrated |

The `.tag` class has `text-transform: uppercase` (app.css:2629-2640), which
is why it renders as "POSSIBLE, NOTHING FROM IT YET". There is no i18n key
for any `coverage_plain` value (grep of `i18n.js` finds none), so in the
Portuguese UI the group heading stays English. The same applies to
`reason_plain`, `note` and `downgraded_because`, which are server English
inserted into PT templates (`sources.downgraded` "Rebaixada: {why}",
i18n.js:8932).

The PLANNED group contains three different populations:

* rows declared PLANNED with no `provider` (pinpoint, jazzhr, virtustant,
  arc_dev, crossover, opentalent, remotesource, company_career_pages): NOT
  implemented;
* rows declared PLANNED with a provider (jooble): implemented, disabled;
* rows declared OPERATIONAL/PARTIAL that `resolve` DOWNGRADES to PLANNED
  because the DB holds zero open postings from the provider
  (catalogue.py:628-638, `downgraded = "no posting from this source is in the
  corpus"`; and for a LIVE row with zero postings, status -> CONFIGURED with
  "the adapter exists but has never persisted a posting", 609-620): implemented
  but never run in this DB. On a fresh install every live adapter lands here.

The comment at catalogue.py:147-153 admits this: "PLANNED covers two different
situations -- permitted and unbuilt ..., and built but never run ... The heading
only has to be true of both, so it says nothing about whether an adapter
exists".

**(B) Row chip and top tally = `SourceHealth.state` (i18n'd).**
`row(source)` (sources.js:698-780) renders `t('source.state.' + source.state)`
(sources.js:771-775); the tally at the top renders `STATES` in order
(sources.js:32-35, 309-326). `state` is computed by health.py:131-156:

```
coverage in {BLOCKED, UNSUPPORTED}      -> DISABLED
id in QUOTA_DISABLED (jooble)           -> DISABLED_QUOTA
collection_blocker set                  -> BLOCKED_PROVIDER
coverage == NOTHING_PUBLISHED           -> NOTHING_PUBLISHED
coverage == UNDOCUMENTED                -> WAITING
quota_remaining == 0                    -> NEEDS_SETUP
not attempted and postings == 0         -> NOT_RUN        (health.py:147-148)
postings == 0                           -> ATTENTION
every board erroring                    -> ATTENTION
else                                    -> HEALTHY
```

| state | EN (i18n.js:1714-1722) | PT (i18n.js:6969-6977) | tone |
|---|---|---|---|
| HEALTHY | Working | Funcionando | good |
| ATTENTION | Needs a look | Precisa de atencao | bad |
| NEEDS_SETUP | Needs a key or quota | Precisa de chave ou cota | warn |
| WAITING | Waiting for permission | Esperando permissao | warn |
| NOT_RUN | **Not run yet** | **Ainda nao rodou** | neutral |
| DISABLED | Off, by their rules | Desligada, pelas regras deles | muted |
| BLOCKED_PROVIDER | Access blocked or unavailable | Acesso bloqueado ou indisponivel | warn |
| DISABLED_QUOTA | Off, quota exhausted | Desligada, cota esgotada | muted |
| NOTHING_PUBLISHED | No job board published | Sem mural de vagas publicado | neutral |

**The conflation, confirmed.** The NOT_RUN branch never checks whether the
row has a provider. For a providerless row `health()` looks up attempts,
boards and postings under the key `""` (health.py:282-294), which never
matches anything (`_stage_provider` never returns ""), so `attempted` is
False and `postings` is 0, and the row is NOT_RUN forever. Result: Pinpoint,
JazzHR, Virtustant, Arc.dev, Crossover, OpenTalent, RemoteSource and Company
career pages show exactly the same chip ("Not run yet / Ainda nao rodou"),
the same tone, the same heading ("Possible, nothing from it yet") and the same
`note` ("Never collected. Nothing has failed; nothing has run.", health.py:91-92,
displayed because NOT_RUN is in `RUNNABLE`, sources.js:45 and 703) as an
implemented adapter that simply has not been run in this database. They are
also counted together in the top tally. RemoteSource is worse: its discovery
integration exists and works, but the panel calls it "Not run yet".

What already distinguishes them but is not used by the panel:

* `Source.maturity` NOT_BUILT / BUILT / COLLECTING with labels "No connector
  yet" / "Connector written, never run" / "Collecting" (catalogue.py:200-217,
  380-387) is in the payload (`maturity`, `maturity_label`); `sources.js`
  never reads it (no occurrence of `maturity`). Caveat: it is derived from the
  catalogue row's `provider`, so Torre and LinkedIn (adapters registered, rows
  without `provider`) would read NOT_BUILT.
* `DOWNGRADE_PLAIN` (catalogue.py:286-300) gives "Nothing has been built to
  fetch these yet." vs "The connector is written, and no job has come through
  it yet." but only for downgraded rows and only inside the "Exact reason"
  disclosure (`sources.exactReason`); providerless PLANNED rows show their
  declared `reason_plain` instead.
* `MatrixState` (sources/matrix.py:82-113, `_state_for` 346-387) separates
  PERMITTED_NOT_BUILT ("Permitted, no adapter") from NOT_STARTED
  ("Implemented; no production run"), READY_OWNER_RUN, PRODUCTION_EMPTY and
  FAILED. It is used only by `career-agent source-matrix` (CLI and generated
  doc), not by the UI. Note it puts "postings" before `collection_blocker`, so
  Gupy reads PRODUCTION there while the UI reads BLOCKED_PROVIDER.

The second refresh vocabulary (health table and per-site cards) does NOT
conflate: `RefreshState` (sources/progress.py:55-92) -> `sources.state.*`
(EN i18n.js:2182-2193: NOT_STARTED "Not checked yet", QUEUED, RUNNING,
PARTIAL "Partly checked", COMPLETE "Checked", PAUSED, FAILED "Could not
update", BLOCKED "Currently unavailable", STALE "Out of date", DUE "Due for a
check", RATE_LIMITED "Asked us to wait"; PT i18n.js:7431-7441 "Ainda nao
consultado", ...). Those rows exist only for sources with an effective
provider (api.py:1108-1119), and the site card's "never" situation is
`row.state === 'NOT_STARTED' && !(source.postings > 0)` (sources.js:384-395;
`sites.pill.never` "Not checked yet" / "Ainda nao consultado",
`sites.card.never` "{name} has not been asked for jobs yet.").

## 3. Existing board discovery

| Mechanism | Command | Code | What it does |
|---|---|---|---|
| Guessed-identifier probe | `discover-boards --from <list.txt> --out <yaml>`; single: `discover-board` (cli_collect.py:431) | `pipeline/discover.py` (`candidate_identifiers` 171, `probe_board` 214, `discover` 302); cli_local.py:694 | Derives a bounded set of slugs from a domain and a name, asks each guessable family's `list_postings`. Outcomes VALID_NONEMPTY / VALID_EMPTY / NOT_FOUND / MALFORMED / TEMPORARY_FAILURE (discover.py:37-59). Never walks a namespace. Families = `registry.guessable_providers()` = ashby, greenhouse, lever, recruiterflow, rippling, teamtailor. Also `identifier_from_url` (281) parses a pasted board URL using each provider's own `board_url()` prefix. |
| Careers-page scan | `scan-career-sites --limit N [--company slug] [--execute]` (preflight by default) | `pipeline/site_discovery.py` (`SiteOutcome` 96-123, `careers_urls` 182 tries `/careers`, `/jobs`, `/careers/`, `/about/careers`, `read_site` 199, `detect()` at 262); patterns in `providers/site_detect.py` (`DETECTORS` 330-335, `detect` 343); robots via `net/robots.py` before every request; cli_local.py:238 | Reads the employer's own page, outcomes REFUSED_BY_HOST / UNREACHABLE / NO_SIGNAL / PROVIDER_DETECTED / IDENTITY_RESOLVED / ALREADY_KNOWN. Writes nothing. **Detects only four families: recruitee, rippling, teamtailor, workday** (Workday host `{tenant}.wdN.myworkdayjobs.com|myworkdaysite.com/[locale/]{site}` and `/wday/cxs/{tenant}/{site}/jobs`; Recruitee `{slug}.recruitee.com` + embed markers; Teamtailor `{slug}.teamtailor.com` + `teamtailor-cdn.com` etc.; Rippling `ats.rippling.com/{slug}/jobs`). Greenhouse, Lever, Ashby, Comeet, SmartRecruiters, Pinpoint, JazzHR, Gupy, iCIMS are NOT detected on a careers page. |
| Published posting URL -> board | (library) | `registry.identify_board_url` (registry.py:679-696), `_BOARD_RECOGNISERS` (666-676), `board_recogniser_families()` (699); posting identity `identify_posting_url` (704-728) over `_URL_RECOGNISERS` (334-343) | Families that can name a board from a URL: **ashby** (`jobs.ashbyhq.com/<board>/<uuid>`), **comeet**, **greenhouse** (`boards|job-boards(.eu).greenhouse.io/<board>/jobs/<id>`; `gh_jid` on an employer domain proves family only), **lever** (`jobs(.eu).lever.co/<board>/<uuid>`), **recruitee** (`<slug>.recruitee.com/o/...`), **rippling** (`ats.rippling.com/<board>/jobs/<uuid>`), **teamtailor** (`<slug>.teamtailor.com/jobs/<id>`), **workable** (account named, but FEED_COVERED), **workday** (all three parts or nothing). Ties are refused. Posting recognisers: ashby, comeet, greenhouse, lever, speedrun, torre, workable, workday. |
| Aggregator index -> boards | `discover-remotesource --max-employers N [--execute]` (cli_local.py:380) | `providers/remotesource.py` (sitemap index, one page per employer, reads `applicationUrl`), `pipeline/board_discovery.py` (`LeadOutcome` 90-123, `classify_origin` 185-247, `BoardDiscovery` 248, `supported_families` 612) | Apply URL -> `identify_board_url` -> the family's adapter lists the board (cap 200) -> registered as `source_board` with `discovery_method = aggregator_origin_pointer`; one `board_discovery_lead` row per employer, resumable. Outcomes REGISTERED / ALREADY_REGISTERED / VALIDATION_FAILED / VALIDATION_DEFERRED / FEED_COVERED / UNSUPPORTED_FAMILY / NO_ORIGIN / UNREADABLE / REFUSED_BY_HOST. Embedded `gh_jid` links become a deferred probe. |
| Aggregator employer -> ATS board | `discover-employer-boards` (cli_local.py:845); `enrich-leads [--no-probe] [--max-companies N]` (cli_local.py:5068) | `pipeline/employer_boards.py` (`employers_to_probe` 128, `probe_employer` 202, `discover_employer_boards` 237), `pipeline/enrich_leads.py`; families `registry.employer_probe_providers()` = ashby, greenhouse, lever (registry.py:478-487); lead sources `employer_lead_providers()` (aggregators minus workable) | Probes the three families with identifiers from the employer name; a board is accepted only when its listing carries a title the aggregator showed for that employer. Every answer is recorded so it is never probed twice. |
| Registry sync | `load-registry` (cli_collect.py:64) and on launch / refresh start | `config/companies.yaml` -> `source_board` | Additive; never reactivates or edits a held board (docs/SOURCES.md). |

## 4. Gupy and Greenhouse adapters

### 4.1 Gupy

* Endpoint: `GET https://employability-portal.gupy.io/api/v1/jobs` (gupy.py:121-125, `API_HOSTNAME` is the only host fetched; `assert_trusted`). Response `{"data": [...], "pagination": {...}}`. Not `api.gupy.io` (employer API, Bearer).
* Pagination: `limit=100&offset=N` (`PER_PAGE = 100`, `limit=1000` answers 400); `MAX_OFFSET = 10_000` hard (offset 10000 answers 400). `pagination.total` is only honest at `limit=10`, so totals come from a separate probe (`slice_count_url`, gupy.py:539-557). Walk via `providers/feeds.walk_feed` with a `PageBudget` (`--max-pages`, default 2 per partition).
* Partitioning: slices by `PARTITION_ORDER = ("workplaceType", "type", "state", "city")` (gupy.py:226); `plan_slices` (382) cuts a cell until it fits under the ceiling and keeps the parent so rows missing the cut dimension are not lost; cities learned from the corpus (`_cities_for`, gupy_collect.py:442). Measured 2026-09-09: remote 2,115, hybrid 5,970, on-site 72,682; feed 81,310.
* Tenant discovery: there is none to do; the portal returns every employer. A "board" is bookkeeping created from what arrived: `board_identifier = careerPageId` (gupy_collect.py:511-526, fallback slugified `careerPageName`), `board_url` = origin of `careerPageUrl` on `*.gupy.io` (528-546), `discovery_method = "gupy_portal"`. `list_postings` raises (gupy.py:747) because Gupy is not walked per board; it is not in `_BY_COMPANY`, so `collect` and `discover` never touch it.
* Canonical URL: `jobUrl` = `https://<employer>.gupy.io/job/<token>`, accepted only if https and on `*.gupy.io` (`public_url`, gupy.py:302-315). That is the ADR-0013 origin pointer.
* external_id: `gupy-<id>` (gupy.py:318-326). A stub needs id, `name` (title) and `jobUrl` (to_stub 329).
* Description/detail: `description` is the whole advert in the listing; `fetch_posting` makes no request (gupy.py:753-765); `_strip_html` to text. `obtains_full_description=True`, `publishes_hiring_scope=False`; field map `workplaceType` -> work_model_hint, `type` -> employment_type_hint (contract regime, e.g. CLT/PJ); `city, state, country` composed into `location_raw` (compose_location 280).
* Dedupe (gupy_collect.py:295-351): (1) external_id already held by ANOTHER provider -> sighting (`_record_sighting`, only writes `job_discovery_source`); (2) canonical URL match against another provider's job -> sighting; (3) same content hash + same board identifier + same title on another provider -> sighting; else `_persist_new` (upsert company, source_board, `jobs.upsert_seen`, payload). Overlapping slices are deduped in-run by a `seen` id set.
* Closed jobs: the Gupy collector closes nothing (no close call in `gupy_collect.py`); absence from a bounded partition walk is not evidence. A job is only re-marked seen. The auto-update never runs Gupy (`web/auto_update.py:52`, docs/AUTO_UPDATE.md:97).
* Run ledger: stage `collect-gupy`; a failed plan finishes FAILED; one failed slice is recorded and the run continues.
* **collection_blocker** (cat.yaml:424): "The candidate feed used for refresh stopped serving jobs on 2026-10-02. Previously collected jobs are still available." `reason_plain` says the same; `unblocked_by`: "The known endpoint answering again on a single bounded recheck, or a documented public replacement. Not the portal's server-rendered HTML." Effect: health state BLOCKED_PROVIDER ("Access blocked or unavailable" / "Acesso bloqueado ou indisponivel"), UI groups it under that heading, `can_refresh` is False (web/source_refresh.py:83-98), it is excluded from every refresh plan (api.py:1131-1148). `collect-gupy` itself is still callable from the CLI. docs/AUTO_UPDATE.md describes the feed host as answering 404. The 2026-10-14 leads doc records a possible replacement (per-company sitemap feed at `job-boards.api.gupy.io/production/job-board-content?...`, posting pages with JSON-LD), terms unverified, owner decision.

### 4.2 Greenhouse

* Endpoint: `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` (`API_BASE` greenhouse.py:55, `jobs_url` 135). One request per board returns every posting with its full HTML body; no pagination parameter; no detail request (`fetch_posting` 201 is local, `html_to_text`).
* Public board URL: `board.board_url` or `https://boards.greenhouse.io/{token}` (132).
* Stub (`_to_stub` 168-199): external_id = `id`, title, url = `absolute_url` (fallback `{board}/jobs/{id}`), `location.name` -> `location_raw` and the one field-map entry (HIRING_LOCATION_HINT, office text, `publishes_hiring_scope` False), department, `posted_at = first_published` (never `updated_at`). Malformed entries counted in `postings_skipped`.
* Errors: a body without `jobs` list raises `FetchError(MALFORMED)`; HTTP failures raise; an empty `jobs` list is a valid empty board.
* Compensation: `read_compensation` (218) reads pay ranges from board custom fields in `metadata` (633 of 10,156 archived payloads); budget fields excluded; `?pay_transparency=true` not sent.
* Tenant discovery: token guessed from company name by `discover-boards` (guessable), employer-lead probes, or a hosted posting URL via `recognise_board_url` (428, `boards|job-boards(.eu).greenhouse.io/<board>/jobs/<id>`). `recognise_posting_url` (379) claims `gh_jid` on any host or a path id on `*.greenhouse.io` (proper subdomain test).
* Closed jobs (shared `pipeline/collect.py`, module docstring 1-20, `_close_or_hold` 620-683): `pass_started_at` captured first; any failure returns without closing; after a successful pass, postings on that board not seen are closed via `jobs.close_absent(board_id, seen_external_ids)` (repositories.py:588), unless `DisappearancePolicy` (>= 20 previously open and >= 50% missing) holds the board as suspicious until a second pass confirms. A posting the board lists and will not serve is kept in the seen set and never closed.
* Dedupe: per board by `(provider, external_id)` via `jobs.upsert_seen` (repositories.py:449); content hash per text; cross-source identity is resolved by aggregators against Greenhouse URLs, not by the board collector.
* Catalogue: LIVE / PARTIAL; 122 boards, 121 answered, 11,134 full-text postings; the limit is the registry, not the collector.

## 5. Provider contract (for specifying a new adapter)

`src/career_agent/providers/base.py`:

* `JobProvider` (runtime-checkable Protocol, 495-627). Required class attributes: `name: str`, `kind: ProviderKind` (ATS | AGGREGATOR, no default, 39-63), `capabilities: ProviderCapabilities`, `field_map: ProviderFieldMap`. Defaults a new adapter must override when different: `retrieval_mode = RetrievalMode.BOARD_EXHAUSTIVE` (BOARD_EXHAUSTIVE | AGGREGATOR_FEED | QUERY_DRIVEN, 66-94), `addresses_boards_by_company = True` (539), `identifier_is_guessable = True` (553), `answers_for_unknown_identifiers = False` (564), `postings_skipped = 0` counter (580).
* Methods: `board_url(board) -> str`; `validate_board(board) -> str | None` (optional, no request, 586-609); `list_postings(board) -> Iterator[PostingStub]` (must raise `FetchError` on failure, never return empty to signal a problem, 611-619); `fetch_posting(board, stub) -> RawPosting` (local when the list carried the body, 621-627).
* `ProviderCapabilities` (107-157): `full_description_in_list`, `obtains_full_description` (required, no default), `exposes_posted_date`, `exposes_department`, `exposes_compensation`, `exposes_location_structured`, `exposes_remote_flag`, `exposes_employment_type`, `publishes_hiring_scope = False`.
* `ProviderFieldMap` / `FieldMapping(path, MetadataDimension)` (160-209): dotted object paths only; dimensions are `*_hint` (work_model, hiring_location, employment_type, compensation). An empty map is a deliberate statement.
* Data classes: `BoardRef(company_slug, provider, board_identifier, board_url)` (97-104); `PostingStub(external_id, title, url, location_raw, department, posted_at RFC 3339 UTC via `to_rfc3339_utc`, description_html, payload)` (448-473); `RawPosting(stub, description_html, description_text, payload)` (476-492); `canonical_url` (390-415); `CompensationHint` / `CompensationBand` (299-387).
* Optional module-level callables, registered in `registry.py` rather than on the protocol: `read_compensation(payload)` (`_COMPENSATION_READERS` 367-382, plus `_COMPENSATION_PATHS` 404-410 for the neutrality guard), `recognise_posting_url(url)` (`_URL_RECOGNISERS` 334-343), `recognise_board_url(url)` (`_BOARD_RECOGNISERS` 666-676), `hiring_scope(payload)` (`_HIRING_SCOPE_READERS` 553-566, only when HS is true).
* Registration checklist (registry.py): add to `_FACTORIES` (132), `_FIELD_MAPS` (173), `_CAPABILITIES` (216), `_KINDS` (248), `_RETRIEVAL_MODES` (285) (tests assert these key sets match); add to `_BY_COMPANY` (415) if it is a board family (this makes it a `collect` family via `board_providers()` and, with `identifier_is_guessable`, a `discover` family); optional `poll_interval_seconds` (101) for a cooldown; `site_detect.DETECTORS` for careers-page detection.
* Outside the adapter: a `config/source_catalogue.yaml` row (`id`, `name`, `region`, `status`, `provider`, `authorization`, `coverage`, `reason`, `reason_plain`, `unblocked_by`, optional `collection_blocker`, `search_url`, `policy_review`, `experimental_override`); the mirror entry in `config/sources.yaml` (a test keeps field maps in sync); a CLI collector `collect-<provider>` for feeds, or nothing extra for a board family (shared `collect` stage, `matrix._stage_for`); the `can_refresh` check needs `_stage_for(provider)` to be `collect` or an existing command (web/source_refresh.py:83-98); fixtures under `tests/fixtures/providers`; `tests/unit/test_provider_neutrality.py` forbids vendor names/paths in generic code.
* Policy hooks: `policy_problems` (catalogue.py:490-557) refuses an adapter registered under a provider whose every reviewed route is EXPLICIT_PROHIBITION; Torre shows the hard-gate pattern (`ROBOTS_DISALLOWS_API`); owner-run sources are listed by hand in `matrix.OWNER_RUN_ONLY` (128-141) and budget-disabled ones in `matrix.QUOTA_DISABLED` (148-153).
