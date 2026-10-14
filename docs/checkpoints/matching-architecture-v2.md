# Matching Architecture v2: from foundation to a shadow candidate (2026-10-14)

Branch `claude/matching-architecture-v2`, from `main` at `81328d4` (the
readers-12 integration). Not merged. Decision record: ADR-0037 and its
addendum. Production is unchanged: readers-12, `job_match`, every screen.

Every number below was measured on a bounded, read-only copy of the real
catalogue (`scripts/matching_v2_sample.py`: 1,680 open postings, stratified
by language and stored score, plus the 181 Mattermost and ElevenLabs
postings), split by posting id into a DEVELOPMENT half (rules were tuned on
it) and an EVALUATION half (labelled once, blind). No collection, no model,
no real recalculation; the scratch copies were deleted.

## 1. Storage

| | prototype (91a5062) | now |
|---|---|---|
| shared facts on disk, per posting | 28,399 B | 1,794 B (+41 B key, +24 B versions index) |
| projected for 171,319 postings | 4.53 GiB | 0.30 GiB |
| one profile's rows, per posting | 2,280 B | 1,092 B |
| projected per profile | 0.36 GiB | 0.17 GiB |
| read back, nothing stale | 0.68 ms | 0.18 ms |

Where the 26 KB went: the per-token lists of `work_facts` (23.7 KB: relation
names 5.3 KB, words 4.1 KB, roots 3.6 KB, offsets 3.6 KB, JSON punctuation
2.9 KB), sentence contexts as JSON (1.2 KB), nine rows each repeating a
content hash, a stamp and a key.

What changed: tokens are rebuilt from `job_raw` (2 ms) and never stored;
sentence contexts are one short string (`R0.85 Q2.40`: context letter, gap,
length); evidence is a span of the canonical text; one row per posting with
its input digests and a versions-map id; readings `match_job` needs
(seniority, experience, engagement, domestic context, the U.S.-band
sentence, schedule facts, eligibility facts) are stored compactly so a
profile recompute does not re-read them (`shared_from_facts`).

## 2. Ambiguity: words in another sense

Six of seven corpus sentences using a phrase's words in another sense
matched under proximity alone. Causes, case by case: a preposition making
the other word the head (`Dados de migracao`, `Data on staff migration`,
`dados ... para analise`), a coordination (`Gestao de pessoas e campanha`),
a third-party subject (`Our suppliers handle ... negotiation`), a word's
other sense (`integracao` beside the welcome week).

`matching_v2.structure` reads the sentence's function words (closed lists
in `language_data.yaml`). On REAL adverts (development half, read by hand)
only some of the readings held. Kept: inverted head through a preposition,
third-party subject, other sense (a data table of words with a second
common meaning and the words that cue it), `|`. Measured and dropped
because real adverts use the same shapes for the work far more often:
colon, parenthesis, comma list, coordination closed by a complement,
separate noun phrases, English compound order, `to` as a preposition. A
finding makes the match UNCERTAIN: shown, worth nothing.

| corpus | legitimate found | other sense avoided |
|---|---|---|
| language corpus (117, tuned) | T 31/31, X 14/14, F 25/25 | 7/7 (production 5/7) |
| adversarial development (99, tuned) | 49/49 | 28/34 |
| adversarial HOLDOUT (70, read once, sha 4b2b1739...) | 40/41 (production 17/41) | 19/22 (production 13/22) |

Holdout precision 0.930, recall 0.976 (production 0.586 / 0.415).

## 3. Section context

156 headings in nine contexts, EN and PT (ES demonstrative), read with
Markdown, HTML, numbering, bullets and inline (`Responsibilities: ...`); a
bullet that only starts with a heading word stays a sentence. From the real
adverts: an unknown heading-shaped line closes the section above it; the
role's own voice (`You will`, `We are looking for`, `Voce vai`, `Buscamos`)
is never set aside as company or benefit; equal-opportunity, privacy,
accommodation and scam notices are CONDITION wherever they stand, and
CONDITION (with BENEFIT and COMPANY_DESCRIPTION) proves no work.

The SECTION that promotes a hit to a core duty is the configured one, as in
production; v2's headings decide context only (owner's decision).

## 4. Eligibility: shared facts, private decision

