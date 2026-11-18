# Resume Tailor load, diagnosis for Stage 7 (no code changed)

Prepared on 2026-11-18 from the code at `9636ecf`, a statement trace on a small
synthetic corpus, and the earlier real measurements. Nothing of the Tailor
changed, and no tailoring, LLM call or document change was made.

## 1. What the Start view reads

`tailor_page.js` reads four things, each with its own state (loading, error,
timeout, try again) and its own time limit:

| Read | Request | Limit |
|---|---|---|
| library | `GET /api/resume/documents` | 20 s |
| saved jobs | `GET /api/jobs?saved_only=true&group_duplicates=1&limit=24&narrowings=later` | 45 s |
| best jobs | `GET /api/jobs?group_duplicates=1&limit=8&narrowings=later` | 45 s |
| career | `GET /api/career` | 20 s |

The lists are kept while `/api/jobs/freshness` is unchanged, so going back to
Start costs one small request. Since the 2026-11-10 fixes, Start draws by
part and is usable before the lists arrive: 0.13 to 0.45 s, against about
150 s before. The real lists then took 10.7 to 13.5 s (2026-11-12) and 7.7 and
10.0 s (2026-11-13, after the narrowings fix).

## 2. Where a list spends its time

`list_jobs` (`src/career_agent/web/api.py`) does, for each request:

1. `_filter_from`, which calls `self.deep_levels()`: the deep level of every
   read posting. This is the derivation that took 7 to 13.5 s of each Home miss
   on the real profile before Step 1. Home now uses `deep_levels_for_home`,
   restricted to its own population; `/api/jobs` still asks for the whole map.
   Within one process the map is remembered (the Step 1c fast path), so the
   first list after a start pays it.
2. The total and the facets over the whole filtered population, in a worker
   pool beside the page, remembered while the list's freshness holds. The
   answer waits for them (`counted.result()`). The trace shows, on a first read:
   - a version census (`COUNT(*) ... GROUP BY config_version`);
   - the total (`COUNT(*)`);
   - with `group_duplicates`, a grouped count by employer and title.

   Each reads the whole open population.
3. The page itself (8 or 24 rows), their descriptions and availability. This
   is cheap: on the synthetic corpus a second read is 0.04 s, against
   0.14-0.52 s first.

**The Tailor never reads `total`, `facets` or `grouped_away`** (none of them
appears in `tailor_page.js` or `tailor_tips.js`). It waits for counts it does
not show, as it once waited for the narrowings.

## 3. Hypotheses for Stage 7 (to be measured before any change)

- H1. A `totals=later` mode for `/api/jobs`, shaped like `narrowings=later`,
  would let the Tailor's lists answer at the cost of their page. The Jobs page
  would keep asking for the totals. Expected: most of the 7.7 to 10 s on a
  first list. It is unmeasured; the real split between counts and deep levels
  is not known.
- H2. The deep levels for a list could be derived only for the postings that
  list can return, as Step 1 did for Home. That is larger work, because a list
  can sort by the level.
- H3. The two lists run at once, and each pays the counts for its own filter.
  The saved list's population is small, but its total still runs the same plan.

## 4. Next step

First measure, read only, the real split of one first list: `deep_levels`,
counts, page. Then choose between H1 and H2.

Estimate:

- measurement: 1 to 2 hours;
- H1 implementation with tests and review: 4 to 6 hours;
- H2: 8 to 12 hours.
