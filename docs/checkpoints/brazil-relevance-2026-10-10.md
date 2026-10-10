# Work she can do from Brazil, ranked by the work (2026-10-10)

Branch `claude/brazil-relevance`, from `main` dacaf4d. Asked for after the
Pavago "Full Stack Engineer" diagnosis: a list without other territories'
jobs in the way, reposts that show the copy she can take, matching that reads
the activity rather than three tool names, an explanation that never ticks a
silence, an optional English title, and her skills editable on My Profile.

Measured on a read-only COPY of her databases (124,528 postings, config v7),
rescored there with `--force` (33 min, 0 errors); nothing of hers was written, collected,
rescored or sent anywhere.

## What changed

| # | Change | Where |
|---|---|---|
| 1 | "To confirm" territory hidden by default; one switch, "Include jobs whose location is still to confirm", remembered under a new key | `state.js`, `i18n.js`, ADR-0034 addendum |
| 1 | An employer's EXPLICIT hiring statement ("must be based in Latin America") admits her beside an office location elsewhere; prose, declared fields and aggregator sightings are still withdrawn | `match/gates.py` |
| 1 | "The LATAM region" resolves; an empty country label prints the country's name ("which includes Brazil", was "includes .") | `match/gates.py` |
| 1 | "We do not sponsor visas" (no country) on a REMOTE role is answered for someone authorized where she lives | `match/authorization.py` |
| 2 | The repost she can take stands for its group (eligibility, then score); each copy keeps its own status | `storage/mvp_repo.py` |
| 3 | A wizard-shaped phrase is read in its inflections, word orders and coordinations within one sentence; no synonyms, no model, no new intent | `match/phrase_forms.py`, `match/lexicon.py` |
| 3 | The tools guard opens in proportion to the work found (one activity of four opens a quarter of the second half), not on one bullet | `match/score.py` |
| 4 | Unknown pay, and a contract when none was asked for or stated, leave the score instead of paying 2 + 1 | `match/score.py` |
| 1 | Plain hiring statements ("We hire candidates in Brazil only", "Open to candidates in Brazil") name a scope when the list names a real place; "We hire candidates in United States only" refuses | `match/gates.py` |
| 4 | A labelled contract line in the body ("Position Type: Full-Time", "Tipo de contratação: PJ") is a contract statement | `match/employment.py` |
| 4 | A range of years is read by its lower bound ("4-6+ years" is 4); years no longer name a level | `match/years.py`, `match/seniority.py` |
| 4 | Drawer: "What fits" (met), and "What the posting leaves open" (not stated, or no conflict) with no tick | `detail.js` |
| 5 | Settings > Look > Job titles: the English half of a title written twice, on cards and lists only | `format.js`, `cards.js`, `table.js` |
| 6 | My Profile: add, rename, remove skills in the Overview and Skills cards, through the evidence routes; tools she wants next shown apart | `profile.js`, `web/api.py` |

`READER_IDENTITY` readers-9, `MATCH_SCHEMA_VERSION` 13.

## Measured on the copy

| | Before (main) | After |
|---|---|---|
| Main list (groups), "to confirm" shown (old default) | 67,029 | 70,618 |
| Main list (groups), "to confirm" hidden (new default) | 3,152 | 3,338 |
| "To confirm" groups hidden by the new default | 63,877 | 67,280 |
| Confirmed eligible postings | 4,089 | 4,198 |
| Explicitly not eligible | 30,771 | 30,797 |
| Geography confirmed, an authorization question pending | 819 | 784 |
| Pavago Full Stack group's card | India copy, 68, to confirm | LATAM copy incl. Brazil, 69, eligible |

The list grows by about 3,600 groups with "to confirm" shown because years
no longer make a posting JUNIOR, which her excluded levels hid.

**Ordering, on a labelled set kept apart from the rule work.** 120 eligible
postings drawn from the copy before any new score existed: the 60 best-scored
groups and 60 at random (seed 20261010). Labelled by the agent against her
seven activities and eight role anchors (relevant: the main activity is one of
them), blind to scores but not to the stratum; Pavago Full Stack excluded as
the development case. 26 relevant, 93 not. AGENT LABELS, not owner-reviewed.

| | Before | After |
|---|---|---|
| P(relevant ranks above irrelevant) | 0.799 | 0.886 |
| ... within the 60 best-scored | 0.599 | 0.810 |
| Relevant in the first 10 / 20 | 5 / 10 | 8 / 14 |
| Mean score, relevant / irrelevant | 42.8 / 30.7 | 55.8 / 29.9 |

Relevant scores rose and irrelevant ones did not: the gain is ordering. In the
top 20 of the new default list, 14 or 15 are her work, against 10 before.

## Limits, said plainly

* **Pavago Full Stack stays high (69, sixth).** The advert really says
  "automation workflows", "system integrations" and "business processes";
  what makes it a software-engineering job is its title and its
  non-negotiables. Reading the title is ruled out by
  `evaluation/role_alignment/README.md` (a title rule would push down the
  moves between neighbouring roles people make most). Telling a central
  activity from a secondary one in the body needs a reader this change does
  not add.
* **A Marketing Specialist rose to 63**: marketing roles that automate their
  own work share her words.
* **The 784 "authorization question pending"** are hidden with the rest of
  "to confirm"; answering the question in her profile settles them.
* **Phrase forms are English and Portuguese morphology only.** An activity
  typed in English is not found in a Portuguese advert unless the root is
  shared (automacao / automation).
* **The English title rule is conservative**: a title that starts in another
  script, or mixes a brand into it, stays whole.
* **Tools she wants next are read-only on My Profile.** They are search
  phrases; editing them is a configuration change that asks for a
  recalculation, and no screen offers it yet.

## Integration

1. Close the app; back up as before (`pre-brazil-relevance-*`).
2. Fast-forward `main` to this branch.
3. Open the app through the shortcut, then press **Recalculate** (or run
   `career-agent rescore`). The reader identity moved, so every posting is
   read again: 30 to 33 minutes on the copy with another job running beside
   it, zero network, zero inference. Until it finishes the previous scores
   stay on screen.
4. Her list then opens on work confirmed open to her; the switch brings back
   "to confirm".
