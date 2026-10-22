# ADR-0039: The list is ordered by the deep reading, still-listed postings stay in view, and unreadable postings are leads

Status: accepted for review (owner, 2026-10-22: "implement P1 ... do not merge
or push without showing me first"). No migration. Changes no Search Fit rule,
no intent, no `UNRESOLVED` handling and no deep reading contract
(`deep-1+289dd2d70ddc`, `assess-9` unchanged).

## Context

The 2026-10-21 audit, the P0 measurement (ADR-0038), the E2 recall panel and
the X1 blind evaluation (`out/architecture-audit-20261021/`) found:

- **Search Fit is not an attention order.** On the owner's development
  labels, the default order had NDCG@20 0.06. The stored deep readings,
  ordered as `sort=deep` already ordered them, had 0.67.
- **Old postings that are still listed were hidden.** All 14 GOOD/STRONG
  postings published before the 120-day cutoff but still listed by their
  source were drawn (E2 S2, a census). The owner kept 6 of them. The age rule
  read only `posted_at`, so not one of the 6 was on any screen.
- **The deep reading covered too little.** Only 77 of the 184 GOOD/STRONG
  readable postings had a reading. The job planned a sample before the pool.
- **The relation and the level were merged.** "Your work as a secondary
  part" and "your main work" with the same level looked the same.
- **Unreadable postings were scored as weak.** 3,970 Jobgether postings and
  4 Gupy postings have no description. Search Fit gave them low numbers that
  read as a judgement. Two of them were owner KEEPs.

## Decision

1. **Still listed counts as fresh.** A posting is inside the age window when
   any of these holds:
   - it was published within `max_age_days`;
   - it is dated, not closed, and its source listed it within
     `RECENTLY_SEEN_DAYS` (7);
   - it is undated, and the person asked to see undated postings
     (`include_undated`, unchanged);
   - the person brought it in, or tracks it (unchanged).

   Other rules:
   - `posted_at` is never rewritten. Publication age and observed freshness
     stay two facts, and the card says "still listed (seen {date})" beside
     the old date.
   - A confirmed closure always wins: `closed_at`, or a `REMOVED`
     availability, keeps the posting out.
   - The deep reading's population uses the same rule (`deep_run._elect`).
2. **The candidate pool is read first.** The pool is what the main list
   shows, in band GOOD or STRONG, with eligibility `VERIFIED_ELIGIBLE` or
   `LIKELY_ELIGIBLE`, a work model the person did not exclude, and a readable
   text (at least 200 characters, the reading's own floor), one posting per
   role. The deep job plans it this way:
   - the unread pool first: STRONG, then GOOD, highest score first;
   - the existing strata (top local, lexical, control) share only what is
     left. MODERATE and WEAK postings never enter as "pool". They enter only
     as the sample they always were.
   - the panel's default count covers the unread pool.

   The cache, the content identity and the reuse rules (ADR-0035) are
   untouched, so a posting already read is not read again.
3. **The list is ordered by the reading.** `sort=deep` is the default for
   the Jobs list and for the sections of the Home and digest pages. The
   command-line digest keeps its score order. The order is:
   - current readings by level;
   - then readings that are out of date;
   - then postings not read yet;
   - Search Fit breaks ties only.

   Three relation-tier orders were compared offline on development, under a
   rule fixed before running:
   - A: unread in the middle;
   - B: unread below neighbour;
   - C: `deep_current`, the order that ships.

   The rule: A or B replaces C only with an NDCG@20 gain of at least 0.05
   and a paired group-bootstrap 95 % lower bound above 0. Neither met it:
   both gained +0.045, with an interval of [0.00, 0.10]. **C stays.**
   - No relation hides or excludes a posting.
   - `unclear` is never a rejection.
   - A posting not read is shown as not read, never as Low.
4. **The relation is shown apart from the level.** The list payload carries
   `deep.relation` (central, secondary, neighbour, unrelated, unclear). The
   card's deep label leads with it: "Your work as a secondary part ·
   Apply, preparing". An unknown code shows the level alone. Neither
   `deep_assess` nor the level changes.
5. **Unreadable postings are leads, not low fits.** A posting is a lead when
   both hold:
   - its source gave fewer than 200 characters of description;
   - the person did not bring it in.

   How leads are shown:
   - The list has three lanes: `shortlist` (the default), `leads` and `all`.
     The lane is the screen's choice. The list API and the Home sections ask
     for `shortlist`. `JobFilter` defaults to `all`, so the command line,
     exports and every other reader are unchanged.
   - In the leads lane, the count line reads "to check yourself, fit not
     assessed", never "match your search".
   - A posting the person saved or tracks stays in her own views, by the
     same `exempt_tracked` rule as every narrowing.
   - A lead shows its title, company, location, source, date and link, plus
     "Fit not assessed: no description text". It shows no level and no score
     band as a verdict.
   - Leads are ordered by the search plan's own terms (role anchors, aliases
     and work phrases, `discovery.plan.query_terms`), through the existing
     soft order. They sort and never filter.
   - Nothing fetches more text. Jobgether's terms forbid it.

## Consequences

- **The default list holds readable postings only.** On the owner's data it
  went from 6,854 to 3,363 postings, plus 4,077 leads. The 7,440 total is
  larger because still-listed postings came back.
- **The 6 still-listed KEEPs are on screen again.** They rank 165 to 267 while
  unread. With the X1 readings shown as stored, they rank 2, 14, 15, 16, 29
  and 94. The order is only as good as its coverage, and 107 pool postings
  wait for a deep reading job that the owner starts.
- **Until that job runs, an unread STRONG posting sorts below a Low
  reading.** A and B, which would put the unread higher, did not earn the
  change.
- **Leads are a queue to check, not a ranking.** The two leads the owner kept
  sit at 1,461 and 3,285 of 4,077.
- **Left for P2:**
  - mandatory requirements the reading leaves unknown;
  - how leads are ordered;
  - a relation-aware tier once coverage is complete;
  - readable MAYBEs the reading calls neighbour.
