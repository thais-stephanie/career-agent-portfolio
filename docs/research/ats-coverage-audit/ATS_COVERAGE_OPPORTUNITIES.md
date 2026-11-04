# ATS coverage opportunities, ranked

Ranked by verifiable gain per effort, with risk as the tie-breaker. Evidence is in
`ATS_COVERAGE_AUDIT.md`, and the section is named on each line. "Approval"
means the change touches closing semantics, the catalogue's content policy or a
connector, which the brief reserves for the owner.

| # | Category | Improvement | Evidence (measured unless marked) | Rows affected | Risk | Effort | Approval |
|---|---|---|---|---|---|---|---|
| **O2** | DISCOVERY_GAP | Owner reviews the measured unregistered boards in `ATS_BOARD_DISCOVERY_GAPS.csv` and registers the chosen ones in `companies.yaml`. Then `sync_registry` (additive), a limited `collect --board ... --no-score` and `rescore --board ...`, the procedure used for BambooHR on 2026-11-04 | 934 unique missing postings on 13 sampled boards, all with FULL_JD; 0 BR_EXPLICIT, 40 BR_POTENTIAL | 934 measured; 2,600 to 7,800 estimated over 154 boards | low (existing path, additive, closing rules unchanged) | hours, no code | yes: board activation |
| **O1** | DATA_QUALITY_GAP | Close Workable rows that miss **N consecutive complete** feed reads (N ≥ 2, with a hold like `DisappearancePolicy` on a sharp drop). Alternative: a per-account widget check for rows the feed stopped listing | ≥ 31,922 open rows not in the latest complete feed (inferred); 10 of 67 stale in the widget sample | ~32,000 (inferred) | medium: closing semantics, and the feed is not each account's board | ~1 day + tests + one rehearsal on a DB copy | yes: closing semantics |
| **O5** | DISCOVERY_GAP | Ashby board recogniser accepts percent-encoded names | 25 aggregator rows; 58 missing postings on the 2 boards | 58 measured | low | **done in this branch** | no |
| **O7** | DISCOVERY_GAP | Give `BoardDiscovery` a second index source: speedrun origin URLs (Ashby, Greenhouse, Lever). Leads are recorded with evidence and validated, and BambooHR stays approval-gated | 1,170 of 1,228 unregistered-board references come from speedrun; `BoardDiscovery` walks only `remotesource` | same pool as O2, made repeatable | low to medium (registers boards; keep it a manual CLI) | ~0.5 day | yes: automatic registration |
| **O3** | DISCOVERY_GAP + latent defect | Lever EU region: read `api.eu.lever.co` when the board was published on `jobs.eu.lever.co`, and until then refuse to register EU boards against the global API | `lovehoneygroup`: 404 global, 39 on EU | 39 measured (1 board) | low | ~0.5 day | yes: connector change |
| **O4** | DUPLICATION_GAP | Decide whether `lever:jobgether` (an aggregator re-posting under the Lever family) stays registered, given that Jobgether is collected directly | full turnover between passes; 3,424 of 8,341 open rows share an exact title with Jobgether rows (indicator, not identity) | up to 8,341 rows | low to retire (closes nothing), but content policy | minutes | yes |
| **O6** | observability | Greenhouse: compare `meta.total` with the rows, as BambooHR does, and refuse a mismatch | 10 of 10 boards matched; no defect | 0 today | very low | ~1 hour | no, but no defect is shown, so do it only alongside other work |
| **O8** | INSUFFICIENT_EVIDENCE | Date semantics: is Lever `createdAt` the publication date? | 17 of 1,926 postings dated before the last pass and never seen | 17 | none | needs history (e.g. keep `first_seen_at` against `posted_at` for a month) | no |
| **O9** | refresh cadence | ~3.2 % of a board's postings appear per day | 61 of 1,926 one day after the pass | 3 % per day | none | configuration | owner preference |

## Recommendation

**Next action with the best verifiable gain per effort: O2**, limited to the
measured boards the owner accepts. It needs no code, the measurement is already
in the CSV, and it uses the integration procedure validated on 2026-11-04.

Set expectations for a candidate in Brazil before choosing. The sample found
**no posting explicitly open to Brazil** among the 934 missing ones. The
measured P1 rows (Kalshi, Rillet, Wonderschool, Shield AI) are picked by the
size of the gap and by any BR_POTENTIAL posting, not by profile fit.

**O1 matters more for the truth of the catalogue** (about 32,000 Workable rows
that probably stopped existing are still presented as open), but it changes
closing semantics and needs an approved design and a rehearsal. Next in line
after O2.
