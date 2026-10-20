# Career-Ops ATS detection, discovery and provider study

Upstream: `career-ops` at commit 55a5165 (MIT). Read-only study: no code run, no network.
All paths are relative to the upstream root. Line numbers are from that commit.

---

## 0. Headline findings

1. **Detection is URL-host based, never HTML based.** There is no careers-page sniffer
   (no script-src, iframe or HTML-marker detection) anywhere in the discovery path.
   The only content-derived signal is in the liveness layer: a `?gh_jid=<digits>` query
   parameter on a non-Greenhouse host is resolved through the Greenhouse embed redirect
   (`liveness-api.mjs:85-108`). Everything else maps a URL to a provider by hostname
   (and sometimes path) pattern.
2. **"Company name -> provider + slug" is a GUESS-AND-PROBE loop.** `discover-ats.mjs`
   derives a slug from the company name (lowercase, non-alnum to `-`), builds a candidate
   board URL for 11 vendors in a fixed order, and calls each provider's real `fetch()`;
   the first board returning >= 1 job wins. Workday is excluded from guessing and needs an
   explicit hint (full URL or `{tenant, site}`), with a bounded 8-instance probe when only
   the `wdN` is missing.
3. **Gupy: Career-Ops switched hosts.** It now calls
   `https://portal.gupy.io/api/job-search/jobs` (`providers/gupy.mjs:80`). The header
   states that the earlier host `employability-portal.gupy.io` "has answered a bare nginx
   404 on every path since early October 2026" (`gupy.mjs:13-15`). The old host is kept
   only so an old `api:` entry still routes to the provider (`gupy.mjs:82-85`); `fetch()`
   only ever calls the portal host. It is keyword-driven (`jobName=`), not per tenant, and
   per-tenant `<slug>.gupy.io` pages are deliberately NOT claimed.
4. **Pinpoint and JazzHR are thin.** Pinpoint reads `https://<slug>.pinpointhq.com/postings.json`
   and keeps only title, url, location (no description, no date, no id, even though the
   feed carries a description). JazzHR scrapes the server-rendered
   `https://<tenant>.applytojob.com/apply` HTML with a browser-like UA; description and
   date come only from an opt-in per-posting JSON-LD detail fetch.
5. **Most remote boards drop the description** even when the feed has it (remotive,
   himalayas, jobicy, workingnomads, remoteok, getonbrd, 4dayweek, speedrun, landingjobs,
   manfred, wwr, nodesk, jobspresso). Description is carried by Greenhouse (content=true),
   Lever, Ashby, Workable, Recruitee, Gupy, Gem, Zoho, HiringRoom, agentic-jobs, remotli,
   generalist-world (teaser), builtin (JSON-LD teaser), and opt-in for SmartRecruiters and JazzHR.
6. **Torre and EchoJobs are retired** in Career-Ops (fetch throws on purpose).

---

## 1. ATS detection and discovery

### 1.1 `ats-vendor.mjs` (51 lines): URL -> vendor label

- `hostIs(host, suffix)` = exact or dot-suffix match (`ats-vendor.mjs:10`).
- `ATS_HOST_PATTERNS` (`ats-vendor.mjs:13-30`), host-only signals:

| id | host pattern |
|---|---|
| greenhouse | `greenhouse.io` and subdomains |
| lever | `lever.co` |
| ashby | `ashbyhq.com` |
| workday | `myworkdayjobs.com`, `myworkdaysite.com` |
| icims | `icims.com` |
| successfactors | `successfactors.com`, `successfactors.eu`, `jobs2web.com` |
| dayforce | `dayforcehcm.com` |
| ultipro | `ultipro.com`, `ultipro.ca` |
| taleo | `taleo.net` |
| bamboohr | `bamboohr.com` |
| smartrecruiters | `smartrecruiters.com` |
| csod | `csod.com` |
| oraclecloud | `/(^|\.)oraclecloud(?:[1-9]|[1-9]\d)?\.com$/` (numbered apexes) |

- `atsVendorOf(url)` returns the vendor id, else the lowercased hostname (long tail kept as
  a signal), else null for non-http(s) (`ats-vendor.mjs:39-51`).
- Consumers: `history-ats-seeds.mjs`, `scan-ats-full.mjs` (`KNOWN_ATS_VENDORS`, line 75),
  `liveness-api.mjs`.

### 1.2 Provider routing: `providers/_registry.mjs` (97 lines)

- `loadProviders(dir)`: imports every `providers/*.mjs` not starting with `_`, in
  ALPHABETICAL order, so `detect()` priority is deterministic; a default export must be
  `{ id, fetch }`; duplicates keep the first (`_registry.mjs:24-51`).
- `resolveProvider(entry, providers)` (`_registry.mjs:68-97`):
  1. explicit `provider:` field wins (skips detect);
  2. `local-parser` if it detects (a user-configured local command);
  3. each provider's `detect(entry)` in load order, first non-null wins; a throwing
     detect is logged and skipped.
- `detect()` returns `{ url }` (informational only) or null (`_types.js:157-163`).

Full auto-detect catalogue as documented for users (`templates/portals.example.yml:1162-1236`):
greenhouse `(job-)?boards(.eu)?.greenhouse.io/<slug>`; ashby `jobs.ashbyhq.com/<slug>`;
lever `jobs.(eu.)?lever.co/<slug>`; workable `apply.workable.com/<slug>`; smartrecruiters
`(careers|jobs).smartrecruiters.com/<slug>`; recruitee `<slug>.recruitee.com`; bamboohr
`<tenant>.bamboohr.com`; breezy `<tenant>.breezy.hr`; solidjobs; comeet (api: only);
personio `<slug>.jobs.personio.(de|com)`; pinpoint `<slug>.pinpointhq.com`; prevueaps;
rippling `ats.rippling.com/<slug>/jobs`; jibeapply; jobvite `jobs.jobvite.com/<slug>`;
tencent; meituan; alibaba; gupy `portal.gupy.io`; feishu-jobs; mokahr; workday
`<tenant>.wd<N>.myworkdayjobs.com[/<locale>]/<board>`; avature; amazon; successfactors;
oraclecloud; taleo; eightfold; gem `jobs.gem.com/<boardId>`; join `join.com/companies/<slug>`;
icims; jazzhr `<tenant>.applytojob.com`; pythonorg; generalist-world; applitrack;
schoolspring; neogov; redrover; peoplesoft (PATH signature, not host); pageup; ultipro;
adp-workforcenow; zoho-recruit `<tenant>.zohorecruit.com`. Explicit-only (no detect):
phenom, eploy, builtin, getro, consider, and every `job_boards` feed selected by `provider:`.

