# ADR-0034: Four facts about where a person can work, and what each decides

Status: accepted (owner, 2026-10-09). Narrows ADR-0023 for published
locations and widens ADR-0033 point 4 from region labels to every published
location of a remote role.

## Context

The profile asked "Which countries can employ you directly?"
(`eligible_countries`). The question mixed two facts: where an employer may
hire her FROM, and whether she may legally be employed there. People who do
not know the difference answered it as either, and the geography gate read
it as both. The form also repeated, under every country list, a sentence
saying nothing typed there changes a score, which was false for the
authorization lists; and a remote posting's published location
(`Remote U.S.`) refused a candidate elsewhere although the employer had
written no restriction.

## Decision

Four facts, each with one job:

| Fact | Field | Decides |
|---|---|---|
| Where she lives | `candidate_country` | requirements about residence and attendance |
| How she wants to work | `preferences.remote` | fit and the work-model narrowing |
| Markets to look at first | `regions.target` | discovery order only (ADR-0032) |
| Where she knows she can be employed | `authorized_countries` | authorization requirements; with residence, where she can be hired |

Rules:

1. **Nothing is inferred.** A country counts as one where an employer can
   hire her when she lives there AND said she can be employed there, or when
   it is in her answer to the retired question. Residence alone, nationality
   and a preferred market prove nothing.
2. **A remote role's published location never refuses by itself.** It
   admits her when it names where she can be hired; otherwise it is "Left to
   confirm", and vague prose ("we work remotely across the US, Europe,
   LatAm") cannot turn it into a pass. A restriction the employer wrote
   ("must reside in the EU", "US residents only") or a declared hiring-scope
   field still refuses, with the quote.
3. **On-site and hybrid**: a job in a country where she lives, or where she
   said she can be employed, is not refused (moving is hers to decide); any
   other country refuses as before. Relocation is not asked yet.
4. **The retired question** is no longer asked. An answer already given is
   kept as given, shown on the profile with what it still does (it only ever
   admits), and removable by her. It is never copied into
   `authorized_countries`.
5. **"I don't know"** answers a question: Home stops asking, and every job
   that needs the answer stays "Left to confirm".

## Consequences

`READER_IDENTITY` `readers-7`: the geography gate moves (remote postings
whose published location does not include her go from FAIL to UNRESOLVED;
on-site jobs in a country she is authorized in stop failing). A full rescore
applies it. Saving an authorization bumps the configuration version through
the existing mechanism and asks for a recalculation; choosing markets does
not.
