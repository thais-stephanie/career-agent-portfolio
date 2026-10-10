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

## Addendum (2026-10-09): where the work is

"Not authorized in the US" read as a filter on American employers would hide
remote work done from Brazil for a US company, which needs no US
authorization. Three facts stay apart: the company's country (never read),
the country where the work is done (the board's workplace field plus the
location), and what the posting explicitly requires.

* A requirement that names no country ("must be legally authorized to
  work", "we do not sponsor visas") takes the posting's country only when
  the board says the role is ON SITE or HYBRID. On a remote role, or one
  whose work model is not stated, the location may be the office; the
  requirement stays to confirm.
* "We do not sponsor visas" refuses only on-site or hybrid work in that
  country. On a remote role it is to confirm, because the sentence alone
  does not say where the work must be done.
* An EXPLICIT restriction still refuses on a remote role: "authorized to
  work in the United States" or "US citizenship required" for whoever
  declared she is not authorized there, and a written territory or
  residence limit ("must reside in the United States", "open to US
  residents only", a declared scope field `USA Only`) for whoever does not
  live there. Only a published location that may be an office ("San
  Francisco, CA", "Remote U.S.") is to confirm on its own. Two forms were
  added so this holds without configured blocker phrases: `US residents
  only` in prose, and a trailing `Only` on a declared scope field.
* The field labels say so: "Countries where you know you are not currently
  authorized to work" and "Countries where you would need visa sponsorship
  to work on site". Neither help text claims remote work needs no visa or
  authorization. Both stay optional; an empty answer leaves every such
  requirement pending.

`READER_IDENTITY` `readers-8`; the work_authorization and geography gates
move on the next full rescore.

## Addendum (2026-10-10): the main list is work she can do from where she lives

The owner asked for a list she does not have to wade through. Three answers
stay apart, as before; what moves is what the list shows by default and what
counts as a confirmed territory.

* **Compatible, confirmed**: her country; a region that contains it
  (LATAM, Latin America, South America, the Americas); explicit worldwide
  hiring; any scope that includes where she lives. On the main list.
* **Explicitly incompatible**: a written restriction that leaves her out.
  Refused, with the sentence that says so.
* **To confirm**: everything else, including a remote role published at an
  office elsewhere ("London, UK (Remote)", "Remote U.S."). Hidden by
  default, never refused; one switch shows it ("Include jobs whose location
  is still to confirm"). The browser remembers that switch under a new key
  (`careerAgent.includeUnresolved.v2`), so a choice stored while "shown" was
  the default does not carry over.

Two readings change:

* An EXPLICIT hiring statement written by the employer ("candidates must be
  based in Latin America", "open to candidates based in the United States,
  Argentina, and Brazil") admits her even when the published location is an
  office elsewhere: where a board files a role and where an employer hires
  are two facts that do not conflict. Prose that only describes a team, a
  declared scope field and an aggregator's sighting ("Worldwide" on a
  re-post) are still withdrawn by a published location that leaves her out.
  "The LATAM region" now resolves as LATAM.
* "We do not sponsor visas", no country named, on a REMOTE role, is answered
  for a candidate who declared she is authorized where she lives: no visa is
  needed to do that work from home. The status still waits on the geography
  gate.

A posting re-published once per territory is ONE card. The copy she can take
stands for the group (confirmed, then likely, then to confirm, then ruled
out), and only then the score; each copy keeps its own status and reasons.

`READER_IDENTITY` `readers-9`, `MATCH_SCHEMA_VERSION` 13 (with the scoring
changes in `docs/checkpoints/brazil-relevance-2026-10-10.md`); a full
recalculation applies them.