### 1.3 `discover-ats.mjs` (1,297 lines): company list -> board

Flow, all in one file:

1. Input: YAML `companies: [{name, slug?, website?, workday?}]` or bare CLI names
   (`parseCompanyInput`, `discover-ats.mjs:185-241`). Dedup by lowercased name.
2. `deriveSlug(name)`: lowercase, `[^a-z0-9]+ -> -`, trim dashes (`:169-174`). Camel-case
   Ashby boards (AlephAlpha, DeepL) need an explicit `slug`.
3. `VENDORS` table (`:94-108`) builds one careers URL per vendor:

| key | provider | careers URL built |
|---|---|---|
| gh | greenhouse | `https://job-boards.greenhouse.io/<s>` (+ `api:` `boards-api.greenhouse.io/v1/boards/<s>/jobs`) |
| ashby | ashby | `https://jobs.ashbyhq.com/<s>` (case kept) |
| lever | lever | `https://jobs.lever.co/<s>` |
| workable | workable | `https://apply.workable.com/<s>` |
| smartrecruiters | smartrecruiters | `https://careers.smartrecruiters.com/<s>` |
| rippling | rippling | `https://ats.rippling.com/<s>/jobs` |
| join | join | `https://join.com/companies/<s>` |
| recruitee | recruitee | `https://<lower(s)>.recruitee.com` |
| breezy | breezy | `https://<lower(s)>.breezy.hr` |
| bamboohr | bamboohr | `https://<lower(s)>.bamboohr.com` |
| pinpoint | pinpoint | `https://<lower(s)>.pinpointhq.com` |

4. Probe order (cost decision, first match wins): `gh, ashby, lever, workable,
   smartrecruiters, recruitee, bamboohr, breezy, pinpoint, rippling, join` (`:119`).
5. Guards per candidate (`buildCandidateUrls`, `:255-308`): `SLUG_RE = /^[A-Za-z0-9._-]+$/`
   (`:67`); re-parse the built URL and require hostname EXACTLY equal to the expected host
   (`:274-283`); then ask the provider's own `detect()` (pure, no network) whether it accepts
   the URL, else `unsupportedSlugShape` (`:301-304`).
6. Validation = real fetch: `probeVendor` calls `provider.fetch(entry, ctx)`; `>=1 job` is
   `match`, 0 is `empty`, throw is `error` with `httpStatus` and `refusedRedirect` kept
   (`:569-600`). Concurrency 8 (`:71`).
7. Verdict semantics:
   - `isDefinitiveAbsence` = HTTP 404 or 410 only (`:613-615`).
   - A refused redirect is "settled, wrong slug", not absence; BambooHR answers an unknown
     tenant with `302 -> www.bamboohr.com` instead of 404 (`:760-775`, `:589-597`).
   - **SmartRecruiters answers 200 with `totalFound: 0` for ANY slug**, so its `empty` is
     ignored (no verdict either way) (`:703-716`).
   - Reason ladder for unresolved companies (`:776-812`).
8. Workday (`parseWorkdayHint`, `:330-372`): accepts a full careers URL or CXS endpoint
   (anchored regexes, `:359`, `:364`; tenant from HOST, not from the CXS path) or
   `{tenant, site, instance?}`. Segments must match `/^[A-Za-z0-9_-]+$/` (`:313`).
   Missing instance -> probe `WORKDAY_INSTANCES = wd1, wd2, wd3, wd5, wd10, wd12, wd101,
   wd103` (`:125`) with `ctx.maxPages: 1` (`:626-674`).
9. Output: preview by default; `--write` text-splices entries into `portals.yml`
   `tracked_companies` (`insertIntoTrackedCompanies`, `:495-542`) and re-parses to confirm
   the count (`splicedCorrectly`, `:554-561`). Small boards are flagged with a
   `# verify:` comment (`flagSmallBoards`, `:436-441`; `renderPortalEntry`, `:415-426`).
10. Blacklist partition before probing and after (`partitionBlacklisted`, `:837-851`).

Tests: `tests/discover-ats.test.mjs`, `tests/discover-ats-small-board.test.mjs`, plus an
inline `--self-test` (`discover-ats.mjs:933+`).

### 1.4 `scan-ats-full.mjs` (1,850 lines): reverse directory sweep

- Walks whole public directories of six ATS families (`SOURCES`, `scan-ats-full.mjs:287-362`):
  greenhouse, lever, ashby, workday, icims, bamboohr. Matches against the user's
  `title_filter` / `location_filter`; keeps only postings with a usable publish date.
- **Directory source is a third-party dataset**:
  `https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/<ats>_companies.json`
  (`:98`), tracked on `main` deliberately, cached 24 h in `data/cache/ats-companies`
  (`:91-92`). No counts are vendored in the repo; sizes are whatever the dataset holds.
- Integrity: `SLUG_RE` (`:243`) plus `entryOnHost` re-parse to the canonical host (`:257-264`).
- Workday dataset entries are `tenant|instance|site`; `WORKDAY_JUNK_TENANT_RE = /^wd\d+$/i`
  drops a corrupted block (~47% of the upstream file) (`:245-251`, `:324-337`).
- iCIMS host ambiguity: `icimsHostCandidates` tries `careers-<s>.icims.com` vs
  `<s>.icims.com` with a fallback only on a first-page 404 (`:266-282`).