`gates.read_eligibility_facts` reads what a posting states (scope clauses
as `ScopeClause`, office attendance, clearance or export control,
right-to-work requirements: authorization, citizenship, no sponsorship) with
the negation lists only. `evaluate_gates` decides with the profile's answers
(residence, authorized countries, sponsorship needed, contractor answers,
selected scopes). A person's own blocker and scope PHRASES are hers: they
are still looked for per profile, and never written into the catalogue.

Production and v2 run the same code: `evaluate_gates` reads the facts itself
when none are handed in. Proven: the existing gate suites pass unchanged;
1,680 real postings decided identically to the stored readers-12 answers
(eligibility, every gate, score); 13 invented situations x 7 profiles in
four countries (`tests/integration/test_matching_v2_eligibility.py`:
silence, worldwide, LATAM, explicit list, exclusion, residence,
citizenship, sponsorship on site, clearance, Mattermost-like,
ElevenLabs-like, location conflict, office) decide from shared facts exactly
as from a private reading.

## 5. Shadow mode

`matching_v2.shadow.compare` runs production and v2 on one posting and
explains each diverging component: the signal, LOST or GAINED, and why (the
UNCERTAIN cause, the excluded context, the relation of a translation), with
the evidence span. It writes nothing. `scripts/matching_v2_shadow.py` runs
it over a copy and writes a JSON report.

On the 1,680 postings, owner's configuration: eligibility identical
1,680/1,680 (including the 181 Mattermost and ElevenLabs postings); score
identical 1,611 (95.9%), fit band 1,647; 49 higher, 20 lower. Reasons: 35
gains by a listed translation, 28 by the same words read where production
missed them, 12+3+3 losses to benefit/condition/company context, 2 to an
inverted head, 8 re-weightings. By language: EN 908/951, PT 481/499, ES
92/100 identical.

Example, the largest move (41 -> 100): `responsibilities` 0 -> 25, the
phrase `workflow automation` found in "Develop scalable automation
pipelines and event-driven workflows" (production's window missed it), and
`technologies` 10 -> 20 by re-weighting once a core duty is present. One
sentence can fill a component's cap: that is the current score's
arithmetic, not v2's, and worth a look before any switch.

## 6. Evaluation on real postings

`scripts/matching_v2_evaluate.py`, evaluation half (840 postings), eleven
profiles (the owner's and ten wizard-built professions: automation,
nursing in Portuguese, teaching, sales, finance, people, design, customer
service in Portuguese, data, operations). Items: every sentence where the
readers disagree, weighted samples of agreements and near misses (every
word of a phrase in a sentence, neither reader matching). Labelled blind by
the agent (no reader decision, no score shown): does the sentence say the
role does or needs the work the phrase names? Labels are AI-made, not the
owner's.

First reading: precision current 0.745 / v2 0.734, recall 0.617 / 0.683.
It showed three faults of v2 (the "apart in an unknown context" rule lost
49 right matches for 19 wrong; unclosed company and benefit sections;
hiring notices), fixed from the DEVELOPMENT half. Second reading (the
evaluation half seen once already, so no longer a clean holdout):

| | precision | recall | F1 |
|---|---|---|---|
| current | 0.769 [0.743-0.792] | 0.631 [0.606-0.656] | 0.693 |
| v2 | 0.775 [0.752-0.796] | 0.775 [0.752-0.796] | 0.775 |
| EN | 0.771 -> 0.786 | 0.663 -> 0.770 | |
| PT | 0.719 -> 0.660 | 0.400 -> 0.857 | |
| ES | (none) -> 0.737 | 0.000 -> 0.727 | |

By profession (F1, current -> v2): owner 0.745 -> 0.770, automation 0.764
-> 0.803, data 0.409 -> 0.889, people 0.521 -> 0.668, sales 0.818 -> 0.902,
customer service PT 0.194 -> 0.759; finance, design, teaching and
operations had too few items for an estimate (< 10 each).

Remaining errors: generic one-word preferences (`recruitment`, `payroll`)
fire in company boilerplate both readers share; a translated phrase reaches
English product names (`Dynamics 365 Customer Service`); the inverted-head
rule lost 7 right matches for 1 wrong on the evaluation half (not changed
after seeing it). Ranking quality was not measured beyond score agreement:
there are no relevance labels per posting, only per sentence.

## 7. Multiple profiles at scale (`scripts/matching_v2_scale.py`)

