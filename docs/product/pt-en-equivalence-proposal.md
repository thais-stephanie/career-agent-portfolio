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

## Wider validation (2026-10-15)

A second synthetic set: 36 sentences in PT-BR and English, each with the
answer a correct reader should give, read today and with the proposal
simulated (ten word equivalences, three fixed expressions). The first
measurement above is kept as it was. Three kinds of change are kept apart:

* **Translation (T, X)**: the same concept in the other language, word by
  word or as a fixed expression. This is what the proposal adds.
* **Inflection and word order (F)**: the same language written another way.
  `phrase_forms` already does this; one Portuguese gap is not translation.
* **Semantic expansion (S)**: another concept. Must never match, and is not
  part of the proposal in any form.

| Category | Cases | Correct today | Correct with the proposal |
|---|---|---|---|
| T translation, should match | 13 | 3 | 12 |
| X fixed expression, should match | 2 | 0 | 2 |
| F inflection or order, should match | 4 | 3 | 3 |
| S another concept, must not match | 5 | 5 | 5 |
| P a word in another sense, must not match | 9 | 8 | 6 |
| N unrelated text, must not match | 3 | 3 | 3 |

What the misses say:

* **F, both readers**: "Automatizar processos" and "processos ...
  automatizados" are missed for "process automation", because the
  Portuguese verb reduces to `automatiz` and the noun to `autom`. That is an
  inflection gap inside Portuguese, a fix to the suffix rule, and not a
  translation.
* **P, the real limit**: the reader decides by words close together in one
  sentence, not by sense. English already reads "Data on staff migration to
  the new office" as data migration. The proposal carries the same limit into
  Portuguese: "Dados de migração da equipe" and "Integração do sistema de
  benefícios" (onboarding) match. The other six stay out because a word of
  the phrase is missing or sits too far away, not because the sense was
  understood. It adds no new kind of error;
  it adds Portuguese text to the existing one.
* **S**: none of the five other-concept cases matched, today or simulated
  (RPA, middleware, digital transformation, client relationship, ETL).

So the proposal recovers the translation cases measured here without
widening the concept, and its precision cost is the reader's existing
proximity limit, now in two languages. How often that limit fires on real
postings is unmeasured; a precision sample on a copy is the gate before
release.

## Does it need a new reader version

Yes. It changes which phrases are found in a posting, and so scores: a new
`READER_IDENTITY` and one full recalculation. Shipped after readers-12, it
means a second full recalculation.

## What it would cost

* A reading change: `READER_IDENTITY` moves and one recalculation re-reads
  every score (the last one took 86 minutes on the owner's corpus).
* An ADR (the reader's vocabulary is a product decision), a reviewed table,
  unit tests per entry, a precision sample on a copy before release.
* No migration; no change to any preference file.