- Concurrency 20 for per-tenant hosts, 6 for single-host directories (Greenhouse, Lever,
  Ashby), with a measured note that 20 against one host got throttled and silently lost
  live boards (`:100-115`). Resolver outage breaker after 50 consecutive DNS failures (`:120`).
- Cheap prefilter: if a provider has `fetchListing` (Greenhouse does), it is called first and
  the expensive `fetch()` only when the listing passes (`:1476-1477`).
- Also `--seeds yc,a16z` (VC portfolios) and `--history-seeds` (boards from the user's own
  tracker and scan history).
- Resumable checkpoint `data/cache/ats-full-checkpoint.json` (`:126`).
- Tests: `tests/scan-ats-full-*.test.mjs` (dedup key, icims hosts, listing precheck, live
  offer, outage checkpoint, resume, title filter, workday junk tenant).

### 1.5 `dead-boards.mjs` (86 lines)

- TSV `data/dead-boards.tsv` (`scan-ats-full.mjs:128`), header `ats board misses last_checked`.
- Only a real HTTP 404 advances the miss counter; any other status clears a row that has not
  reached the threshold (`dead-boards.mjs:36-53`). 200 deletes the row.
- Retire after `DEAD_BOARD_MISSES = 3`, re-check after `DEAD_BOARD_RECHECK_DAYS = 30`
  (`:9-10`, `shouldSkipDeadBoard :31-34`).
- `recordBoardFailure`: an error marked `firstPage: false` (board answered page 1, later page
  failed) is recorded as a success (`:74-76`).
- Atomic tmp-then-rename save (`:78-86`). Tests: `tests/dead-boards*.test.mjs`.

### 1.6 `history-ats-seeds.mjs` (138 lines)

- Reads the user's tracker and `data/scan-history.tsv`, takes posting URLs, maps them via
  `atsVendorOf` and normalises to a board URL (`atsBoardUrlOf`, `:22-49`): Greenhouse
  rewrites `boards.greenhouse.io` to `job-boards.greenhouse.io`, keeps first path segment;
  Lever/Ashby/SmartRecruiters first segment; Workday strips a locale segment; SuccessFactors
  cuts at `job|search|tile-search-results|services`; iCIMS/BambooHR root.
- Unknown vendors keep their URL "so they become scannable once a provider exists" (`:16-21`).
- Dedup by `vendor + careersUrl` (`:122-138`). Test: `tests/ats-vendor-history-seeds.test.mjs`.

### 1.7 `discover-new-companies.mjs` (226 lines)

- `scan-history.tsv -> companies.yml -> discover-ats.mjs -> portals.yml`
  (`discover-new-companies.mjs:18-21`). Subtracts tracked and blacklisted companies.
- Explicitly motivated by Gupy: the keyword sweep "constantly surfaces employers nobody
  configured" (`:9-11`). Defaults: `--since 30`, `--min-rows 2`, `--limit 40`. Local files only.

### 1.8 `verify-ats.mjs` is NOT about ATS detection

It scores a generated CV's ATS-friendliness (fonts, sections, layout) (`verify-ats.mjs:3-20`).
Irrelevant to board discovery.

### 1.9 Seeds

- `seeds/vc-portfolios.mjs` (445 lines): `SEED_SOURCES = { yc, a16z }` (`:436-445`). YC via
  `https://api.ycombinator.com/v0.1/companies?page=N&per_page=1000` (`:54`, `:341`); a16z by
  scraping `https://a16z.com/portfolio/` (`:60`). `toPortalEntry` (`:282-310`): explicit
  `ats`/`ats_id` hint for greenhouse/lever/ashby, else GUESSES Greenhouse by slug, else the
  company website. US startup-centric; no LATAM seed source.
- `templates/portals.example.yml` (3,473 lines): 172 uncommented `tracked_companies` entries
  by host: Ashby 49, Greenhouse 43 (+2 EU), Lever 15, Workable 5, PageUp 3, then single
  branded domains. Zero Pinpoint, zero JazzHR, zero Gupy tenants uncommented.
  - LATAM block (`portals.example.yml:3025-3081`): Nubank (Ashby), Clara (GH), dLocal (Lever),
    EBANX (GH), Yuno (Lever), Addi (Ashby), Platzi (Workable), Deuna (Lever), Cobre (GH),
    Simetrik (Ashby). The comment says the LatAm job boards (Get on Board, OCC, Gupy,
    HiringRoom) existed but no LatAm employer was tracked (`:3027-3031`).
  - Gupy board stanza commented at `:1664-1666`; OCC at `:1630`.
- No remote-first seed list as such; remote coverage comes from `job_boards` feeds.

---

## 2. Provider contract

### 2.1 Module shape (`providers/_types.js`, 229 lines; `ADDING_A_PROVIDER.md`, 837 lines)

- `Provider = { id, detect?(entry) -> {url}|null, fetch(entry, ctx) -> Job[], dedupKey?(job) }`
  (`_types.js:217-227`). Optional `fetchListing` (Greenhouse) and `enrichDate` hook
  (`ADDING_A_PROVIDER.md:162`).
- `Job` (`_types.js:15-125`): `title` (required), `url` (required, absolute, THE dedup key),
  `company`, `location`, optional `description` (only when free in the list payload or via
  opt-in bounded `fetchDetails`), `postedAt` (epoch ms), `salary {min?, max?, currency?}`
  (only real figures, never inferred), `externalId` (ATS posting id), `requisitionId`
  (employer req id; MANY-TO-ONE with postings, measured on Affirm: 181 postings, 118 req ids,
  `:74-97`), `trustScore/trustFlags/trustLevel`, `language`.
  Greenhouse additionally sets `listingIdentity { ats_provider, board_slug, posting_id }`
  (`greenhouse.mjs:263-265`).
- `PortalEntry`: `name, enabled, careers_url, api, provider, transport, max_pages, ...`
  (`_types.js:144-155`); provider-specific blocks (`gupy:`, `jazzhr:`, `smartrecruiters:`) are opaque.
