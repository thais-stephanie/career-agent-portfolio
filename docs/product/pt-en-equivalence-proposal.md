# PT/EN equivalence in matching: a proposal (2026-10-14)

Status: PROPOSAL. Nothing here is built. The owner's seven work phrases are
not edited to work around a limitation of the reader, and the multilingual
implementation is a separate step that needs its own ADR.

## What was measured

Synthetic phrases in the shape the setup wizard files them (one English
pattern equal to the entry's label), an English advert, and a faithful
Portuguese translation of the same advert, sentence by sentence. Read with
`match.lexicon.find_hits` on this branch. The owner's configuration was not
read. Script: kept out of the repository (scratchpad `pt_en_measure.py`).

| Phrase (wizard-shaped) | EN advert | PT translation | Why PT misses |
|---|---|---|---|
| workflow automation | found | missed | `workflow` is "fluxo de trabalho" |
| systems integration | found | missed | `system` vs `sistem` |
| AI implementation and adoption | found | missed | `ai` vs `ia`, `adopt` vs `adoc` |
| CRM implementation | found | found | every word shares a root already |
| business process improvement | found | missed | `busin` vs `negoci`, `improv` vs `melhori` |
| data migration | found | missed | `data` vs `dado` |
| API integration | found | found | every word shares a root already |
| stakeholder management | found | missed | `manag` vs `gest` |
| requirements gathering | found | missed | `requir` vs `requisit`, "levantamento" |

EN 9 of 9, PT 2 of 9. `match/phrase_forms.py` already strips suffixes in
both languages, so a phrase made entirely of cognates (`implementacao` and
`implementation` both reduce to `impl`) is found today; one non-cognate word
is enough to lose the whole phrase.

An earlier reading on a copy of the owner's data (2026-10-13, one posting
pair, her own configuration) scored the English advert 74 and its Portuguese
version 32. That is one pair, not a rate.

## What the measurement does NOT support

The report of 2026-10-13 said the larger loss "is not source coverage". No
comparison backs that: the language gap was not measured against the other
losses on the same corpus, such as the 75% of open postings whose hiring
scope is unresolved and hidden by default, the eligibility gates, or the
postings never collected. The sentence is withdrawn. Ranking the losses
needs, at least: the number of postings in Portuguese among the eligible
ones, how many of them would change band under the proposal (measured on a
copy after building it), and the same counts for the other filters.

## The proposal

**Translation is not widening, and only translation is automatic.**

1. **Word translation.** A closed table, in code, of Portuguese roots that
   are the dictionary equivalent of one English root: `sistem` = `system`,
   `ia` = `ai`, `adoc` = `adopt`, `dado` = `data`, `negoci` = `busin`,
   `melhori` = `improv`, `gest` = `manag`, `requisit` = `requir`. One word for
   one word, both directions, each entry reviewed with a reason. It lives
   beside `phrase_forms.root`, applies to wizard-shaped entries only (as the
   existing forms do), and never touches the person's stored phrase. Every
   word of the phrase must still appear in one sentence, and the quote stays
   a contiguous substring of the advert (ADR-0002).
2. **Fixed expressions.** A second, smaller table for terms that translate as
   a unit rather than word by word: `workflow` = "fluxo de trabalho",
   `requirements gathering` = "levantamento de requisitos". Same rules,
   matched as a sequence.
3. **Semantic widening is never automatic.** Related but different concepts
   ("automation" and "RPA", "integration" and "middleware", "AI adoption"
   and "digital transformation", "stakeholder management" and "client
   relationship") change what the person asked for. They can only enter as
   a phrase the person adds herself, and the interface can suggest them as
   such, labelled as a different concept.

Simulated on the same nine phrases with the eight-entry word table (no
fixed expressions): PT 7 of 9. A Portuguese advert about unrelated work
(retail service, stock, a time-clock system, supplier negotiation) fired
none of the nine. That control is small; the real check is a precision
sample on a copy of the corpus.

## What it would cost

* A reading change: `READER_IDENTITY` moves and one recalculation re-reads
  every score (the last one took 86 minutes on the owner's corpus).
* An ADR (the reader's vocabulary is a product decision), a reviewed table,
  unit tests per entry, a precision sample on a copy before release.
* No migration; no change to any preference file.
