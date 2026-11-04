# Retrieval source expansion: feasibility matrix (2026-11-03)

Every observation below was made first-party on 2026-11-03 with single,
spaced, unauthenticated requests carrying an honest User-Agent (robots.txt
first, then terms, then at most a handful of public endpoint calls). No
cookie, session, proxy, alternate identity, CAPTCHA or paid service was used,
and nothing was retried after a refusal. Third-party claims (Career-Ops
modules, a third-party Torre CLI, search-result summaries) were used only to
know where to look, never as evidence.

Final status per entry: **Implemented**, **Already covered**, **Conditional**
or **Blocked**.

## Summary

| # | Entry | Identity resolved | Status | Why, in one line |
|---|---|---|---|---|
| 1 | DailyRemote | dailyremote.com, an aggregator (Rapidr, Inc.) | **Blocked** | Terms section 12 forbid scraping, crawling and automated access (updated 2026-09-21) |
| 2 | BambooHR | per-tenant ATS family | **Implemented** | Public list + detail JSON, tenant robots allow, explicit validated tenant list |
| 3 | Breezy HR | per-tenant ATS family | **Conditional** | Contract confirmed; no employer-published tenant known; vendor terms unreadable (403) |
| 4 | Torre | torre.ai / search.torre.co | **Blocked** (already built) | robots.txt still `Disallow: /api/`; no official API documentation or terms found |
| 5 | Built In LA | the Built In network (builtin.com and city sites) | **Blocked** (already in the catalogue) | Community terms forbid bots, spiders and scrapers |
| 6 | DailyRemote / HireBoost | the identities of rows 1 and 8 | answered | DailyRemote's HireBoost post is HireBoost's general talent pool; same origin as the Jobgether/Himalayas rows; DailyRemote is blocked anyway |
| 7 | Paired | paired.so, a recruiting firm; Workable account `pairedrecruiting` | **Already covered** | Workable XML holds 64 of the 69 postings the account's own widget lists |
| 8 | HireBoost | a small LATAM recruiting agency (employer of record for client roles), not a platform | **Already covered** (aggregators) / Conditional (first-party) | Jobgether 3 + Himalayas 2 open rows; no public first-party board exists |
| 9 | Near / Hire With Near | jobs.hirewithnear.com, a staffing agency | **Blocked** (already in the catalogue) | Terms: "Do not scrape or bulk-download the Service" (re-read unchanged) |
| 10 | LatamCent | latamcent.com (WordPress WP Job Manager) applying to Ashby board `latamcent` | **Implemented** (one registry row, existing Ashby family) | The Ashby board is the origin; read through the existing adapter |

## Per source

### 1. DailyRemote: Blocked