- `Context`: `fetchText`, `fetchJson`, `fetchResponse`, optional `maxPages`, `sleep`,
  `dedupIncludeLanguage`, `preferredLanguageRank` (`_types.js:181-211`).

### 2.2 Eligibility gate (`ADDING_A_PROVIDER.md:15-56`)

Source Indexing Policy: employer-attributed, free to read and apply; one source per provider
(no meta-aggregators); full inventory, no paid placement; robots.txt per path. A rule naming
only `ClaudeBot`/`anthropic-ai` "doesn't name the scanner" but honouring it is "a maintainer
call" (`:43-49`). Single-company ATS adapters clear the gate by construction.

### 2.3 Pagination and health probe

- Absolute page ceiling MUST be a module constant independent of source-reported totals
  (`ADDING_A_PROVIDER.md:420-471`); reference `DEFAULT_MAX_PAGES` / `MAX_PAGES_CAP` /
  `resolveMaxPages`. Neither page count nor results count proves completeness; only the
  walk's natural end does (`:456-471`).
- `ctx.maxPages: 1` = liveness probe by `verify-portals`; skip detail fetches; the probe
  hard-caps at `PROBE_REQUEST_BUDGET` 4 requests and throws `ProbePageBudgetReached`, which
  a provider must propagate UNWRAPPED (`:473-507`).
- Pacing: `if (page > 0) await sleep(INTER_PAGE_DELAY_MS, ctx)`; retry via
  `fetchJsonWithRetry` / `fetchTextWithRetry` (`:509+`). Retry defaults
  `{ retries: 2, baseDelayMs: 500, maxDelayMs: 8000 }` (`_http.mjs:333`); retryable =
  429, 5xx, transport (`_http.mjs:412`); Retry-After honoured but clamped to 4x max
  (`_http.mjs:474-476`).
- Health check tiers (`ADDING_A_PROVIDER.md:602-623`): tier 1 direct GH/Ashby/Lever slug
  probe with suggested fixes; tier 2 everything else via `fetch(entry, {maxPages: 1})`.
  Unclaimed entries are `skipped`, not ok.

### 2.4 Trust validation (`providers/_trust-validator.mjs`, 254 lines)

Flags only, never drops (`:5-6`). Score 100 minus penalties (`:53-58`): `invalid_url` 50,
`missing_apply_url` 40, `suspicious_domain` 25 (URL shorteners: bit.ly, tinyurl, t.co,
forms.gle, goo.gl, shorturl.at, rebrand.ly, cutt.ly; `:21-30`), `company_domain_mismatch`
15, skipped for an ATS allowlist (greenhouse.io, ashbyhq.com, lever.co, workday.com,
smartrecruiters.com, jobvite.com, myworkdayjobs.com, recruitee.com, workable.com, icims.com,
taleo.net, applytojob.com, breezy.hr, jazz.co, bamboohr.com, teamtailor.com; `:33-50`).
Levels 90-100 high, 60-89 medium, else low (`_types.js:133`). Config-overridable (`:182-254`).

### 2.5 SSRF and network guards

- `_http.mjs`: redirects refused by default (`redirect = 'error'`, `_http.mjs:147`); 10 s
  default timeout, env `CAREER_OPS_HTTP_TIMEOUT_MS` (`:54-61`); a literal-IP target is
  checked with `isBlockedAddress` before dialling (`:117-119`); default UA set when absent (`:156`).
- Per provider: parse, require `https:`, host allowlist (`Set`) or anchored per-tenant
  regex BEFORE any request; re-validate the final href after adding params
  (`ADDING_A_PROVIDER.md:184-270`; e.g. `greenhouse.mjs:24-35`, `:211-213`).
- `_ip-guard.mjs` (169 lines): blocked IPv4 ranges 0/8, 10/8, 100.64/10, 127/8,
  169.254/16 (cloud metadata), 172.16/12, 192.0.0/24, 192.168/16, 198.18/15, 224/4, 240/4
  (`:65-77`); canonical-decimal octets only (octal bypass refused, `:85-91`); IPv4-mapped
  IPv6 in dotted and hex form unwrapped (`:126-139`); `::`, `::1`, fc00::/7, fe80::/10,
  ff00::/8 (`:141-144`). Scoped through `AsyncLocalStorage` `providerFetchContext` so only
  provider requests are guarded and loopback keeps working elsewhere (`:9-48`). Validated at
  LOOKUP time to defeat DNS rebinding (`:26-29`). Error code `ECAREEROPS_BLOCKED_ADDRESS`.
- `_dns-cache.mjs` (393 lines): patches callback `dns.lookup` process-wide; TTL 5 min, 512
  entries, negative TTL 30 s only for resolver-failure codes (ENOTFOUND never cached),
  in-flight coalescing, token bucket 400 lookups/min burst 20 (`:63-105`); the IP guard is
  applied at entry so cache hits are also validated (`:225-260`). Motivation: 37k lookups
  for one host in one sweep broke a Pi-hole.
- `_connection-scope.mjs` (88 lines): per-board undici `Agent` destroyed at the end of the
  unit, because per-tenant hosts (Workday, iCIMS, BambooHR) leaked ~1 idle socket per board
  and later stages failed wholesale (`:1-30`). Opt-out `CAREER_OPS_CONNECTION_SCOPE=0`.
- `_safe-url.mjs` (47 lines): `safeEncodeURIComponent` returns null on a lone UTF-16
  surrogate so one bad id drops one posting, not the page.

### 2.6 User agents (`user-agent.mjs`, 31 lines)

- `DEFAULT_USER_AGENT = 'Mozilla/5.0 (compatible; career-ops/1.0; +https://github.com/career-ops-hq/career-ops)'`
  (`:13`), pinned, not the release version.
- `BROWSER_LIKE_USER_AGENT` Windows Chrome 151 (`:22-23`) for WAF-blocked sources;
  `MACOS_BROWSER_LIKE_USER_AGENT` Chrome 125 (`:30-31`).
