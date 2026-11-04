# Retrieval source expansion: validation (2026-11-03)

Phases B (implement), C (validate), D (live smoke) and E (report) for the
sources the [feasibility matrix](RETRIEVAL_SOURCE_FEASIBILITY_MATRIX.md)
marked READY. Branch `claude/retrieval-source-expansion` from `c2c4021`; not
merged, not pushed. No real database was written, no full refresh, rescore or
Deep Reading ran, no LLM was called, nothing in matching, eligibility,
ranking, profiles or P2 changed.

## 1. What was built

| Source | Change | Files |
|---|---|---|
| BambooHR | New per-tenant ATS family | `src/career_agent/providers/bamboohr.py`; registry entries (factory, field map, capabilities, kind, retrieval mode, by-company, posting and board URL recognisers) in `providers/registry.py`; careers-page recogniser in `providers/site_detect.py`; `config/sources.yaml`; catalogue row `bamboohr`; `scripts/bamboohr_tenants.py`; two tenants in `config/companies.yaml` |
| LatamCent | One board on the existing Ashby family | `config/companies.yaml` (Ashby `latamcent`) |
| Breezy, DailyRemote | Catalogue rows only (PLANNED; BLOCKED with `policy_review`) | `config/source_catalogue.yaml` |
| Torre, Built In, Hire With Near | Re-read dates in their existing rows | `config/source_catalogue.yaml` |
| Docs | BambooHR section | `docs/SOURCES.md` |
| Tests | New integration suite; fixtures; recogniser and field-map tables extended; `test_greenhouse` now uses Breezy as its example of an ATS with no adapter, because BambooHR became one | `tests/integration/test_bamboohr_collect.py`, `tests/fixtures/providers/bamboohr/board-postings.json`, `tests/unit/test_board_recognisers.py`, `tests/unit/test_field_map.py`, `tests/unit/test_greenhouse.py` |

Registered BambooHR tenants: `firstintuition` (First Intuition,
firstintuition.co.uk) and `enterpriseelectrical` (Enterprise Electrical,
enterpriseelectricalco.com), each domain taken from the employer's own advert.
`freudigmanbillings` and `rougecare` passed the board check but are not
registered: neither publishes a domain, and the registry requires one
(`test_every_active_company_has_an_identity_anchor_and_provenance`).

## 2. Offline validation (Phase C)

`tests/integration/test_bamboohr_collect.py`, 21 tests, all synthetic and
labelled fictional:

| Property | Test |
|---|---|
| Parsing list + detail into a whole advert | `test_a_tenant_list_and_its_details_...` |
| Dates: the employer's `datePosted`, RFC 3339 UTC; the list (no date) never supplies one; no discovery date | same, and `test_a_missing_date_or_place_stays_missing` |
| FULL_JD | description text asserted on every collected posting |
| Locations composed, never invented ("Remote"/"Worldwide" never filled in) | `test_a_missing_date_or_place_stays_missing`, `test_a_location_does_not_repeat_itself` |
| Work model not guessed (`isRemote` null, `locationType` unread) | first test |
| Application form never stored | first test |
| Canonical / apply URL | `https://<tenant>.bamboohr.com/careers/<id>` asserted |
| Pagination | none exists; the list is the whole board (asserted one list request) |
| Empty board, malformed item, duplicate id, changed contract | four tests |
| Unknown tenant (302 to the vendor) is an error, never an empty board | `test_an_unknown_tenant_redirecting_...` |
| Tenant identifier validated before any request | parametrised test |
| Failure isolation: 404, 500, timeout, 403 (not retried), 429 (retried with backoff) on five tenants while a sixth succeeds | `test_one_failing_tenant_never_stops_the_others_...` |
| A listed posting whose detail 404s is counted, never closed | same |
| Idempotency: a second identical run adds nothing | same |
| A posting leaving the list closes | same |
| Cross-source identity: an aggregator's apply link (with `?source=`, trailing slash or upper-case host) resolves to the stored `external_id`; foreign and look-alike URLs are refused | `test_an_aggregators_apply_link_resolves_...` |
| Reposts under another job id share the company row so the existing `(company, title)` grouping applies; the rows are never merged | `test_a_registered_tenant_joins_the_company_...` |
| Declared field-map paths resolve in the fixture | `tests/unit/test_field_map.py` |
| No two families claim one URL; the family list | `tests/unit/test_board_recognisers.py` |
| The adapter names no meaning (shape only) | `tests/unit/test_provider_neutrality.py` |

