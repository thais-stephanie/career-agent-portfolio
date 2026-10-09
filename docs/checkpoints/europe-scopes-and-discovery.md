# Europe: three scopes, wider discovery, Hire With Near (2026-10-09)

Branch `claude/europe-coverage-near`, from `main` at `567a8cd`. Decision:
ADR-0033.

## What changed

- **Gazetteer** (`config/places.yaml`): EU, EUROPE and EMEA are separate
  region ids with membership tables; 16 countries added (AL AD XK LI MD MC ME
  MK RU SM SI VA IQ JO LB BH); native and long names (`Nederland`,
  `Belgique`, `Schweiz`, `Republic of Ireland`...). Short names that are also
  United States towns (`Lebanon`, `Jordan`, `San Marino`, `Macedonia`,
  `Moscow`) are deliberately absent.
- **Resolver** (`match/places.py`): `region_contains` answers EU and EUROPE by
  membership; `regions` adds wider and member scopes; an unambiguous
  trailing code closing a single place beats a town named like a country;
  AL, MA, MT and PA are also guarded as Brazilian states.
- **Authorization** (`match/authorization.py`): "authorized to work in the
  EU" is a quoted regional requirement, always PENDING.
- **Settings and interface**: EUROPE and EU are offered as hiring scopes and
  target markets; labels in English and Portuguese.
- **Discovery**: three LinkedIn scopes; `country_scopes` with rotation for
  Himalayas (rotation = runs of `collect-himalayas` in the run ledger);
  Jobgether `emea` and `european-union` (cut) plus 20 whole European
  country slugs; slice order per scope; any European country market puts the
  European boards first.
- **Hire With Near**: forbidden by its terms, not built (catalogue
  `policy_review`).
- `READER_IDENTITY` `readers-6`.

## Measured on the real corpus (read-only, 35,049 distinct locations)

- 1,883 location groups change country. Gains are the new countries (MK
  287, IQ 293, JO 245, SI 232, ...), mostly long country lists and Workable
  `City, Region, CC`. Losses are corrections of towns read as countries:
  `Melbourne, FL, US` (was Australia), `Dublin, OH, US` (Ireland),
  `Athens, GA, US` (Greece), `Manchester, NH, US` (United Kingdom),
  `Vancouver, WA, US` (Canada).
- Stated labels: EMEA 1,156 before; after EUROPE 853, EMEA 458, EU 125.
- Known residue: `Montenegro, Rio Grande do Sul, Brasil` (one Gupy posting)
  carries Montenegro beside Brazil.

## Validation collection (temporary catalogue, synthetic profile)

A scratch database and a copy of the committed configuration with
`regions.target: [EU]` and a candidate in Portugal. Nothing touched the
owner's databases.

| Source | Limit | Result |
|---|---|---|
| Arbeitnow | 1 page | 250 postings, 111 resolved inside the EU |
| Jobgether | 8 requests | order began `european-union, europe, austria, belgium...`; 2 of 695 slices walked, 179 postings: stated EU 20, EUROPE 13, EMEA 15 |
| Himalayas | 4 searches, feed `--max-pages 1` | 4 country searches (AT, BE, BG, CY) ran without error; the default 50 backlog pages also ran (1,009 postings) |

The four Himalayas searches returned nothing because the synthetic
profile's only search term is a long work phrase; the same endpoint with
`Business Analyst` returned 177 postings for DE and 94 for AT.

Before the change, 24 Jobgether location slugs were probed with one request
each: all European ones answered 200 except `czech-republic` (400).

## Integration

1. Close the app; back up the profile, the catalogue and local state (the
   `pre-*` ZIPs).
2. Fast-forward `main` to this branch; run the full Python and browser
   suites.
3. The full rescore already pending applies `readers-6`. No migration.
4. Arbeitnow stays as the profile left it (paused for the owner).

## Pending, not in this branch

- Probe and add the remaining EU country slugs on Jobgether (slovakia,
  slovenia, estonia, latvia, lithuania, cyprus, malta).
- Workable two-part `City, CC` through the structured `countryName`.
- The Gupy `Montenegro, RS` residue.
- `EEA`, which the hiring-scope enum knows and the gazetteer does not.