- Browser automation (Playwright) is allowed only as standalone `scan-<source>.mjs`
  scripts, public pages, no CAPTCHA solving or header/cookie spoofing
  (`ADDING_A_PROVIDER.md:641-649`). Precedents: `scan-interamt.mjs`, `scan-dayforce.mjs`.

---

## 3. Gupy and Greenhouse in detail

### 3.1 Gupy (`providers/gupy.mjs`, 469 lines)

- **Endpoint**: `GET https://portal.gupy.io/api/job-search/jobs` (`:80`), described as the
  endpoint portal.gupy.io's own job search calls (`:13`). Old host
  `employability-portal.gupy.io` "has answered a bare nginx 404 on every path since early
  October 2026" (`:13-15`); kept in `PLATFORM_HOSTS` for routing only (`:82-85`).
  `assertApiUrl` pins hostname to `portal.gupy.io` on every request (`:120-132`, `:408`).
- **Response**: `{ data: [ { name, jobUrl, careerPageName, city, state, workplaceType,
  type, publishedDate, description, ... } ], pagination: { total, offset, limit } }` (`:8-11`).
  `extractGupyRows` throws a descriptive error on a non-`{data: []}` envelope (`:239-246`).
- **Query**: `jobName=<keyword>&offset=<page*100>&limit=100`, optional
  `workplaceType=<csv>`, `type=<csv>`, `state=<x>` (`:399-406`). No country filter; a
  `gupy.country` key is warned and ignored (`:354-358`). Keywords from `gupy.keywords[]`,
  else `gupy.q`, else `config/profile.yml` `target_roles` (`:155-162`). Each keyword is a
  separate sweep because `jobName` terms AND together (`:147-151`).
- **Pagination**: `PER_PAGE = 100` server max (101 is a 400); offset >= 10,000 is a 400
  so `MAX_PAGES_CAP = 100` (`:86-92`, `:36-38`). Default `max_pages` 5 per keyword (`:87`).
  `pagination.total` reports the PAGE SIZE (100) at every offset, so only a short page ends
  the feed (`:41-47`, `:443`). `ctx.maxPages` is a TOTAL page budget across keywords
  (`:364-366`). 200 ms between requests (`:95`, `:409`). Truncation warning names the remedy
  (`:451-458`).
- **Recency**: newest-first across pages; early stop when a page's oldest dated posting is
  older than the cutoff minus a 2-day margin (`pageIsPastWindow`, `:209-214`; `:100`).
  `ctx.sinceMs` only stops pagination; the entry's own `since_days` also FILTERS (`:377-380`,
  `:434-437`), cutoff truncated to UTC midnight (`:192-197`).
- **Normalisation** (`normalizeGupyApiJob`, `:295-319`): title `name`; url `jobUrl` host-locked
  to HTTPS `gupy.io` or `*.gupy.io` (`isSafeGupyUrl`, `:108-117`); company `careerPageName`;
  location = pt-BR label of SINGULAR `workplaceType` (`remote` Remoto, `hybrid` Hibrido,
  `on-site` Presencial) or `isRemoteWork`, plus city, state, country (`buildGupyLocation`,
  `:258-271`); description straight from the list payload (`:315`); postedAt from
  `publishedDate` (`:316-317`). No externalId, no contract regime field mapped.
- **Confidential/closed**: drops `isConfidentialCareerPage === true` and employer labelled
  `^(empresa )?confidencial$` (portal endpoint no longer sets the flag; 4 of a 500-row sample)
  (`:105`, `:297`, `:306`). No explicit closed/expired handling: the search only returns
  live postings.
- **Dedup**: by posting URL across keywords (`seen`, `:385`, `:430`).
- **Tenant discovery**: none per tenant. `<tenant>.gupy.io` career pages are NOT claimed
  (`:58-61`). Tenants surface as `careerPageName` and flow into `discover-new-companies.mjs`.
- **Failure policy**: a failed page ends that keyword's sweep, others continue; if no sweep
  produced any page, the ORIGINAL error is rethrown (`:388-393`, `:412-422`, `:466`). While
  probing, errors propagate unwrapped (`:418`).
- **No browser impersonation**: answers the default UA with no Origin/Referer (`:49-50`).
  robots.txt `User-agent: * / Disallow:` (allow all), checked 2026-10-07 (`:33-34`).
- **Tests**: `tests/providers/gupy.test.mjs` (793 lines), ~50 named cases, among them:
  pins every request to `https://portal.gupy.io/api/job-search/jobs` (`:787`); ignores a
  `pagination.total` equal to page size (`:298`); offset/limit=100 stops on short page
  (`:275`); `ctx.maxPages` as total budget (`:325`); cap at 100 (`:377`); confidential drops
  (`:116`, `:132`); host lock incl. lookalikes (`:99`); detect claims platform hosts, never a
  tenant (`:188`); since_days vs ctx.sinceMs (`:710-751`). Fixture shape:
  `jobUrl: 'https://acme.gupy.io/job/abc123'` (`:24`). Inline fixtures, no fixture files.

### 3.2 Greenhouse (`providers/greenhouse.mjs`, 280 lines)

- **Hosts**: allowlist `boards-api.greenhouse.io`, `boards.greenhouse.io`,
  `job-boards.greenhouse.io`, `job-boards.eu.greenhouse.io` (`:16-21`); legacy hosts
  `boards[.eu].greenhouse.io` still 301 to job-boards (`:14`).
- **Slug resolution** (`resolveApiUrl`, `:38-74`): explicit `api:` (validated) wins; else
  regex `job-boards(.eu)?.greenhouse.io/<slug>`; else legacy host first path segment from the
  PARSED URL; else `?for=<slug>` on an embed URL, only on a Greenhouse host; `embed` is never a
  slug. Output `https://boards-api.greenhouse.io/v1/boards/<slug>/jobs`.
- **List + description in one request**: `?content=true` (`:209-216`); body is
  DOUBLE-encoded HTML, decoded by the shared `htmlToText` (`:153-168`), capped at
  `DESCRIPTION_CAP = 4000` chars (`providers/_html-to-text.mjs:11`, `:54`).