**Full regression** (unit + integration, owner product switches unset as
`tests/conftest.py` expects): see section 5.

## 3. Live smoke (Phase D)

`live_smoke.py` (scratchpad, not committed) registered the four BambooHR
tenants and the LatamCent Ashby board in a SCRATCH database under the
session's temporary folder and ran the normal `Collector` with the production
`HttpFetcher` (1 s per-host delay, 3 attempts, honest User-Agent). The real
catalogue was only read, read-only, to measure overlap.

| Measure | Result |
|---|---|
| Boards | 5 attempted, 5 succeeded, 0 failed |
| Requests / time | 28 requests in 25.3 s |
| Postings | 33 new (BambooHR 23, Ashby 10), 0 malformed, 0 detail failures |
| FULL_JD | 33 of 33 with text (BambooHR 2,556 to 8,246 characters; Ashby 2,305 to 5,370) |
| `posted_at` | 33 of 33 from the source's own date (BambooHR calendar dates back to 2023-10-16; Ashby timestamps) |
| Locations | 33 of 33 from the employer's fields; none invented. Examples: "Houston, Texas, United States"; "Remote - Headquarters are in Westport, Connecticut, United States" (the tenant's own city text); LatamCent "Brazil / Colombia [Remote] / Argentina / El Salvador" |
| URLs | apply URLs checked live: 200 for `firstintuition.bamboohr.com/careers/211`, `enterpriseelectrical.bamboohr.com/careers/45`, an Ashby `latamcent` posting |
| Second run (idempotency, live) | 0 new, 33 seen again, 0 closed |
| Tenant validation script | 4 accepted; `hireboost` and an invented name refused ("redirected to https://www.bamboohr.com") |

## 4. Coverage gain, measured

Compared with the live catalogue (281,589 jobs), by URL / external id and by
same company + same normalised title:

| Board (registered?) | Postings | Already held by URL | Same company + title already held | New |
|---|---|---|---|---|
| LatamCent, Ashby (yes) | 10 | 0 | 0 | **10** (primary country: Brazil 5, Mexico 3, Argentina 1, United States 1; secondary Argentina, Colombia, El Salvador, Mexico) |
| Enterprise Electrical, BambooHR (yes) | 6 | 0 | 2 (Workable) | **4** (Texas, on-site) |
| First Intuition, BambooHR (yes) | 4 | 0 | 4 (4 Day Week) | **0** |
| Freudigman & Billings (not registered) | 10 | 0 | 0 under that name | (10) |
| Rouge Care (not registered) | 3 | 0 | 0 | (3) |

**Registered gain: 14 new postings (10 LatamCent, 4 Enterprise Electrical) in
a corpus of 265,772 open, about 0.005%.** The six postings already held arrive
again as the employer's own record (full advert and publication date) and are
grouped with the aggregator copies by `(company, title)`; none is a second
card. LatamCent is the only gain in the owner's target region (Brazil and
LATAM roles in sales, product, engineering and finance at unnamed US clients).
Paired and HireBoost add nothing new (already covered). The other five entries
add nothing because they are blocked or have no tenant.

## 5. Full regression

Run from the worktree with the repository's environment, the owner's product
switches unset (`CAREER_AGENT_ELIGIBILITY_SOURCE`, `CAREER_AGENT_WORK_MATCH_SOURCE`),
never beside another pytest session or a live collector (no Python process
was running).

