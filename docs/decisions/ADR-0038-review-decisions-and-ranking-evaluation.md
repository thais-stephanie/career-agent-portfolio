# ADR-0038: Review decisions are recorded with their exposure, and the list's order is measured against them

Status: accepted (owner, 2026-10-21: "P0: measurement and feedback
infrastructure only"). Adds migration 0060 (`review_decision`, profile scope).
Changes no score, reading, order, filter or eligibility.

## Context

The 2026-10-21 audits measured the list against the owner's own review of 181
GOOD/STRONG postings (17 kept, 164 rejected). Those decisions lived only in a
spreadsheet, with no reasons. The product had two places for a judgement, and
neither measures a ranking:

- `hidden_reason` (0026) says why a posting was hidden;
- `search_fit_feedback` (0046) says whether a NUMBER looked right.

No test compared any order with what the person decided, so a rule change
could make the list worse with every test green.

## Decision

1. **A review decision per posting.** KEEP, MAYBE or REJECT.
   - KEEP means "worth keeping for consideration or application". It does
     not mean strong fit, likely hire, no gaps or ideal job.
   - MAYBE is its own answer. Metrics report it in a separate perspective
     and never fold it into REJECT.
   - **Reasons.** One primary reason for a MAYBE or REJECT:
     `WRONG_PRIMARY_WORK`, `MANDATORY_EXPERIENCE_GAP`,
     `MANDATORY_TOOL_PLATFORM_GAP`, `MANDATORY_CREDENTIAL_GAP`,
     `LOCATION_OR_WORK_AUTH`, `COMPENSATION`, `SENIORITY_OR_SCOPE`,
     `WORK_MODEL_OR_SCHEDULE`, `COMPANY_OR_OPPORTUNITY_QUALITY`,
     `NOT_INTERESTED_DESPITE_FIT`, `DUPLICATE`, `STALE_OR_CLOSED`,
     `INSUFFICIENT_INFORMATION`. Any decision may also carry secondary
     reasons from the same list (a KEEP with a known gap is a stretch worth
     recording), plus a note.
   - **Where it comes from:** the posting drawer, a CSV import, or a recall
     panel.
2. **The exposure is stored beside the decision**, so the moment can be
   replayed. Each field is nullable, and NULL means not known:
   - body hash and source;
   - settings and Search Fit schema versions;
   - score, band and eligibility;
   - Deep Reading level and state (current, current for the work only, out
     of date, unread), contract and assessment version;
   - freshness;
   - and, as the screen reports it, the list's sort, the posting's rank,
     the lane and the list's query.
3. **Measurement only.** Nothing that scores, reads, ranks, retrieves, gates
   eligibility or filters may read the table. A test fails when any module
   outside a short allow-list names it.
4. **A golden set** (`data/evaluation/golden/`, private, never committed).
   - **Groups.** Postings are grouped so variants of one opportunity never
     sit in two splits:
     - a canonical vacancy identity, which does not exist yet;
     - the posting identity (`job.id`, already folded across sources), plus
       the same body (at least 200 characters) at the same company;
     - otherwise the same company and normalised title.
   - **Splits:**
     - `development`: open for analysis and tuning.
     - `regression`: a fixed set the release gate reads. It is not called a
       holdout, because it was inspected.
     - `prospective_lockbox`: starts empty and receives later decisions in
       time order. Neither CI nor the gate reads it. Opening it is logged and
       fails the gate.
5. **A ranking evaluation** (`evaluation/ranking.py`).
   - **Policies.** It reproduces the list's shipped orders (`score`,
     `deep_current`) and replays the order actually served
     (`served_history`). It simulates two offline alternatives, which are
     hypotheses and not choices: unread placed between Check first and Low
     priority, and reserved exploration slots for unread postings.
   - **Metrics:** P@k, `recall_within_labeled_surface@k` and NDCG@k; the
     KEEP rank distribution and prevalence; duplicate-adjusted variants;
     group-bootstrap intervals.
   - **Perspectives**, never mixed: strict pursuit (KEEP) and review-worthy
     (KEEP + MAYBE).
6. **A conservative release gate**, which fails on:
   - frozen labels or splits changed;
   - a group in two splits;
   - the lockbox opened;
   - too many labelled postings without a score;
   - regression NDCG@20 of a shipped order falling more than 0.15, or two
     fewer regression KEEPs in its top 20;
   - a not-eligible posting in a shipped order's top 20.

   It runs as a test where the private set exists and is skipped elsewhere.
   The harness itself is tested on synthetic postings everywhere.
7. **A recall panel** (`scripts/ranking_eval.py panel-draw` / `panel-report`):
   a blind, stratified, group-deduplicated sample of postings the review list
   never showed. The file to label carries no score, band, level, stratum or
   origin. Excerpt-only postings carry a neutral note to open the URL.

## Consequences

- A ranking change becomes falsifiable against decisions, with the
  uncertainty stated. The gate catches large regressions only: with five KEEPs
  in `regression`, nothing finer is measurable.
- Ranking the labelled postings among themselves measures order, not market
  recall. Recall needs the panel and the person's time.
- Decisions are tracking data: `reset --what tracking` clears them.
