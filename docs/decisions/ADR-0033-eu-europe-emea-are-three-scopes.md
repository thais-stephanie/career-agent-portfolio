# ADR-0033: EU, Europe and EMEA are three scopes

Status: accepted (owner, 2026-10-09). Extends ADR-0023 (an explicit country
list outranks a regional label) and ADR-0032 (desired markets guide
discovery).

## Context

`config/places.yaml` filed `eu`, `european union`, `europe` and `emea` under
one region, EMEA. Three readers inherited the folding:

- the geography gate: "Remote - EU" admitted a candidate in the United
  Kingdom, "Europe" one in Nigeria;
- the filters: the region facet had no Europe and no EU bucket;
- discovery: the three markets were one search scope labelled "Europe", one
  Jobgether slice (`europe`), and nothing at all on Himalayas, whose search
  can only be asked about a country.

The authorization reader dropped "authorized to work in the EU" because the
place was not a country, so the gate read no requirement.

## Decision

1. **Three region ids**: `EU` (the 27 member states), `EUROPE` (the
   continent, the United Kingdom, Switzerland, Norway, the Western Balkans,
   Ukraine, Moldova and Belarus included) and `EMEA`. Membership lives in
   `places.yaml` (`region_members`), not in code. `country_regions` stays
   one bucket per country (EMEA for every European country) so facet buckets
   keep their meaning.
2. **Partly European countries are unknown, not members**: Russia, Turkey,
   Georgia, Armenia, Azerbaijan and Kazakhstan answer None to "is it in
   Europe", which leaves the gate UNRESOLVED rather than guessing.
3. **What was written is kept.** `stated_regions` holds only the label the
   posting printed. `regions` (what the filters read) adds the wider scopes
   a stated one lies inside (EU in EUROPE in EMEA) and the scopes a resolved
   country belongs to. Never the other way: an EMEA posting is not filed
   under Europe, because it may be hiring in Cairo.
4. **A regional location is not a requirement.** The gate keeps ADR-0023's
   reading: a REMOTE posting's structured location is where the role is
   open; an ONSITE or HYBRID posting naming only a region refuses nobody. A
   requirement the DESCRIPTION states ("authorized to work in the EU") is a
   separate, quoted `work_authorization` requirement, always PENDING: a right
   to work in one member state is not one across the Union, and Career Agent
   does not answer it from a country list.
5. **Discovery asks each scope under its own words.** LinkedIn gets three
   location texts (`EMEA`, `European Union`, `Europe`); Jobgether's EU and
   EMEA markets walk the vendor's own `european-union` and `emea` filters
   first, then `europe` and the member countries. A source that can only be
   asked about countries (Himalayas) asks a named region as its member
   countries, rotated by one per run so a fixed query budget does not leave
   the same countries unasked forever. Budgets do not grow, no market pauses
   anything, and with no market named discovery stays global (ADR-0032).

## Consequences

`READER_IDENTITY` moves to `readers-6`: countries, regions, the geography
gate and the authorization gate move. Stored readings apply after a full
rescore, the one already pending since `readers-5`. Jobgether's slice
universe grows from 625 to 695 (two cut aggregates, twenty whole European
countries); its request budget is unchanged and the slice ledger still walks
fewest-walked first.