| Suite | Result |
|---|---|
| `tests/unit tests/integration`, first run | 106 failed, 17 errors: a YAML quoting error in the new `dailyremote` row broke every test that loads the catalogue, and `test_greenhouse` used `bamboohr` as its example of an unregistered ATS. Both fixed; the failing files re-run green |
| `tests/unit tests/integration`, final run | **13,140 passed, 8 skipped, 1 failed**: `test_resume_pdf_cleanup.py::test_a_real_print_leaves_no_resume_no_profile_and_no_browser` (a real Edge print left its temporary profile folder for a moment); it passed in the first run and passes alone (8 of 8). Unrelated to retrieval |
| `tests/browser` | **595 passed** |
| `ruff check src tests scripts`, `ruff format --check` | clean |
| `mypy` on the new and changed provider modules | clean |

Rendered text checked in the browser (demo database of this worktree, port
8781, stopped afterwards): Settings, Job sites lists "BambooHR", "never",
"Not checked yet", "BambooHR has not been asked for jobs yet." and the plain
reason "Complete adverts, with their publication date, from employers' own
BambooHR careers pages."; the technical reason sits in the collapsed
technical-details block, as for Pinpoint.

## 6. READY checklist

| Criterion | BambooHR | LatamCent (Ashby) |
|---|---|---|
| Source identified | yes, per-tenant ATS | yes, recruiter's own Ashby board, found from its careers page's apply links |
| Access acceptable | tenant robots allow; customer-agreement judgement recorded | Ashby family already permitted; latamcent.com terms judgement recorded |
| Normalised to the existing schema | yes (no schema change, no migration) | existing adapter |
| URLs work | 200 live | 200 live |
| Locations not invented | tested and observed | existing adapter, observed |
| Dedup tested | URL identity, company join, never merged | URL identity unchanged; no prior copies |
| Refresh limits respected | 1 + n requests per tenant at 1 s per host (12 for the two registered) | 1 request |
| Failures isolated | tested (5 failing tenants, 1 succeeding) | existing |
| Tests pass | yes (section 5) | yes |
| Limited public query confirms | yes | yes |

## 7. What stays pending (owner decisions and follow-ups)

1. **Two judgements under ADR-0018 the owner may reverse**: BambooHR's
   customer agreement versus a job seeker reading a public careers page; and
   LatamCent's website terms versus its Ashby board. Each is recorded where it
   applies (catalogue row; `config/companies.yaml` comment).
