# Matching Architecture v2: foundation (2026-10-14)

Branch `claude/matching-architecture-v2`, from `main` at `81328d4` (the
readers-12 integration). Not merged. Decision record: ADR-0037.

## What exists

`src/career_agent/matching_v2/`, beside the production path:

| Module | What it is |
|---|---|
| `components.py` | 14 components (8 shared, 6 per profile), each with a version, the components it depends on and the raw inputs it reads; `affected()` closes a change over the graph; `stamp()` is an artefact's identity |
| `language.py` + `language_data.yaml` | language detection by function words; canonical roots through EXACT_EQUIVALENT, MORPHOLOGICAL_VARIANT (rules proven by their examples) and FIXED_EXPRESSION only; RELATED_CONCEPT, BROADER, NARROWER recorded and never used; a listed form is looked up before its root, and a root two concepts share is never mapped |
| `context.py` + `context_data.yaml` | each sentence is RESPONSIBILITY, REQUIREMENT, TOOL, CONDITION, COMPENSATION, LOCATION, BENEFIT, COMPANY_DESCRIPTION or OTHER, from multilingual headings (EN, PT, ES) or its own cue |
| `features.py` | `JobFeatures`: one payload per shared component (parser sections, language, sentence contexts, canonical tokens per sentence, pay, location, seniority, engagement), each stamped |
| `profile_match.py` | a profile's phrases matched against shared tokens in context: MATCH, UNCERTAIN (words apart in a sentence of no known purpose), EXCLUDED_CONTEXT (About us, Benefits); the observations go into the EXISTING `match_job` |
| `store.py` | `job_features` (catalogue, migration 0056) and `profile_job_match` (profile, migration 0057): one row per (posting, component), stale components recomputed, the rest reused, both counted |
| `invalidation.py` | for a language-table change: the stored roots it can move, the postings whose stored facts contain one, and re-stamping the rest without reading them |
| `evaluation.py` | reads the labelled corpus through the matcher |

`match/engine.py` gained `SharedReading`, `read_shared()` and two optional
parameters on `match_job` (`shared`, `observed`). Called without them it is
the reading it always was; the full suite proves it.

## Shared facts vs profile data

Shared (catalogue, the same for everybody): parser sections, language,
sentence contexts, canonical tokens, pay as stated, location as stated,
seniority, engagement as stated. Private (profile database): work and tools
matches with their evidence, eligibility (status and gates), compensation,
seniority match, the score. A profile database holds no shared fact; the
catalogue holds no profile row (tested).

## What is still a prototype

* **Production does not use it.** `job_match` stays the source of every
  screen. No dual-write: switching is its own decision and its own
  recalculation.
* **The gates still read the advert per profile**: blocker phrases and scope
  clauses are configuration. Moving location and restriction statements into
  shared facts is the next step; the graph records the dependency
  (`eligibility` depends on `text_parser`).
* **Three text-only readings are still done per profile inside `match_job`**
  (explicit facts, U.S.-company evidence, the period default): about 0.25 ms
  per profile; memoising or sharing them is a small next step.
* **Selective invalidation** scans stored payloads; an index of roots per
  posting comes when a real table change shows the scan matters.
* **v2 normalises every multi-word lexicon entry**, curated ones included;
  production reads curated multi-pattern entries literally. A one-word entry
  matches its own word or a listed translation only, as production does.

## Evidence

PT/EN first case (synthetic corpus, 117 sentences, 12 concepts in 9 fields,
EN/PT plus 11 ES; `tests/fixtures/matching_v2/language_corpus.yaml`):

| Category | Cases | v2 correct |
|---|---|---|
| Translation of the same concept | 31 | 31 |
| Fixed expression | 14 | 14 |
| Inflection / word order | 25 | 25 |
| Related, broader, narrower concept (must not match) | 21 | 21 |
| Unrelated text (must not match) | 13 | 13 |
| The concept in About us / Benefits (must not match) | 6 | 6 |
| The words in another sense (should not match) | 7 | 1 |

The other-sense limit is the reader's proximity rule, in English as in
Portuguese (`Data on staff migration ...`). A stricter rule (words apart are
UNCERTAIN in any context) was measured: other-sense 5 of 7, but inflections
14 of 25 and translations 26 of 31. Not adopted; it is a product decision.
Earlier measurements kept as they were: 9/9 English vs 2/9 faithful
translation (today's reader), 7/9 simulated (2026-10-13).

Multi-profile (`tests/integration/test_matching_v2_multiprofile.py`): four
wizard-built profiles (automation, nursing written in Portuguese, teaching,
sales) over four postings in two languages. Shared facts read once per
posting; each profile's own posting is its best answer; no evidence crosses
profiles; a salary change makes stale only compensation and score of that
profile; a work-phrase change only work, tools and score; new text only that
posting; a language version only language and work facts and the work
matches; a location change only location, pay and engagement.

Selective invalidation (`tests/integration/test_matching_v2_selective_invalidation.py`):
two entries added to the table re-read 6 of 120 postings; every posting
called unaffected recomputes identically.

Parity on real postings (`scripts/matching_v2_parity.py`, 300 open postings
copied read-only into a scratch file, the owner's configuration, local, no
model): eligibility identical 300/300; score identical 270/300. The other 30
are the design working, each explained: a phrase only in About us / Benefits
no longer counts (3), words apart in a sentence of no known purpose are
UNCERTAIN (9), a sentence under a heading v2 recognises (`What you'll do`,
`Responsabilidades` ...) counts as a duty and B1 completes the work (14), a
phrase found through a listed form (5). The first two runs found three real
defects, fixed before this one: a one-word product reduced to a common root
(`workato` -> `work`, 257 of 300), translation by a root that two languages
share for different meanings (`cliente` / `client`), and v2 not knowing the
headings the current reader already used.

Moving production to v2 is therefore a scoring change in about 10% of
postings, all of it in the direction of the principles above; it is the
owner's decision, with its own recalculation.

## Performance (`scripts/matching_v2_benchmark.py`)

Ten wizard-built profiles in different fields; ms per posting.

| Profiles | Current reader | v2 (facts once + each profile) |
|---|---|---|
| 1 | 9.6 | 17.8 |
| 2 | 23.9 | 22.6 |
| 10 | 109.8 | 63.7 |

On 300 real postings (bounded read-only copy). Shared facts cost 12.9 ms per
posting, once; each further profile about 5 ms where the current reader pays
about 11. Synthetic set: 1.9 / 3.7 / 25.1 vs 4.5 / 6.1 / 17.8.

Storage is the measured weak point: about 27 KB of JSON per posting on real
adverts (4.4 GB extrapolated to 171,319), almost all of it the per-token
lists of `work_facts`. A compact encoding (roots per sentence as one string,
offsets recomputed on demand) is the next step before any production fill.
Filling takes 14 ms per posting (about 40 minutes for the corpus); a pass
over current rows reads only stamps (1.5 ms per posting).

What each change makes stale, 10 profiles x 300 postings:

| Change | Current: adverts re-read | v2: adverts re-read / rows rewritten |
|---|---|---|
| a reader or language rule | 3,000 | 300 / 9,600 (fewer with `invalidation.affected_jobs`) |
| one profile's salary | 0 (replay rewrites 300 scores) | 0 / 600 |
| one profile's work phrases | 300 | 0 / 900 |
| one posting's text | 10 | 1 / 68 |
