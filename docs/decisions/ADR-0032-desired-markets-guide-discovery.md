# ADR-0032: Desired markets guide discovery; residence answers requirements

Status: accepted (owner, 2026-10-08). Amends the scheduling half of ADR-0019
("ingestion describes the market, not the candidate"), which lives with the
earlier ADRs in the private history; that ADR's rule about WHAT a source
ingests is unchanged.

## Context

Three readers used where the candidate lives, or where employers may hire her
from, to decide where to LOOK:

- the refresh plan (`sources.scheduling.plan_refresh`) paused every regional
  board outside her country's region. For a remote candidate in Brazil that
  paused Arbeitnow, the one operational European board, as "a market you have
  not asked for", although remote work on it may hire from anywhere;
- the targeted searches (`discovery.scopes.market_scopes`) asked only her
  country and the regions in `eligible_scopes`;
- Jobgether's slice order followed her country.

Meanwhile `regions.target`, the markets she says she wants, was written by
`setup` and read by nothing.

## Decision

Four questions, four homes:

| Question | Field | Read by |
|---|---|---|
| Where she lives | `eligibility.candidate_country` | the gates (requirements) |
| Where employers may hire her from | `eligible_countries`, `eligible_scopes` | the geography gate |
| How she works | `preferences.remote` | Search Fit, the work-model narrowing |
| Which markets to search first | `regions.target` | discovery only |

Rules for discovery:

1. **No markets named: search globally.** Every available board is refreshed
   (worldwide ones first). LinkedIn's targeted lane asks one worldwide scope;
   Himalayas' asks only country markets, so with none it adds no searches and
   its full walk is unchanged.
2. **Named markets get priority and exclude nothing.** Their boards are
   refreshed first, then worldwide boards, then every other board; targeted
   searches ask one scope per market, in her order; Jobgether walks their
   slices first and still walks all of them.
3. **Only an explicit choice leaves a source out.** The scheduler cannot pause
   anything. A pause she sets in Settings & Sources (stored per profile in `candidate_state`) is
   applied as before, per profile, and no market setting overrides it.
4. **Residence decides nothing here.** Preferring remote work never pauses a
   European board because she lives in Brazil.
5. **Budgets do not grow.** The query budget (`plan_queries`) bounds the plan
   however many markets are named, and no market adds a query a source cannot
   ask.

## Consequences for scores

No matcher reads `regions` (asserted over the syntax tree of `match` and
`pipeline`). So `SearchConfig.digest` leaves `regions` out, and a save whose
document changes nothing outside `regions` keeps `config_version`: choosing
markets answers "no recalculation needed". Any change elsewhere, in the same
save or not, bumps the version and the digest as before. Leaving `regions` out
of the digest changes every stored digest once; this ships in the same upgrade
whose reader change already asks for a recalculation, so it costs nothing extra.

## Costs accepted

A candidate who names no market now refreshes regional boards of every market
on "Find jobs" (for example a Brazilian board for somebody in the United
States). That is the rule she asked for; one pause in Settings & Sources
removes any board she does not want, and it is remembered.