- **Cheap listing**: `fetchListing` drops `content` and returns title, url, postedAt
  (99 KB vs 1.67 MB measured on 5 boards) (`:183-200`).
- **Fields** (`:245-278`): title, `absolute_url` (rows without it dropped, `:218`),
  company = entry name, `listingIdentity`, `externalId = id`, `requisitionId =
  requisition_id`, location `location.name`, postedAt `first_published`.
- **Office enrichment**: when `location.name` is only a work model (Hybrid, Remote,
  Distributed, ...; `WORK_MODEL`, `:96`; `isWorkModelOnly`, `:105-109`), one extra call to
  `/v1/boards/<slug>/offices` maps job id to office names (`buildOfficeMap`, `:128-151`),
  sorted and joined with a spaced middle dot, U+00B7 (`:256`); failure degrades with a warning (`:229-241`).
- **Pagination**: none (whole board in one response).
- **Closed jobs**: not represented; disappearance from the list. Liveness per job via
  `https://boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>` (`liveness-api.mjs:110-118`);
  a 404/410 is "expired". Embedded careers pages with `?gh_jid=` resolve the board through
  `https://boards.greenhouse.io/embed/job_app?token=<id>` and the `for=` param of its
  redirect (`liveness-api.mjs:85-108`).
- **Tenant discovery**: discover-ats probe (first vendor), scan-ats-full directory dataset,
  YC seed guess, history seeds.
- **Tests**: `tests/providers/greenhouse.test.mjs` (518 lines): EU slug, embed `?for=`,
  legacy hosts, untrusted/non-HTTPS/malformed `api:` (`:24-155`); content=true and
  redirect:error (`:189`); identity mapping (`:216`); epoch-0 kept (`:295`); 4000-char cap
  (`:281`); offices enrichment and sorting (`:333-497`).

---

## 4. Pinpoint and JazzHR in full

### 4.1 Pinpoint (`providers/pinpoint.mjs`, 157 lines)

- **Endpoint**: `GET https://<slug>.pinpointhq.com/postings.json`, public, zero-auth,
  every active posting in ONE call (`pinpoint.mjs:7-11`).
- **Host guard**: `PINPOINT_HOST_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.pinpointhq\.com$/`
  (valid DNS label, no leading/trailing hyphen) (`:22`); `assertPinpointUrl` requires https
  (`:24-37`). `resolveApiUrl` uses `careers_url` only, discards path, roots at host (`:39-51`).
  No `api:` support.
- **detect/fetch**: `:57-69`; `redirect: 'error'`; uses `ctx.fetchJson` directly (no retry
  wrapper).
- **Response read**: `{ data: [ { title, url, path, location: { name, city, province, ... },
  job: { department, division, ... }, compensation, description, ... } ] }` (`:9-11`).
- **Fields kept** (`parsePinpointResponse`, `:124-157`): `title` (trimmed, required), `url`
  (any well-formed `https:` URL; display-only so not host-locked), `location` =
  `location.name` else `city, province`, `company` = entry name. **Not kept**: description,
  date, id, department, compensation, workplace type. The description is read only to detect
  demo content.
- **Demo-tenant filter**: some abandoned trial tenants answer 200 with canned postings
  ("Head of DEI - UK/Belfast"); identified by an embedded doubled YouTube URL
  `youtube.com/embed/https://www.youtube.com/embed/pFxm6fszrpw` (`:72-94`). Verified on six
  unrelated company slugs; a genuinely nonexistent slug 404s.
- **Pagination**: none. **Dates**: none. **Detail fetch**: none.
- **Tenant discovery**: only discover-ats guess `https://<lower(slug)>.pinpointhq.com`
  (`discover-ats.mjs:107`), 9th in probe order. No directory dataset, no seeds, no
  `portals.example.yml` entry uncommented.
- **Tests**: `tests/providers/pinpoint.test.mjs` (242 lines), inline fixtures: detect roots
  postings.json at host and ignores path (`:20-28`); rejects non-https, path spoofing,
  hyphen-edged labels, accepts internal hyphens (`:41-71`); drops empty-title / no-url /
  non-absolute / non-https rows (`:102`); location fallback (`:129-135`); fetch uses
  redirect:error (`:164`); throws on non-pinpoint host (`:185`); demo fingerprint drop and
  non-false-positive cases (`:208-234`). No files under `test-fixtures/` (that directory only
  holds `upgrade/` and `url-cell-parity.json`).

### 4.2 JazzHR (`providers/jazzhr.mjs`, 238 lines)

- **Endpoint**: server-rendered HTML board `GET https://<tenant>.applytojob.com/apply`
  (`boardUrl`, `:63`). Not an API. Sent with `User-Agent: BROWSER_LIKE_USER_AGENT`,
  `Accept: text/html`, `redirect: 'error'`, via `fetchTextWithRetry` (`:218`).
- **Host guard**: `HOST_RE = /^[a-z0-9][a-z0-9-]*\.applytojob\.com$/i` (`:37`). Origin rebuilt
  from hostname only; path and port of the input discarded; `api:` then `careers_url`
  (`resolveOrigin`, `:52-61`).
- **List parsing** (`parseJazzHRList`, `:123-175`): primary pass over
  `<li class="...list-group-item...">` cards: first `<a href=".../apply/<x>">` gives url and
  title, location from text after the `fa-map-marker` icon (`:136-146`); fallback pass over
  every `/apply/<x>` anchor, title only, deduped against the first pass (`:158-163`).
  Posting URLs must be same-origin with path `/apply/<non-empty>`, query stripped
  (`resolvePostingUrl`, `:113-120`). `MAX_JOBS = 1000` (`:38`). Throws if posting-shaped links
  exist but no card parses (`:170-173`), while a bare `/apply/` nav link on an empty board
  returns [] (`:165-170`).
- **Fields from list**: title, url, company = entry name, location. No date, no description,
  no id.