2. **Cross-source reposts with different titles** (TPAPT's "Tutor, SAT/ACT,
   Full Time" linking `freudigmanbillings/83`) are not joined by any
   deterministic rule, and ADR-0008 forbids fuzzy merging. Joining them would
   need either an aggregator collector that resolves apply links found inside
   descriptions, or a change to ADR-0008: an architecture decision, not done
   here.
3. **Aggregator rows held before the employer board** stay separate rows
   (rule 1 stops at their own id); `fold_sighted` folds only origin-pointer
   aggregators (Speedrun). Unchanged here.
4. **Board discovery now recognises BambooHR URLs.** RemoteSource-driven
   board discovery (`pipeline/board_discovery.py`) can register a BambooHR
   board from an employer-published apply link after the adapter lists it.
   That is the same evidence rule every family follows and is not
   enumeration, but it means new BambooHR boards may appear after a refresh
   without a `companies.yaml` edit. Remove `bamboohr` from `_BOARD_RECOGNISERS`
   if the owner wants the list strictly explicit.
5. **Breezy**: waiting for one employer-published tenant and readable vendor
   terms.
6. **Blocked until written permission**: DailyRemote, Built In, Hire With
   Near; Torre until its robots.txt or an official statement changes. Writing
   to them is the owner's move.
7. **Freudigman & Billings and Rouge Care** can be registered the day a
   domain is published (or the owner supplies one with evidence).

## 8. Answers

* **Which sources are ready?** BambooHR (new family, two tenants registered)
  and LatamCent (one Ashby board). Paired and HireBoost are already covered;
  Breezy is conditional; DailyRemote, Built In LA, Hire With Near and Torre
  are blocked by first-party statements.
* **What coverage gain was measured?** 14 postings the corpus did not hold
  (LatamCent 10, nine of them in Latin America; Enterprise Electrical 4), plus
  6 already-held postings now also read from the employer's own board. About
  0.005% of the open corpus; the LatamCent part is the one that matters for a
  Brazil/LATAM search.
* **What stays pending?** Section 7: two reversible owner judgements, the
  different-title repost case (an ADR-0008 question), board discovery now
  recognising BambooHR, Breezy's missing tenant and terms, the blocked
  sources' written permissions, and two validated tenants without a domain.
* **Is the branch ready?** Ready for review and a controlled integration:
  merge, then one bounded collection of the two BambooHR boards and the
  LatamCent board (13 requests) before the next full refresh. Not merged, not
  pushed, no real database touched.

## 9. Integration review (2026-11-04, before the merge)

This section was added during the controlled integration. Sections 1 to 8 are kept as written.

### Discovery now needs the owner's approval (closes section 7, item 4)

How a new board could appear, checked in the code:

| Path | Before this change | After |
|---|---|---|
| `employer_boards` (runs inside a refresh) | probes only guessable families (`ashby`, `greenhouse`, `lever`); BambooHR has `identifier_is_guessable = False`, so it was never probed | unchanged |
| Embedded-board probe | only for guessable families | unchanged |
| `discover-remotesource` (manual CLI) | **would register** a BambooHR board as soon as the adapter listed it | the board is kept as a lead: `VALIDATION_DEFERRED`, detail `PENDING_OWNER_APPROVAL: …` (the only open, not-registered outcome the lead table's CHECK allows, so no migration is needed) |

How it works:
- The adapter declares `BambooHRProvider.registration_requires_approval = True`.
- `registry.approval_required_providers()` reads that declaration, and `BoardDiscovery._validate_and_register` checks it before it would register.
- The only way to register a BambooHR tenant is now a reviewed `companies.yaml` entry, synced additively by `sync_registry`.
- `scripts/bamboohr_tenants.py` still writes nothing.

### Pagination, completeness, incremental collection

| Source | Contract | Completeness | Incremental |
|---|---|---|---|
| BambooHR | **Not paginated.** One `GET /careers/list` returns the whole board with `meta.totalCount`, then one `/careers/<id>/detail` per posting. | **New:** a list whose length differs from its own `meta.totalCount` is refused as a whole (`MALFORMED`), so the runner records a failed board and closes nothing. On 2026-11-04 the count matched: firstintuition 4/4, enterpriseelectrical 6/6. | None offered: no `updated_since`, no ETag. The details are re-read on every pass (1 + n requests, n ≤ 10). |
| Ashby (LatamCent) | **Not paginated.** The module docstring, measured earlier: `offset`, `page` and `cursor` are ignored, and the envelope has no cursor, `hasMore` or total. | The whole board in one response (10 jobs on 2026-11-04). There is no total to compare, so a silently truncated answer is guarded only by the runner's DisappearancePolicy, as for every Ashby board. | None offered. |

Behaviour inherited from the existing runner (`pipeline/collect.py`), unchanged and pinned by tests:
- A failed list closes nothing and keeps `last_collected_at`.
- A posting listed but not served (detail error) is counted (`postings_fetch_failed`), stays in the seen set and is never closed.
- A time budget or interrupt inside a board writes nothing for that board (fetch first, one transaction).
- A board's postings close only after a successful, non-suspicious pass. A large drop is held for a second pass before anything closes.

Paging scenarios (multi-page, repeated cursor, cross-page duplicates, resume from a cursor) do not apply: neither contract pages, and no artificial paging was added.

**Metrics:** recorded per board in `pipeline_run.stats.by_board`: `postings_observed`, `jobs_new`, `jobs_seen_again`, `postings_fetch_failed`, `boards_failed` and `elapsed_ms`, plus the run-level HTTP counts in `stats.http`. A scan is:
- **COMPLETE** when the board succeeded with `postings_fetch_failed = 0`;
- **PARTIAL** when it succeeded with fetch failures (those postings are kept open);
- **FAILED** when the list failed.

### Tests added

- `test_the_list_is_one_unpaginated_request_and_must_match_its_own_count`
- `test_a_truncated_list_closes_nothing`
- `test_board_discovery_keeps_a_bamboohr_board_as_a_lead_awaiting_approval`