300 real postings, ms per posting, all profiles together:

| profiles | shared facts | v2 work | v2 eligibility | v2 rest of score | v2 total | current |
|---|---|---|---|---|---|---|
| 1 | 28.2 | 0.2 | 0.1 | 1.0 | 29.5 | 20.8 |
| 2 | 28.7 | 0.5 | 0.2 | 2.3 | 31.6 | 48.0 |
| 10 | 31.4 | 2.2 | 0.6 | 10.3 | 44.5 | 215.8 |
| 50 | 29.7 | 10.4 | 3.3 | 55.2 | 98.5 | 1,152.0 |

A profile costs about 1.3 ms in v2 and 23 ms in the current reader; the
shared reading is slower than one profile's current reading (it reads more,
once), so v2 loses with one profile and wins from two. One profile's
preference changes: v2 9.3 ms per posting from stored facts (no advert
read), current 20.9. One posting's text changes under 50 profiles: v2 73 ms,
current 1,360 ms.

## 8. Invalidation without a full scan

Language-table change on 1,680 stored postings: through the catalogue's
existing full-text index (`job_search`) 132 selected in 6 ms; a full scan
re-reading every advert finds 122 in 5.7 s (projected ~10 min for the
corpus); the index's selection contains the scan's. No new storage: the
index already exists. A morphology-rule change, or an index that is not
current, falls back to the scan and says so. A new phrase of one person
selects its candidates the same way (339 in 8 ms). Version bumps select by
the `versions` column (indexed: 0.15 ms vs 3 ms; 24 B per posting, kept).

## 9. Migration, without a big bang

* **Phase A, shadow.** A background pass fills `job_features` for the most
  relevant postings first (eligible, then by stored score), then the rest;
  the shadow report is produced per profile and read by the owner. Nothing
  the product shows comes from v2. Cost: about 28 ms per posting, once
  (~80 min for the corpus at one core), resumable, interruptible.
* **Phase B, dual read for one component.** The interface may SHOW v2's
  evidence beside the current one (for example the UNCERTAIN and excluded
  sentences), still ordered and filtered by `job_match`.
* **Phase C, v2 controls a low-risk component.** Eligibility first: it is
  already identical by construction, so the switch is storage and speed,
  not behaviour.
* **Phase D, v2 controls work matching and the score**, after its gates
  (below), with its own recalculation of the profile rows only; shared facts
  are reused.

Not needed: dual writes to `job_match`, a full recalculation to turn v2 on,
a readers-13. Postings are materialised on demand and in the background;
the old state stays usable throughout.

**Rollback.** v2's tables are additive and read by nothing in production;
each phase is a switch in configuration reading `job_match` again. Dropping
`job_features`, `job_feature_versions`, `profile_job_match`,
`profile_match_versions` and `profile_match_inputs` returns the schema to
55 plus two empty migrations; `job_match` is never written by v2.

## 10. Proposed gates for production

1. No leakage between profiles (tested: a profile database holds no shared
   fact; the catalogue holds no profile row and no person's phrase).
2. Eligibility parity >= 99.9% with the stored readers-N answers on a copy,
   outside intended, documented changes. Today: 100% on 1,680.
3. Zero regressions on the Mattermost and ElevenLabs cases. Today: 181/181.
4. Work matching: precision not lower than the current reader's at 95%
   confidence, recall higher, on a NEW blind evaluation set (the one here is
   spent), labelled by the owner or another independent labeller, per
   language and per profession with at least 30 items each.
5. Storage: <= 2.5 KB per posting shared, <= 1.5 KB per posting per profile.
   Today: 1.86 KB and 1.09 KB.
6. Performance: one profile no worse than 1.5x the current reader; two or
   more better. Today: 1.4x with one, better from two.
7. Incremental invalidation correct (selective suites) and shadow stable
   across two full passes on a copy.
8. Migrations additive and droppable; full suite and gates green.

## 11. What is still open

* the inverted-head rule cost more than it saved on the evaluation half;
* generic one-word preferences match boilerplate in both readers;
* a component can be filled by one sentence (score arithmetic);
* shared facts cost 28 ms per posting, mostly the U.S.-band sentence regex
  (24 ms when it runs) shared with production;
* Spanish is demonstrative; French and other languages get no grammar
  (proximity only);
* ranking quality has no per-posting relevance labels yet.