- **Detail fetch** (opt-in `jazzhr: { fetchDetails: true, detailLimit: N }`, default 25, max
  100; `:201-205`): GET each posting page, 200 ms apart (`:44`, `:225`), parse
  `application/ld+json` JobPosting (`parseJsonLd`, `:83-94`; `parseJazzHRDetail`,
  `:178-199`): `description` (cleaned text), `datePosted` -> postedAt, `jobLocation.address`
  (addressLocality, addressRegion, addressCountry) only when the list location is empty or
  `n/a`. Skipped while probing (`:221-222`). Detail errors are swallowed (`:231`).
- **Pagination**: none, one page.
- **Tenant discovery**: none. "There is no public directory of ApplyToJob tenants to
  enumerate" and it is never in the reverse sweep (`:7-10`); not in discover-ats VENDORS.
  Trust allowlist includes `applytojob.com` and `jazz.co`.
- **Tests**: `tests/providers/jazzhr.test.mjs` (185 lines), inline HTML fixtures: resolves
  variants to the board root and rejects bad hosts (`:26-51`); parses titles, entities,
  locations, relative/absolute links and pins to board host (`:58-60`); JSON-LD description
  and date, list location wins over sparser detail (`:69-84`); empty board returns [] (`:88-97`);
  details fetched with redirect:error and 200 ms pacing (`:116`); probe skips details (`:127`);
  SSRF guard before network (`:140`); fallback anchor pass and partial redesign (`:150-178`);
  throws on unparseable links (`:184`). No fixture files.

---

## 5. Other providers, one line each

Legend: "desc" = is a description mapped onto the Job from the list call.