| Field | Observation |
|---|---|
| Official URL | https://dailyremote.com/ |
| Type | Aggregator and directory ("DAILYREMOTE IS A JOB AGGREGATOR AND DIRECTORY ONLY", its terms) |
| Access mechanism | HTML pages; no RSS, feed or API (`/rss`, `/feed`, `/api`, `/remote-jobs.rss` all 404) |
| Auth | Public listings; "Premium" exclusive listings behind a paid subscription |
| robots.txt | `User-agent: *`, `Disallow: /apply/`, `Disallow: /resume-builder/` |
| Terms | `dailyremote.com/terms`, last updated 2026-09-21, section 12: "Scrape, crawl, or use automated means to access the Site without written permission." |
| Salary | Section 7: listings "enhanced using automated tools and AI to ... infer salary ranges"; displayed salary "is estimated" -- never the employer's |
| Strategy | None. Catalogue row `dailyremote` added as SEARCH_LINK / BLOCKED with a `policy_review` |
| Unblocked by | Written permission (contact@dailyremote.com; the owner's move) |

### 2. BambooHR: Implemented

| Field | Observation |
|---|---|
| Official URL | https://www.bamboohr.com/ ; a board is `https://<tenant>.bamboohr.com/careers` |
| Type | Employer ATS, one employer per tenant |
| Access mechanism | `GET /careers/list` (JSON: `meta.totalCount`, `result[]`) and `GET /careers/<id>/detail` (JSON: `result.jobOpening`), the endpoints the public careers page reads |
| Auth | None. No authenticated API is used; the BambooHR REST API (API key) is not touched |
| FULL_JD | Yes, `jobOpening.description` (HTML), detail only |
| Pagination | None: the list is every open posting (measured 3 to 10 per tenant) |
| Dates | `datePosted` (calendar date) on the detail; the list has none. Stored as `posted_at` through `to_rfc3339_utc` (midnight UTC); never the discovery date |
| Stable id | Integer per tenant; stored as `bamboohr-<tenant>-<id>` |
| Apply / canonical URL | `https://<tenant>.bamboohr.com/careers/<id>` (equals `jobOpeningShareUrl`) |
| Location | `location {city, state}` on the list; `location {city, state, postalCode, addressCountry}` on the detail; `atsLocation` (null in every sample). Composed without repeats, never "Remote" or "Worldwide" when empty |
| Remote / work model | `isRemote` read only when `true` (null on all 23 postings seen); `locationType` ("0", "1", "2") archived and NOT read: no first-party page says what the values mean |
| Employment type | `employmentStatusLabel`, the tenant's own label (e.g. "Permanent - Full Time 100%"), mapped raw to the employment-type hint |
| Salary | `compensation`: free text or null (null on every sample); no reader registered, so the adapter does not claim pay |
| Open/closed | List membership (a posting that leaves the list closes); `jobOpeningStatus` archived |
| Errors | Unknown tenant: 302 to www.bamboohr.com; careers page switched off: same 302 (HireBoost); missing posting: 404 JSON |
| robots.txt | Each tenant: `User-Agent: *`, empty `Disallow:` (firstintuition, enterpriseelectrical, freudigmanbillings, rougecare, hireboost) |
| Terms | `www.bamboohr.com/legal/terms-of-service` forbids "any robot, spider, other automated device ... to monitor or copy any content from the Service", in the CUSTOMER subscription agreement (section 4.2 binds the subscribing company). A JUDGEMENT under ADR-0018, recorded in the catalogue row: no first-party sentence addresses a job seeker reading a public careers page. The owner may reverse it |
| Cost / rate limits | Free; none documented. Cost per refresh = 1 + n requests per tenant at the fetcher's 1 s per-host delay (12 for the two registered tenants today; 27 for all four that passed the check) |
| Duplicate risk | HIGH for these employers: Enterprise Electrical also posts on Workable, First Intuition on 4 Day Week (see the validation report) |
| Maintenance | Low: two JSON endpoints, one family module |
| Tenant source | Explicit list only: names employers published as apply links on aggregators, validated by `scripts/bamboohr_tenants.py` (robots first, no redirects followed). No enumeration, no web index |
| Status | **Implemented**: `providers/bamboohr.py`, two tenants registered in `config/companies.yaml` (four passed the board check; two publish no domain) |

### 3. Breezy HR: Conditional

| Field | Observation |
|---|---|
| Official URL | https://breezy.hr/ ; a board is `https://<tenant>.breezy.hr` |
| Type | Employer ATS, per tenant |
| Access mechanism | `GET <tenant>.breezy.hr/json`: a JSON list (id, friendly_id, name, url, `published_date`, type, structured location with country id, state, city, `is_remote`, department) |
| FULL_JD | Not in `/json`; on `/p/<friendly_id>` (one request per posting) |
| robots.txt | Tenant (`breezy.breezy.hr`, the vendor's own): disallows only `/css`, `/fonts`, `/stylesheets`, `/javascripts`. Vendor host `breezy.hr`: 403 to robots.txt |
| Terms | NOT READ: the vendor host answered 403. A gap, not a permission; not retried under another identity |
| Errors | Unknown tenant: 404 HTML "Career portal not found"; the separation the task asks for (invalid tenant 404, transient 429/5xx retried, denied 401/403 terminal) is exactly what `HttpFetcher` already provides |
| Tenant source | None: no Breezy tenant in the 281,589-row corpus (only image CDN links), none on the reviewed boards, none found by search |
| Status | **Conditional**: catalogue row `breezy` (SEARCH_LINK / PLANNED) |
| Unblocked by | One employer-published `{tenant}.breezy.hr` and the vendor's terms read first-party |

### 4. Torre: Blocked (already built and refused)

| Field | Observation |
|---|---|
| Official URL | https://torre.ai/ |
| Type | Talent marketplace; opportunities are posted by organisations and by individuals; recruiter and people profiles are not jobs |
| Access mechanism | `search.torre.co/opportunities/_search` (POST), 20 results per query, no paging (`providers/torre.py`) |
| robots.txt | `torre.ai/robots.txt` re-read 2026-11-03: `User-agent: *` ... `Disallow: /api/`, plus `Disallow: /search/jobs?*` and `/*/search/jobs?*` |
| Official API / terms | None found. The only "public API" documentation found is a third-party Go CLI that says itself it is not official |
| Open/closed, eligible countries | Already modelled in the refused adapter (status, organisation or None, never "Torre" as the employer) |
| Status | **Blocked**; nothing changed. Catalogue reason now says re-read 2026-11-03 |

### 5. Built In LA: Blocked (already in the catalogue)

| Field | Observation |
|---|---|
| Official URL | https://www.builtinla.com/jobs (one of the Built In city sites; the network is builtin.com) |
| Type | Aggregator; each job page carries a schema.org JobPosting of a posting that originates on the employer's ATS; listing cards are Built In's own summaries ("Build and deliver backend payment-processing features ...") |
| robots.txt | Allows `/jobs` but disallows `/jobs*?search=`, `/jobs*?f*`, `*?page=`, `/jobs/office*`, `/apply/` and more |
| Terms | `builtin.com/community-terms-of-use` (linked from builtinla.com) re-read 2026-11-03: you may not "access, use or copy any portion of the Site or Services through the use of bots, spiders, scrapers, web crawlers, indexing agents, or other automated devices or mechanisms" |
| Overlap | Built In LA, Built In NYC, Built In and the rest are ONE source and one terms page; their postings are employers' ATS postings (Toast, Hewlett Packard Enterprise on the first page) that the ATS families reach directly when the employer is registered |
| Remote labels | "Remote Los Angeles, CA" and "In-Office or Remote Los Angeles, CA" are a work arrangement plus an office city; never Worldwide, never a hiring scope (invariant 3). Moot while blocked |
| Status | **Blocked**; reason updated with the re-read |

### 6. DailyRemote / HireBoost: the same origin?

DailyRemote's HireBoost entry (found through a web search; the DailyRemote
page itself was not fetched because the terms forbid automated access) is
"General Talent Pool", HireBoost's standing pool, which Jobgether also
republishes ("General Talent Pool", Jobgether offer `6a33664e...`). So the
origin is HireBoost itself, republished by several aggregators; DailyRemote
adds no posting that has a first-party origin we can read, and adds a location
("Italy") that contradicts the pool's LATAM description. Nothing to build.

### 7. Paired: Already covered

| Field | Observation |
|---|---|
| Official URL | https://paired.so/careers (robots.txt empty: no rules) |
| Identity | Paired, a recruiting firm placing remote talent (creative, ads, sales) for client companies; its careers page embeds the Workable account `pairedrecruiting` |
| Underlying ATS | Workable. `apply.workable.com/robots.txt`: `User-agent: *`, empty `Disallow:` (plus a `Content-Signal` line) |
| Measured | The account's public widget (`apply.workable.com/api/v1/widget/accounts/pairedrecruiting`, one request) lists 133 rows for 69 distinct postings. The corpus holds 116 open Workable rows for company `paired`; 64 of the 69 widget postings are among them (93%); 5 are not yet; 52 open corpus rows are not in today's widget (likely closed since the last full XML read, which closes them on the next complete feed) |
| Also seen | Himalayas 18, Jobgether 14, LinkedIn 1 open rows for the same company |
| Publisher / apply | Publisher is Paired (the recruiter); apply destination stays the Workable URL unchanged, as the Workable source requires |
| Countries | Postings name hiring countries per row (e.g. "RO / RS / HR / ES / CO / BR", "PH / ID") |
| Status | **Already covered** by the Workable XML family; a per-account widget adapter (not built anywhere in the product) would add at most 5 postings here and is not justified |

### 8. HireBoost: Already covered / Conditional

| Field | Observation |
|---|---|
| Identity | HireBoost: a recruiting agency (2-10 people per Jobgether) placing Latin American professionals with US companies; it posts its client roles and a general talent pool under its own name. NOT a platform; a separate G2 product with the same name is unrelated |
| First-party board | None public. `hireboost.com` is a parked domain (redirects to `/lander`); `hireboost.io` answers HTTP 402; a BambooHR tenant `hireboost` exists but its careers page is switched off (302 to the vendor); no Ashby, Workable, Breezy or Recruitee board answers |
| Measured coverage | Jobgether 3 and Himalayas 2 open rows (company `hireboost`); "Paid Media Manager" appears on both under two job ids with conflicting locations (Jobgether "Latin America"; Himalayas a "United States" restriction) |
| Paid / authenticated search | Not used (none needed, none approved) |
| Status | **Already covered** through aggregators; **Conditional** for a first-party source: the day HireBoost publishes a careers board on a supported family, it is one registry row |

### 9. Near / Hire With Near: Blocked (already in the catalogue)

| Field | Observation |
|---|---|
| Official URL | https://www.hirewithnear.com/find-a-job links to https://jobs.hirewithnear.com/ |
| Underlying system | A Next.js app (`/api/jobs/list?start=&count=`, `/api/facets`) serving data in the Bullhorn job-order shape (`publicDescription`, `employmentType`, `onSite`, `salaryLow/High`, `clientCorporation`), 225 open roles; `clientCorporation` is null on the first page (clients unnamed) |
| robots.txt | `jobs.hirewithnear.com`: `User-Agent: *`, `Allow: /` |
| Terms | `jobs.hirewithnear.com/terms` re-read 2026-11-03, "Last updated: September 2026": "Do not scrape or bulk-download the Service ..." |
| Countries | "We place talent exclusively from Mexico, Central and South America, and the Caribbean"; "serves LATAM" is never "hires in Brazil" |
| Status | **Blocked**; reason updated with the re-read. Unblocked by written permission (hello@hirewithnear.com) |

### 10. LatamCent: Implemented (existing Ashby family)

| Field | Observation |
|---|---|
| Official URL | https://latamcent.com/careers/ (robots.txt: `User-agent: *`, empty `Disallow:`) |
| Identity | LatamCent (operated by eCommerceCos per its terms), a nearshore recruiting firm; client companies unnamed |
| Board | WordPress with the WP Job Manager plugin; its public REST route `/wp-json/wp/v2/job-listings` lists 13 listings whose `_application` field is an Ashby URL on the board `latamcent` |
| Underlying ATS | Ashby `latamcent`: `api.ashbyhq.com/posting-api/job-board/latamcent` answers 10 open postings with full descriptions (2.3k to 5.4k characters), `publishedAt`, structured `address.postalAddress.addressCountry`, `secondaryLocations` and `workplaceType` |
| Origin vs listing | The 3 WordPress listings missing from Ashby are roles the ATS has closed (WordPress shows them still). The ATS is the truth, so the board read is Ashby |
| Countries | Primary location on Ashby: Brazil 5, Mexico 3, Argentina 1, United States 1 (Saint Paul, MN); secondary Argentina, Colombia, El Salvador, Mexico |
| Terms | `latamcent.com/terms-of-service` (last updated March 2025): "Any unauthorized reproduction, distribution, scraping, data harvesting, or other use of the platform's content is strictly prohibited", for "the website located at www.latamcent.com and all related services". A JUDGEMENT under ADR-0018: this collector never reads latamcent.com; it reads the Ashby posting API, the vendor's public job-distribution interface, as it does for 125 other Ashby employers whose own website terms are not consulted. Recorded in `config/companies.yaml`; the owner may reverse it by deleting that one entry |
| Publisher / apply | Company = LatamCent (the recruiter); apply URL = the Ashby posting |
| Status | **Implemented** as one `config/companies.yaml` row; no code |

## What "READY" required, and who passed

READY needed: source identified, access acceptable, normalised to the
existing schema, URLs work, locations not invented, deduplication tested,
refresh limits respected, failures isolated, tests passing and a limited
public query confirming. Only BambooHR and LatamCent pass; the evidence for
each criterion is in the validation report.
