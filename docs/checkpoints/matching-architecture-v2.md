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

---

# Round 3 (2026-10-14): Phase C readiness for Eligibility

Branch `claude/matching-v2-phase-c-readiness`, from `21c4f11`, with the
`_US_ONLY_BAND` fix (`ed0e38f`) cherry-picked first. Not merged; production
unchanged (readers-12, `job_match`). Every number below is from bounded
read-only copies of the real catalogue; no collection, no model, no real
recalculation. Labels in this round are the agent's and are DEVELOPMENT
labels: the gate for work matching is the blind package (section 9).

## 1. The U.S.-band sentence inside v2

Same 300 real postings, run back to back, before (`21c4f11`) and after, ms
per posting, all profiles together:

| profiles | shared facts | v2 total | current | before: shared / v2 / current |
|---|---|---|---|---|
| 1 | 23.2 | 25.2 | 14.8 | 31.7 / 33.3 / 21.5 |
| 2 | 19.4 | 22.4 | 25.8 | 27.3 / 30.4 / 48.4 |
| 10 | 19.6 | 34.5 | 128.2 | 28.6 / 43.7 / 241.8 |
| 50 | 18.5 | 93.2 | 633.1 | 28.5 / 111.3 / 1,107.5 |

The fix cut v2's shared reading by about 9 ms per posting (paid once), and
the current reader by about 10 ms per posting PER PROFILE (it read the
sentence for each profile). So the current reader gained more: with ONE
profile v2 is now 1.7x the current reader (was 1.55x), which breaks proposed
gate 6 (1.5x) for Phase D; from two profiles on v2 is faster (50 profiles:
6.8x). What v2 still pays that the current reader does not: tokens (about
9 ms) and sentence contexts (about 5 ms), both for work matching; Phase C
needs neither.

60 real postings that DO carry a U.S.-only band sentence: the reader 20.9 ->
0.37 ms median (identical answers, same hash); shared facts 59.9 -> 30.2 ms;
the current reader 48.1 -> 21.0 ms with one profile. The slower path of the
isolated benchmark (the anchor at the end of a 7,000-character line) is
synthetic; real adverts did not show it.

## 2. Eligibility v2 against readers-12, widened

`scripts/matching_v2_eligibility_parity.py`: 7,074 open postings drawn per
stratum (every provider, Jobgether's textless leads included; sponsorship,
its absence, authorization, citizenship, clearance, export control, office
attendance, hybrid, worldwide, LATAM, Brazil, United States, Europe,
exclusions, country lists, residence, silence, a published remote, hybrid
or on-site location; the 181 Mattermost and ElevenLabs postings). Whole
`GateOutcome`s are compared (result, reason, code, quote, offsets,
conflict), with the status and the score:

* owner: the stored readers-12 answer = this build's reader = v2 over the
  stored JSON facts, on 7,074 of 7,074 (640 eligible, 2,239 not, 4,195
  unresolved);
* 16 synthetic profiles (BR residence only; BR authorized for BR; BR
  authorized for BR and US; BR needing U.S. sponsorship; BR not authorized
  for the U.S.; contractor yes and no; U.S. authorized without sponsorship;
  CO; MX; AR; PT; DE authorized in four EU countries; IN needing U.S. and
  U.K. sponsorship; two with their own clearance, export, credential, travel
  and requirement blockers): legacy = v2 on 7,074 of 7,074 each; 742,770
  gate comparisons, 0 divergences;
* the blocker profiles exercise the gates no stored answer does: on 743
  clearance, export and citizenship postings, 352 clearance FAILs, 12
  requirement, 6 credential, 1 travel, identical both ways.

Mattermost (14) and ElevenLabs (167, the Colombia and Mexico postings
included): 181 of 181 identical to readers-12, 2,896 profile comparisons.

## 3. Phase C, prepared and OFF