| provider | endpoint | method | desc in list | browser / anti-bot |
|---|---|---|---|---|
| remotive | `GET https://remotive.com/api/remote-jobs` (`remotive.mjs:12`) | JSON, whole feed, no search | no (not mapped) | none |
| himalayas | `GET https://himalayas.app/jobs/api?limit=50` (`himalayas.mjs:11`) | JSON | no | none |
| jobicy | `GET https://jobicy.com/api/v2/remote-jobs?count=50` (`jobicy.mjs:9`) | JSON | no | none |
| workingnomads | `GET https://www.workingnomads.com/api/exposed_jobs/` (`workingnomads.mjs:10`) | JSON array | no | none |
| weworkremotely | `GET https://weworkremotely.com/remote-jobs.rss` (`weworkremotely.mjs:12`) | RSS, in-process tag extractor | no | none |
| remoteok | `GET https://remoteok.com/api` (`remoteok.mjs:13`) | JSON array, index 0 metadata, ~100 latest | no | none; notes ToS link-back |
| getonbrd | `GET https://www.getonbrd.com/api/v0/categories/{cat}/jobs?per_page=&expand[]=company&page=N` (`getonbrd.mjs:22`, `:178`) | JSON:API, category feeds (default `programming`), max_pages default 3 | no | none |
| torre | was `POST https://search.torre.co/opportunities/_search` (`torre.mjs:88`) | RETIRED 2026-10-08: endpoint answers 400 to every body; fetch throws | n/a | n/a |
| 4dayweek | `GET https://4dayweek.io/api/jobs?page=N` (`4dayweek.mjs:20`) | JSON, 25/page, `has_more`, drops `is_expired`; URL built `/job/<slug>` | no | none |
| a16z-speedrun-talent | `GET https://speedrun-talent-network.com/api/v1/jobs?page=N&source=career-ops` (`a16z-speedrun-talent.mjs:26`) | JSON, 50/page, 0-indexed, `total_pages` | no | none |
| getro | `POST https://api.getro.com/api/v2/collections/{id}/search/jobs` (`getro.mjs:64`) | JSON search; collection id from board page `__NEXT_DATA__`; needs Referer | no | BROWSER_LIKE UA for the board page |
| consider | `POST {board_origin}/api-boards/search-jobs` (`consider.mjs:7-10`) | JSON; anonymous GET first to seed session cookie + csrfToken | no | BROWSER_LIKE UA, session cookie primer |
| gem | `POST https://jobs.gem.com/api/public/graphql/batch` (`gem.mjs:24`) | GraphQL batch: list + one batched detail POST | yes (descriptionHtml via batched detail) | none |
| wttj | `GET https://www.welcometothejungle.com/api/env` then Algolia `POST https://{appId}-dsn.algolia.net/1/indexes/{INDEX}/query` (`wttj.mjs:43`, `:203-229`) | public Algolia key fetched fresh, Referer-locked; config block REQUIRED | no | Referer header |
| startup-jobs | `GET https://startup.jobs/feeds/jobs` (`startup-jobs.mjs:2-3`) | RSS, fixed ~50 newest, no pagination; role/workplace params | yes (feed description, location from its last line) | none |
| landingjobs | `GET https://landing.jobs/api/v1/jobs` (`landingjobs.mjs:17`) | JSON array, whole set; company from URL slug | no | none |
| manfred | `GET https://www.getmanfred.com/api/v2/public/offers` (`manfred.mjs:24`) | JSON full catalogue (~2.3 MB), `lang` required, 1,604 of 1,628 CLOSED filtered | no | none |
| nodesk | `GET https://nodesk.co/remote-jobs/index.xml` (`nodesk.mjs:12`) | RSS | no | none |
| echojobs | was `GET https://echojobs.io/api/jobs` (`echojobs.mjs:26`) | RETIRED 2026-08-20: Vercel bot checkpoint 429, robots disallows /api; not worked around | n/a | refused on purpose |
| agentic-jobs | `GET https://agentic-engineering-jobs.com/api/v1/jobs?page=N` (`agentic-jobs.mjs:10-12`, `:33`) | documented REST, OpenAPI | yes (HTML stripped) | none |
| generalist-world | `GET https://generalist.world/jobs/` (`generalist-world.mjs:4`) | one SSR WordPress page, card regex | teaser paragraph only | none |
| remotli | `GET https://remotli.ch/api/jobs?page=N&limit=50&remote=all` (`remotli.mjs:5`) | JSON, nested `{jobs, companies}` rows | yes | none |
| jobspresso | `GET https://jobspresso.co/?feed=job_feed` (`jobspresso.mjs:3`) | WordPress RSS | no | none |
| hackernews | Algolia HN search (`tags=story,author_whoishiring`) then items API (`hackernews.mjs:6-11`) | JSON; each top-level comment is a post, first line is title | no | none |
| builtin | builtin.com market hosts, `/jobs/<category>` HTML (`builtin.mjs:1-20`) | SSR HTML + JSON-LD ItemList; explicit `provider:` only | teaser from JSON-LD `description` | BROWSER_LIKE UA |
| themuse | `GET https://www.themuse.com/api/public/jobs?page=N` (`themuse.mjs:3`) | JSON, `page_count`, safety cap, retry | no | none |
| occ | `GET https://www.occ.com.mx/empleos/de-{kw}/[en-{state}/]` (`occ.mjs:15-17`) | SSR HTML cards, page as slug suffix; `/api/*` is 403 | no | BROWSER_LIKE UA |
| hiringroom | `https://<company>.hiringroom.com/jobs` or `/portal/jobs` (`hiringroom.mjs:4-12`) | SSR HTML cards (legacy) or JSON-LD ItemList of JobPosting (portal) | yes when present (`:183-184`) | none; `/jobs` 302 refused, point at `/portal/jobs` |
| recruitee | `GET https://<slug>.recruitee.com/api/offers/` (`recruitee.mjs:40`) | JSON per tenant; language versions | yes (title, description, requirements per language) | none |
| breezy | `GET https://<tenant>.breezy.hr/json` (`breezy.mjs:8-12`) | JSON array, published date | no | none |
| bamboohr | `GET https://<tenant>.bamboohr.com/careers/list`; JD at `/careers/<id>/detail` (`bamboohr.mjs:8-14`) | JSON list; `enrichDate` hook only for filter survivors | no (detail skipped) | none; unknown tenant 302s to www |
| personio | `https://<slug>.jobs.personio.(de|com)/` Next.js payload, `/xml` feed, `/job/<id>` (`personio.mjs:8-14`, `:195`) | SSR payload + XML feed | no | none |
| rippling | `GET https://ats.rippling.com/api/v2/board/<slug>/jobs?page=&pageSize=` (`rippling.mjs:5-6`, `:23`) | JSON, pageSize up to 1000 | no | none |
| teamtailor | `GET https://<slug>.teamtailor.com/jobs.rss` (or branded host + `/jobs.rss`) (`teamtailor.mjs:4-12`, `:67`) | RSS | no | none |
| smartrecruiters | `GET https://api.smartrecruiters.com/v1/companies/<slug>/postings?limit=&offset=&status=PUBLIC`; detail `/postings/<id>` (`smartrecruiters.mjs:103`, `:107`) | JSON, paginated | opt-in `fetchDetails` (default limit 25) | none; any slug answers 200 empty |
| workable | `GET https://apply.workable.com/api/v1/widget/accounts/<slug>?details=true`; fallback `/<slug>/jobs.md` (`workable.mjs:2-4`, `:138-139`) | JSON widget, whole account in one call | yes (+ `published_on`) | BROWSER_LIKE UA, Referer `apply.workable.com/<slug>/` |
| lever | `GET https://api.(eu.)?lever.co/v0/postings/<slug>` (`lever.mjs:51`) | JSON whole board, long timeout (42.8 MB board seen) | yes (`descriptionPlain`) | none |
| ashby | `GET https://api.ashbyhq.com/posting-api/job-board/<slug>?includeCompensation=true` (`ashby.mjs:185`) | JSON, ~10 s latency floor, longer timeout + jittered retry; salary annualised | yes (`descriptionPlain`) | none |
| comeet | `GET https://www.comeet.co/careers-api/2.0/company/<uid>/positions?token=<token>` (`comeet.mjs:4-10`) | JSON; uid and token must be supplied in `api:` (not derivable) | no | none |
| join | `GET https://join.com/companies/<slug>[?page=N]` (`join.mjs:53`) | SSR `__NEXT_DATA__` parse, page cap 50 | no | none |
| zoho-recruit | `GET https://<tenant>.zohorecruit.com/jobs/Careers` (`zoho-recruit.mjs:5-14`) | SSR page with `<input id="jobs" value="[...]">` entity-escaped JSON | yes (`Job_Description`) | none |

---

## 6. Notable differences relevant to Career Agent

- **Gupy host**: Career-Ops moved to `portal.gupy.io/api/job-search/jobs` (keyword search,
  `jobName`, limit 100, offset cap 9,900, `workplaceType`/`type`/`state` filters, no
  country filter, confidential via `careerPageName`). Its claim that
  `pagination.total` returns the page size matches our own V1.6 observation on the old host.
  Its sweep is keyword-scoped; ours was partitioned by workplace/contract/state/city with no
  keyword. Moving to this host would need its own permission read (its header records
  robots.txt allow-all on 2026-10-07) and a decision on whether a keyword-free walk is
  possible there (the code always sends `jobName`; nothing in the file tests an empty one).
- **Discovery by guessing** (derived slugs probed on 11 vendors) is the opposite of our
  "employer-published identity only" rule for Workday and Recruitee; Career-Ops itself
  excludes Workday from guessing and documents that SmartRecruiters (any slug -> 200 empty),
  BambooHR (302 for unknown) and Pinpoint (abandoned demo tenants answer 200 with fake
  postings) produce false positives under guessing.
- **Directory source** for the reverse sweep is a third-party GitHub dataset tracked on
  `main`, not an employer-published pointer.
- **Dead-board rule** (only 404 counts, 3 misses, 30-day re-check, later-page failure counts
  as alive) is a small, directly portable idea.
- **Description depth**: Pinpoint and JazzHR in Career-Ops never store a whole advert from
  the list; Pinpoint's feed does carry `description` and Career-Ops discards it.
