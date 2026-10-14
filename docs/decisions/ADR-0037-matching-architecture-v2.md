# ADR-0037: Matching Architecture v2: shared job facts, private profile matches, versions per component

Status: accepted as a FOUNDATION (2026-10-14). Built on
`claude/matching-architecture-v2` beside the production path, which it does
not change. Nothing in production reads or writes the new tables yet.

## Context

One stored row, `job_match`, holds everything about one posting for one
configuration: the reading of the advert (sections, signals, seniority,
pay, engagement, places, gate outcomes) and the score. Its validity is one
identity, `READER_IDENTITY` (readers-1 .. readers-12) folded with every
guarded configuration section into `input_digest`. Three costs follow:

* any change to any reader invalidates every reading: readers-12 changed
  three gate rules and a pay default, and 171,319 scores were read again
  (86 minutes on 2026-10-13);
* a second profile reads the same advert again from the beginning: the
  folding, the sections, seniority, pay and engagement are recomputed per
  configuration although none of them depends on a person;
* the vocabulary of the reader is the person's phrases: a posting in
  Portuguese and a preference written in English meet only when every word
  is a cognate (`docs/product/pt-en-equivalence-proposal.md`: 9/9 in
  English, 2/9 in a faithful translation).

`match/identity.py` already split a reading (expensive) from its arithmetic
(cheap, replayable). This ADR carries that split one step further, along
the line between the posting and the person.

## The principle

1. **Career preferences describe concepts, not words.** A person's phrase is
   a concept; the words that show it in an advert can be in any language.
2. **Linguistic normalisation may prove that two expressions are the same
   concept. It never creates a new professional preference.** Only three
   relations match automatically: EXACT_EQUIVALENT (the same word in another
   language), MORPHOLOGICAL_VARIANT (the same word inflected, proven by a
   rule) and FIXED_EXPRESSION (a term that translates as a unit).
   RELATED_CONCEPT, BROADER and NARROWER are recorded and never match.
3. **Facts about a posting are shared; the interpretation for a profile is
   private.** The shared catalogue holds what any reader of the advert would
   derive; a profile database holds only what depends on that person.
4. **A change invalidates only what depends on it.** Every derived artefact
   names the components it was made by and the inputs it read; a change to
   one component or one preference makes stale only its dependants.
5. **Context decides what a sentence proves.** A phrase in a duty is
   evidence of the work; the same phrase in "About us" or "Benefits" is not.
6. **When the evidence has more than one plausible reading, say UNCERTAIN.**
   It earns nothing and is shown. Deep Reading may read it later; local
   matching never needs it.

## Decision

`career_agent.matching_v2`, additive:

* `components`: a registry of components, each with a version, a scope
  (`job` or `profile`), the components it depends on and the raw inputs it
  reads (`job.text`, `job.metadata`, `profile.work`, `profile.eligibility`,
  ...). `affected()` closes a change over the graph; `stamp()` is the digest
  a stored artefact carries.
* `language`: language detection (EN, PT, ES by function words), a closed,
  data-driven table of relations (`language_data.yaml`), morphology rules,
  fixed expressions; it maps tokens to canonical roots only through the three
  automatic relations. No profession, product or person appears in code.
* `context`: each sentence of an advert is RESPONSIBILITY, REQUIREMENT, TOOL,
  CONDITION, COMPENSATION, LOCATION, BENEFIT, COMPANY_DESCRIPTION or OTHER,
  from multilingual heading and sentence cues kept as data.
* `features`: `JobFeatures`, the shared facts of one posting: language,
  sentences with context and canonical tokens, pay, seniority, engagement,
  location, each with the version of the component that made it.
* `profile_match`: the profile's phrases are matched against the shared
  features (no new scan of the advert); the observations feed the EXISTING
  `match_job` arithmetic, gates and score through two optional parameters,
  so A1 and B1 (ADR-0036) hold by construction and nothing is re-implemented.
* `store` + migrations 0056 (catalogue `job_features`) and 0057 (profile
  `profile_job_match`): one row per (posting, component), each with its
  stamp, so a language change does not touch pay rows.

## What is deliberately not done

* The production rescore is not switched. `job_match` stays the source of
  every screen; v2 rows are written only by tests and the benchmark. Moving
  production is a separate decision with its own recalculation.
* The gates still scan the advert per profile (blocker phrases are
  configuration). Extracting location and restriction statements into
  shared facts is the next step; the dependency is recorded in the graph.
* No conversion of the 171,319 stored rows.
* Deep Reading does not enter the percentage.

## Consequences

* A second profile costs only its own matching over shared facts.
* A preference change invalidates that profile's dependent components only;
  a language-table change invalidates work facts and work matches, never pay,
  location or engagement.
* Multi-language support becomes data plus tests, never branches.

## Addendum, 2026-10-14 (second round): toward a production candidate

Owner's decisions for this round: no global stricter proximity rule;
section headings are evidence of CONTEXT and never on their own promote a
secondary activity to a core one; v2 does not go to production this round.

* **Storage is one compact row per posting** (0056/0057 rewritten; they were
  never applied outside tests). Canonical tokens are rebuilt from `job_raw`
  (2 ms) instead of stored; evidence is a span of the canonical text; a row
  keeps its input digests and the id of a versions map, and
  `components.stale` decides per component without reading a payload.
  Measured: 28,399 -> 1,794 bytes per posting on disk for shared facts.
* **Ambiguity is read from the sentence's function words**
  (`matching_v2.structure`): an inverted head through a preposition, a
  third-party subject, a word's other sense (data), a `|`. Rules that held
  on invented sentences and failed on real adverts (colon, parenthesis,
  comma list, coordination, compound order, `to`) were measured and dropped.
  A finding makes a match UNCERTAIN, never NONE.
* **The hit's section comes from the configured headings**, exactly as in
  production; v2's 156 headings decide context only.
* **Hiring notices are CONDITION** wherever they stand, and CONDITION proves
  no work; an unknown heading closes the section above it; the role's own
  voice is never set aside as company or benefit.
* **Eligibility is split in production code, behaviour-identical**:
  `gates.read_eligibility_facts` reads what the posting states (scope
  clauses, attendance, clearance, right-to-work requirements) with the
  negation lists only; `evaluate_gates` decides with the profile's answers,
  and always reads the facts itself when none are handed in. A profile's own
  blocker and scope phrases stay its own and are still looked for per
  profile. Verified: 1,680 real postings decided identically to the stored
  readers-12 answers.
* **Shadow mode** (`matching_v2.shadow`) explains every diverging component
  and writes nothing.
* **READER_IDENTITY stays readers-12.** No readers-13: the production reader's
  output is unchanged (asserted on 1,680 stored answers). Component versions
  live in `matching_v2.components.REGISTRY`; while v2 is in shadow,
  `READER_IDENTITY` remains the identity of `job_match` and each v2
  component's version is the identity of its own artefacts. When v2 controls
  a component, a change to that component bumps its version only.

The production path is switched by a later decision, in phases (shadow,
dual read, one low-risk component, score), each with its own gate:
`docs/checkpoints/matching-architecture-v2.md`.