`CAREER_AGENT_ELIGIBILITY_SOURCE` is `legacy` (the default) or `v2`;
anything else is refused by name. Under `v2` a full read hands `match_job`
the production `read_shared` with the STORED eligibility facts attached;
every other reading stays production's. A posting without current facts (no
row, another component version, text, metadata, headings or negation key)
is read from its advert and counted (`eligibility_reads` in the run stats).
Replay keeps its stored gates and is untouched. No readers-13: the same code
over the same facts.

Facts are written only by `scripts/matching_v2_materialize.py`
(`phase_c.priority_order`: stored-eligible first, then by score, then the
rest; `--job-id` on demand; `--max` and `--budget-seconds`; resumes).
Rollback is the variable back to `legacy`. Tested on the real `rescore`: v2
with no facts, with every fact and with one stale fact writes exactly the
legacy rows; a deliberately falsified stored fact moves that one posting
under v2 only, and switching back restores it with the row still in place.

## 4. One-word preferences

On a new development set (ids of class 0: never in the first evaluation),
one-word preferences in 15 professions were right 0.376 of the time. The
sentence's CONTEXT did not separate right from wrong (a duty holds `support
the launch` as often as `customer support`), and v2's listed translations
widen the problem (`suporte` reaches every English `support`). The word's
own grammar did (`structure.one_word`, closed lists only, no profession):

* `pre-sales`: the second half of a hyphenated compound;
* a team the role works with: a unit noun after the word (`sales team`) or
  before it (`equipe de vendas`), or one name in a list, with a word of
  working together within eight words (a bare `with`, `com` or `across` also
  lists tools and skills, and is not enough for a list);
* an English verb: a lower-case word after `to` or a modal or before a
  determiner, or a capitalised imperative opening a duty; never a gerund,
  never a name.

| set | P | R (within matches) | F1 | F1 with no rule |
|---|---|---|---|---|
| development, 265 labelled | 0.495 | 0.957 | 0.652 | 0.547 |
| fresh, 89 labelled before the rules ran on them | 0.479 | 0.946 | 0.636 | 0.587 |

Better, and still poor: about half of one-word matches remain wrong. One
word is weak evidence by nature; the interface should say so, or ask for a
phrase. The owner's shadow run caught two readings that took tools for
teams and verbs (`experience with ToolA, ToolB`, `connect workflows to
ToolA`); both were fixed and are tested.

## 5. The inverted-head rule: removed

New development set (2,249 class-0 postings, 36 profiles): the rule fired on
65 sentences; 40 were the role's own work, 22 another sense. With a base of
6,821 multi-word matches (precision 0.857 on 100 labelled), estimated F1: no
rule 0.8138, the rule 0.8117, only an adjacent preposition 0.8134, only
outside a duty or a requirement 0.8139. None is clearly better than none, so
the rule is gone. On the invented corpora the cost is visible: other-sense
sentences matched rise 0 -> 1 of 7 and 6 -> 20 of 34.

## 6. One sentence filling a component (simulated, NOT adopted)

1,595 postings, the owner and ten professions: 635 of the owner's phrase
components were paid, 353 of them to 100% by ONE sentence (all
`responsibilities`: one central duty counts four pieces, ADR-0036); 751 of
1,450 across profiles. Of 30 such owner components read by the agent, 12
were the posting's work and 18 inflated (an incidental mention, a
collaboration, "technical fluency", integration TESTS, an AI sales role).

| variant | owner scores moved | mean move | band changes, all profiles | owner top 10/20/50 kept, eligible |
|---|---|---|---|---|
| B: no sentence pays over 50% | 359 | -20.7 | 751 | 8/10, 20/20, 50/50 |
| C: one sentence stops at 75% | 353 | -10.5 | 230 | 10/10, 20/20, 50/50 |

Recommendation: C, after the blind labels, as its own reader identity and
the owner's decision; it moves the middle of the list, not the top.

## 7. Two shadow passes and eight changes

`scripts/matching_v2_shadow_passes.py`, 1,595 postings, ten profiles in
separate databases. Pass 1: 91 s. Pass 2 in the REVERSE order: 2.5 s, 0
adverts read, 0 components computed, answers identical. A fresh world in a
shuffled order: identical answers and byte-identical shared facts. Each
profile alone = beside nine others (10 of 10); 0 evidence of another
profile's phrase; 0 shared facts in a profile database; 0 profile rows in
the catalogue.

| change | stale | adverts read | profiles recomputed | time |
|---|---|---|---|---|
| salary preference | compensation_match, score | 0 | that one | 17.0 s |
| work preference | work_match, tools_match, score | 0 | that one | 15.9 s; 38 of 1,595 can gain the phrase (index, 4 ms) |
| residence | eligibility, compensation_match, score | 0 | that one | 17.3 s |
| authorization | eligibility, compensation_match, score | 0 | that one | 16.6 s |
| posting text | everything of that posting | 1 | all ten | 0.09 s |
| language table | language, work_facts, work_match, tools_match, score | 44 of 1,595 (index, 3 ms; 300 of 300 others tokenise identically) | all ten | 4.3 s |
| eligibility rules version | eligibility_facts, eligibility, compensation_match, score | 1,595 | all ten | 89.5 s |
| work match version | work_match, score | 0 | all ten | 60.7 s |

Nothing went stale without a dependency. Recompute granularity is one call
per profile and posting (a salary change re-runs that profile's match from
stored facts, about 10.6 ms per posting); staleness is per component.

Shadow against the stored readers-12 answers (owner, 1,595): eligibility
1,595 of 1,595, score 1,539 (96.5%), fit band 1,568.

## 8. Storage

Shared facts 1,864 B per posting on disk with indexes (1,428 B of columns);
per profile 1,138 B (933 B). Projected for 171,628 postings: 0.30 GiB
shared, 0.18 GiB per profile.

## 9. The blind package

`scripts/matching_v2_blind_package.py`: 562 sentences from postings of id
class 2 (never in the first evaluation nor in this round's development
sets), per profile, language and kind of item (192 disagreements, 167 both
matched, 66 held by v2, 137 near misses); EN 207, PT 158, ES 92, undetected
105; 24 profiles, one-word ones included. The reviewer sees the preference,
the title, the advert's own heading and the sentence; the readers' decisions
are in `key.jsonl`, sealed by its SHA-256. About 15 s per sentence, 2.3
hours. Written to `out/matching_v2_blind/` (ignored: employer text, never
committed). Profiles with too little material for 30 items: design,
teaching, finance, operations (6 to 12 each).

## 10. Migration, revised

* **A, shadow**: as before; the passes above are its acceptance.
* **B, dual read**: show v2's UNCERTAIN and excluded evidence beside the
  current one; ordering and filters stay on `job_match`.
* **C, Eligibility from shared facts**: run `matching_v2_materialize.py` in
  the background (eligible first; 18 to 23 ms per posting, about an hour for
  the corpus on one core, interruptible), then set
  `CAREER_AGENT_ELIGIBILITY_SOURCE=v2`. Missing facts are read, never
  guessed; no `job_match` recalculation, no readers-13; rollback is the
  variable. The migrations 0056 and 0057 must be applied first (additive).
* **D, work matching and the score**: only after the blind labels clear
  gate 4, the one-profile speed gate holds again, and the one-sentence
  decision is taken; it recomputes profile rows from stored facts, never
  171k adverts.

## 11. Verdicts

* Eligibility: **READY_FOR_CONTROLLED_PHASE_C**: parity 100% on 7,074
  postings and 17 profiles; Mattermost and ElevenLabs 181 of 181; no leak
  between profiles; rollback tested; shadow deterministic across two passes
  and a fresh world; migrations additive; suite green.
* Work matching and the score: **KEEP_WORK_MATCH_IN_SHADOW**: no
  independent labels yet; one profile at 1.7x; one-word precision about 0.5;
  the one-sentence ceiling undecided.

---

# Round 4 (2026-10-14): the first human evaluation, Phase D readiness

Branch `claude/matching-v2-phase-d-readiness` (worktree `ca-phased`), from
`main` at `d094b27` (Phase C in production). Not merged. Production is
untouched: readers-12 scores, `CAREER_AGENT_ELIGIBILITY_SOURCE=v2`.

## 1. The labels, frozen

The owner labelled package 1 blind (562 sentences, class 2). Verified before
use: key SHA-256 `26b4e6e1...a94` = the seal = the expected value; 562 rows,
288 Y, 245 N, 29 ?; no id missing, duplicated, unknown or invalid. The
reviewed file reorders two columns, corrects 89 language cells (French and
Italian marked `und`) and strips bullets and markup from 22 sentences; two
sentence cells (`b0015`, `b0390`) were overwritten, both labelled `?`, so
outside every metric. Original, reviewed file, key and instructions are
frozen with their hashes beside the package (never committed: employer
text). The importer ran unchanged on `d094b27`; it reproduces 562/562 key
decisions (`scripts/matching_v2_blind_replay.py`). Package 1 is SPENT: it
measured `d094b27` and validates nothing developed after.

## 2. Official result (`matching_v2_blind_package.py score`, Kish intervals)

| group | n | current P / R / F1 | v2 P / R / F1 |
|---|---|---|---|
| ALL | 533 | 0.678 / 0.606 / 0.640 | 0.711 / 0.722 / 0.717 |
| one-word | 339 | 0.632 / 0.608 / 0.620 | 0.668 / 0.709 / 0.688 |
| 2+ words | 194 | 0.872 / 0.601 / 0.712 | 0.870 / 0.763 / 0.813 |
| EN | 191 | 0.681 / 0.639 / 0.659 | 0.723 / 0.715 / 0.719 |
| PT (detected) | 149 | 0.714 / 0.364 / 0.482 | 0.654 / 0.729 / 0.690 |
| ES (detected) | 88 | 0.475 / 0.253 / 0.330 | 0.547 / 0.812 / 0.654 |
| und | 105 | 0.623 / 0.528 / 0.571 | 0.711 / 0.816 / 0.760 |
| owner | 36 | 0.995 / 0.670 / 0.801 | 0.959 / 0.736 / 0.833 |

Every other profile has fewer than 30 labelled items except automation,
one_support and one_suporte: INSUFFICIENT_FOR_GATE.

## 3. Paired (`scripts/matching_v2_blind_paired.py`)

Same 533 items. Both right 212, both wrong 134, current right / v2 wrong 64,
current wrong / v2 right 123. v2 recovers 87 true positives, loses 15,
removes 36 false positives, introduces 49 (weighted: 1,500 / 525 / 1,104 /
1,151). Stratified paired bootstrap (within cells, seed 20261014, 2,000):

| group | delta precision | delta recall | delta F1 |
|---|---|---|---|
| ALL | +0.034 [-0.043, +0.112] | +0.118 [+0.044, +0.192] | +0.078 [+0.004, +0.154] |
| one-word | +0.037 [-0.054, +0.126] | +0.102 [+0.006, +0.194] | +0.070 [-0.023, +0.157] |
| 2+ words | -0.004 [-0.101, +0.093] | +0.162 [+0.119, +0.209] | +0.101 [+0.045, +0.149] |
| EN | +0.039 [-0.046, +0.125] | +0.074 [-0.012, +0.157] | +0.057 [-0.028, +0.140] |
| PT (detected) | -0.059 [-0.200, +0.074] | +0.367 [+0.227, +0.491] | +0.207 [+0.066, +0.330] |
| ES (detected) | +0.073 [-0.072, +0.233] | +0.559 [+0.468, +0.654] | +0.324 [+0.191, +0.447] |
| und | +0.088 [+0.035, +0.140] | +0.290 [+0.222, +0.363] | +0.189 [+0.141, +0.234] |

By the reviewer's corrected language (descriptive): PT -0.011 [-0.167,
+0.144] in precision, +0.486 [+0.382, +0.568] in recall.

## 4. The human gate, read literally (reading fixed before any delta)

| criterion | verdict |
|---|---|
| new, blind set | PASS (class 2, sealed, labels before the key) |
| owner's labels | PASS |
| precision not lower at 95% | INSUFFICIENT_DATA (ALL interval crosses zero; und PASS) |
| recall higher | PASS (ALL, PT, ES, und); EN INSUFFICIENT_DATA |
| per language with 30+ items | PASS for EN, PT, ES, und |
| per profession with 30+ items | INSUFFICIENT_DATA (3 of 23 professions plus the owner) |

**HUMAN_WORK_MATCH_GATE = INCOMPLETE.** Nothing failed; precision parity is
not demonstrated and most professions are unmeasured.

## 5. v2's errors (198), by structural cause

| class | n | FP / FN | current also wrong | v2 regression |
|---|---|---|---|---|
| word form not matched (near miss) | 49 | 0 / 49 | 49 | 0 |
| unsupported language (FR, IT, DE, PL, NL, ID, RU) | 37 | 16 / 21 | 29 | 8 |
| hiring or legal notice (recruit words) | 20 | 20 / 0 | 9 | 11 |
| one word in another sense | 18 | 18 / 0 | 9 | 9 |
| team or collaboration not caught | 16 | 16 / 0 | 10 | 6 |
| company or product description | 11 | 11 / 0 | 6 | 5 |
| work written in a benefits section | 11 | 0 / 11 | 5 | 6 |
| product or system name | 8 | 8 / 0 | 1 | 7 |
| phrase in another sense | 8 | 8 / 0 | 6 | 2 |
| false UNCERTAIN (verb 6, team 6, compound 4, other sense 1) | 17 | 0 / 17 | 10 | 7 |
| education level read as teaching | 3 | 3 / 0 | 0 | 3 |

The language detector put French and Italian adverts in `es` and `pt` (and
German, Polish, Indonesian in `en`), so the official PT and ES rows contain
other languages. `cross-selling`, `upselling` and `inside-sales` are sales:
the compound reading is wrong for prefixes that keep the activity.

## 6. One-word preferences

v2 vs current on one-word items: P 0.668 vs 0.632, R 0.709 vs 0.608; v2
removes 32 false positives and introduces 38 (weighted 1,027 vs 1,020: net
zero), recovers 43 true positives and loses 12 (1,140 vs 501). Against 2+
words (P 0.870) one word stays weak evidence in both readers.
**ONE_WORD_ACCEPTABLE_FOR_PHASE_D**, relative to the current reader: no more
false positives, more right matches.

## 7. One sentence filling a component (human labels)

Of the 290 labelled items v2 matched, 170 pay into a phrase component: 104
Y (mean share 0.320 of the component) and 66 N (0.230). 23 Y fill a
component to 100% alone; 9 N do too. No component at its ceiling was held
by two distinct labelled sentences. Under B a sentence's share drops 35% for
Y and 30% for N; under C 18% and 15%. A cap scales right and wrong evidence
alike: it is not a precision fix, recognition is. The labels prove what a
sentence says; they cannot prove what a posting is worth (that needs
posting-level relevance labels). Ranking effect on 1,595 postings (owner):
B moves 363 scores (-20.5), owner top 10 5/10 overall, 8/10 eligible; C moves
353 (-10.5), top 10 9/10, eligible 10/10. **Recommendation: KEEP_A.**

## 8. Performance (output-neutral, proven)

`tokens` skips the fixed-expression window for a root that opens none;
`_norm` is cached; `_has` is one alternation; `heading_context` looks up word
prefixes. v2's whole answer hashed per profile and posting on 323 postings x
26 profiles: identical (8,721 lines); the 562 items 562/562; shadow report on
1,595 postings identical; sentences, contexts, headings, tokens, language
identical on 115,959 lines. One profile, interleaved, best of 3: v2 17.7-18.0
-> 16.4-16.9 ms; ratio to current 1.56-1.61 -> 1.39-1.50 (gate 1.5: met by
every run after the change, with a small margin on a noisy machine). Final
code, 300 postings, ms per posting:

| profiles | shared | v2 total | current |
|---|---|---|---|
| 1 | 13.3 | 14.6 | 9.9 |
| 2 | 13.6 | 15.9 | 20.5 |
| 10 | 14.3 | 25.9 | 104.3 |
| 50 | 14.3 | 74.9 | 533.9 |

## 9. Corrections, on a NEW development set

dev3: 1,300 postings of class 0 in none of the earlier sets, plus the
Portuguese postings of the earlier development sets (class 0 has no unused
Portuguese left). The 562 labels named the classes; they tuned nothing.
* Language identification for French, Italian, German, Dutch, Polish and
  Indonesian (no grammar; never read as EN/PT/ES): 420 of 2,379 development
  postings changed language, every sampled change correct.
* Hiring notices for recruitment words: 39 of 73 non-work set aside, 0 of
  74 recruiting duties lost; 4 other matches move, all privacy notices.
* NOT changed: work written in a benefits section. The owner's own rule
  (round 3) says a benefit cannot prove work; her labels said Y to 11 such
  sentences. DECISION_REQUIRED.

## 10. Package 2

`out/matching_v2_blind_2` in the worktree, key SHA-256 `7e92cf93...ecbf`: 760
sentences, about 3.2 hours, from 1,720 postings of class 2 plus 160
Portuguese postings of classes 1 and 3, none in package 1, any development
set or the shadow sample. 22 of 26 profiles at 32+ items. It measures this
branch's code, not `d094b27`.

## 11. Readiness

| item | verdict |
|---|---|
| A human quality | INSUFFICIENT_DATA (gate INCOMPLETE) |
| B one-word | PASS (relative to current) |
| C multilingual | FAIL at `d094b27` (language misidentified); corrected, unvalidated |
| D multi-profession | INSUFFICIENT_DATA |
| E determinism | PASS |
| F isolation | PASS |
| G invalidation | PASS |
| H storage | PASS (1.86 / 1.14 KB; +322 MB measured in production) |
| I performance | PASS, marginal (1.39-1.50 vs 1.5) |
| J score arithmetic | DECISION_REQUIRED (recommend KEEP_A) |
| K rollback / migration | PASS for the pattern proven in Phase C; Phase D's own switch not built |

**KEEP_WORK_MATCH_IN_SHADOW.** Next: the owner labels package 2; the gate is
read on it with the same rule; the benefits question is decided.

---

# Round 4b (2026-10-15): a benefits heading versus a sentence that assigns work

Owner's decision: section context is evidence about what a passage is FOR,
not an override of what a sentence says. A benefit proves no work; a sentence
under a benefits heading that itself gives the role a duty or a requirement
may.

## What the code did before

Not an absolute block. Under BENEFIT, COMPANY_DESCRIPTION or CONDITION a
sentence carrying any role-voice cue (`you will`, `you have`, `you are`,
`this role`, ...) was lifted to OTHER and could match. Under a benefits
heading that cue is mostly the benefit itself (`you will receive`, `if you
have any doubts`), so the existing exception was too wide, not too narrow.

## The rule (B), `context.benefit_states_duty`

Only for BENEFIT (company and condition headings keep the role-voice reading):
a sentence is lifted to OTHER when it has a duty cue (`responsible for`,
`you must`, `responsavel pelo`, `seras responsable de`), introduces the role
(`we are looking for`, `this role is`, `buscamos`), or has the candidate as
the subject of a verb that is not something given to them (`you will teach`,
`voce vai ministrar`, `vas a liderar`; never `you will receive`, `voce vai
contar com`, `vas a tener`). No subject, no lift: a duty bullet left under a
benefits heading by a malformed advert stays a benefit. Data in
`context_data.yaml` (`benefit_duty`); `sentence_context` 3 -> 4.

## Development (new set; package 1 not used)

1,642 postings of id class 0 plus 170 Portuguese postings of classes 1 and 3,
none in package 1, package 2, dev2/dev2b/dev3 or the one-sentence sets. 457
items (181 sentences) under a benefits heading whose phrase matches when the
heading is ignored, labelled by the agent: 23 Y, 399 N, 35 `?`.

| rule | TP | FP | FN | precision | recall | F1 |
|---|---|---|---|---|---|---|
| A0 absolute block | 0 | 0 | 23 | - | 0.000 | 0.000 |
| A1 code before (role voice) | 2 | 15 | 21 | 0.118 | 0.087 | 0.100 |
| B explicit duty | 2 | 0 | 21 | 1.000 | 0.087 | 0.160 |

All 15 false positives A1 admitted were recruiting-scam and privacy notices
(`if you have any doubts`, `you also represent`); B admits none. Every
difference is in English; the Portuguese benefit sentences of the set (40
items) are wellness and support programmes that no rule lifts. 21 of 23 Y
are duties or requirements under a heading the advert never closed, with no
subject: B leaves them, by the decision. On 18 invented sentences written
with the rule (EN 9, PT 5, ES 4, a specification rather than evidence): A1
TP 6/7 FP 6/11, B TP 7/7 FP 0/11.

## Package 2

Replayed with B on its 453 postings: 760/760 key items reproduce (current
and v2 state, cause, context). Off the package, 35 sentence contexts in 25
postings move (33 OTHER -> BENEFIT, 2 BENEFIT -> OTHER) and 6 profile
answers change; no labelled item does. Package 2 stays as generated (key
SHA-256 `7e92cf93...ecbf`); its labels measure B as they would have measured
the code before.

---

# Round 5 (2026-10-15): package 2, the final human evaluation of the candidate

Measured on `e5d5fa6`. No rule was changed in this round; package 2 labels
are evaluation, never development. Frozen copies with hashes beside the
package (`human_review_20261015`, never committed: employer text).

## Files and reproduction

Key SHA-256 `7e92cf93...ecbf` = the seal; 760 rows, 430 Y, 317 N, 13 `?`; no
id missing, duplicated, unknown or invalid. The reviewed file corrects 22
language cells (14 `und` -> en, 7 `und` -> fr, 1 pt -> und) and strips
bullets and hyphens from 177 sentences (content unchanged). The branch
reproduces 760/760 key decisions (current; v2 state, cause and context);
v2's digest over the package's 453 postings is identical to the run that
validated `e5d5fa6`.

## Official result (Kish intervals) and paired deltas (seed 20261014)

| group | n | current P / R / F1 | v2 P / R / F1 | delta P | delta R |
|---|---|---|---|---|---|
| ALL | 747 | .643 / .602 / .622 | .691 / .693 / .692 | +.047 [-.029, +.120] | +.090 [+.014, +.160] |
| one-word | 448 | .612 / .598 / .605 | .654 / .677 / .665 | +.041 [-.050, +.131] | +.079 [-.017, +.167] |
| 2+ words | 299 | .777 / .619 / .689 | .847 / .750 / .796 | +.069 [-.026, +.139] | +.131 [+.091, +.164] |
| EN | 359 | .644 / .616 / .630 | .695 / .685 / .690 | +.052 [-.029, +.129] | +.070 [-.013, +.146] |
| PT | 150 | .655 / .336 / .444 | .637 / .819 / .716 | -.018 [-.155, +.128] | +.482 [+.385, +.568] |
| ES | 108 | .594 / .207 / .307 | .740 / .943 / .829 | +.146 [-.077, +.404] | +.737 [+.658, +.837] |
| und | 74 | .576 / .710 / .636 | .496 / .663 / .567 | -.079 [-.149, -.031] | -.046 [-.123, +.005] |
| fr/de/it/id/nl | 56 | .636 / .522 / .573 | .699 / .753 / .725 | +.063 | +.231 |

ALL F1 delta +.069 [-.004, +.139]. Paired, ALL: both right 323, both wrong
175, current right / v2 wrong 75, current wrong / v2 right 174; v2 recovers
120 true positives, loses 24, removes 54 false positives, introduces 51.

21 of 26 configurations have 30+ labelled items (design 25, one_payroll 28,
teaching 16, one_enfermagem 2, nursing_pt 1 do not). By the fixed reading:
precision FAIL for one_teaching (-.390), one_vendas (-.472) and one_design
(-.004); recall FAIL for one_design, one_logistics, one_marketing (-.001) and
one_operations. A bootstrap within cells of one or two items cannot vary, so
the intervals of small groups (`other`, one_design, one_marketing) are too
narrow; they are reported as computed and not used to rescue or condemn.

## Gate (reading unchanged since 2026-10-14)

* precision not lower: INSUFFICIENT_DATA (ALL interval crosses zero)
* recall higher: PASS (ALL, 2+ words, PT, ES)
* one-word: INSUFFICIENT_DATA
* multilingual: FAIL (`und` precision interval wholly below zero)
* multi-profession: FAIL (one_teaching, one_vendas precision)

Secondary, package 1 (`d094b27`) and package 2 (this branch) combined by
inverse variance as if one system: delta precision +.041 [-.013, +.094],
recall +.104 [+.052, +.156]. Still crosses zero.

## What the labels show (reported, not fixed)

* `teaching` (one word) matches an education level (`Ensino medio completo`,
  6 items) and the employer teaching the hire (`We'll teach you the
  product`): the education-level class package 1 already showed, unfixed.
* `vendas` reaches English `Sales` as a team or department name (`to Sales
  and regional leadership`).
* Recruitment notices: 18 of 22 N set aside; 4 remain (personal-data
  processing, a scam warning, an agency description).
* Benefits: 34 items under a benefits heading, none lifted by B, none by the
  old rule; 7 labelled Y stay set aside, 4 of them `Support throughout the
  recruitment and integration process` (support received, which the
  decision says proves no work: a label and the rule disagree).
* Language: one Turkish posting detected `pt`; 15 English items in postings
  detected `und` (v2 wrong on 7, current on 5).

**KEEP_WORK_MATCH_IN_SHADOW.** The Phase D mechanism was not built.

---

# Package 3 gate, pre-registered (2026-10-15, before any development of this round)

Fixed by the owner before package 3 is drawn; implemented as
`matching_v2_blind_paired.py --gate package3` and not to be changed after.

* Primary, ALL, paired stratified bootstrap as before (seed 20261014, 2,000
  replicates, within cells): precision non-inferiority with margin -0.03
  (lower bound of the 95% interval of precision v2 - current above -0.03),
  recall higher (lower bound above 0), F1 v2 >= F1 current at the point.
* Safety, each group with 30+ labelled items among one-word, 2+ words, EN,
  PT, ES, each profile and each preference of two words or more: BLOCK on a
  material and consistent regression (precision delta <= -0.10 with upper
  bound < 0 and 3+ false positives introduced; or recall delta <= -0.10 with
  upper bound < 0, 3+ true positives lost and lower F1), or a STRUCTURAL
  defect named in the qualitative review (3+ v2-only errors in one group
  with one structural cause). No per-group significance is required.
* `?` stays out of every metric.

This round tunes only the four known defects (teaching, sales as a team,
recruitment notices, `lead generation` composed from unrelated words), on
NEW development material; packages 1 and 2 are a regression set only.
