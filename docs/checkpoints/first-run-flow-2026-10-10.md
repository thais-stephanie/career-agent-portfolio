# The first run of a new installation, made usable (2026-10-10)

Branch `claude/first-run-flow`, from `main` 96aae27. The owner reset her
installation and ran the first collection (124,528 postings, 124,233 scored,
finished 2026-10-10T03:45:29Z). The results could not be used. Every cause
below was reproduced on a read-only COPY of her databases; nothing of hers was
written, collected, rescored or sent to a model.

## Causes, proven

| Symptom | Cause | Fix |
|---|---|---|
| Find jobs: `a.label.localeCompare is not a function`, Retry repeats it | An employer's slug is `constructor` (3 postings). `normaliseFacets` looked labels up on a plain object, and `{}['constructor']` is JavaScript's own `Object` function: the label was a function and sorting threw on every list read | Own-property lookup (`Object.hasOwn`) in `api.js`; nothing converted to text or ignored |
| List "unavailable for 20 minutes" | The same error after each answer; `/api/jobs` answers in about 3 s on the copy | The list no longer waits on a secondary read (readiness), says when it is slow (8 s), fails with a retry after 90 s, and shows a plain sentence with the program's words folded |
| Settings: "Never run", "Last successful source check: never", "0 need attention" while the sidebar said a site needed her | Settings read its sources ONCE per session (`if (loaded && !force) return`); its 5-second poll gave up while Settings was hidden and never rescheduled, and a finished run reloaded it only if Settings was on screen | The panel is invalidated when the poll fires hidden and when a run ends elsewhere; the next visit reads again |
| Every unreached source "Never run" during the first check | `QUEUED` existed and was never assigned | A source a running whole check has not reached is `QUEUED` ("Waiting to start") |
| "A job site needs you" / "Fix a job site" | Workable stored 71,777 complete records, then the 550 MB XML transfer did not finish; the run was recorded FAILED and FAILED meant "fix it" | A failed run that stored postings is `PARTIAL` (`SOME_FAILED`, "what was read is kept") and counts as the last read; `RATE_LIMITED` (the site's cooldown) is a state, not a task. The alert and the Home task name the source and open its own card in Settings |
| "124,233 of 124,233" with no way to the list | After the last posting the same pass rebuilds the search index (a full rebuild on a first run, minutes long); scoring failures were never reported | `/api/retrieval` `scoring.step` = `preparing`, `failed`, `error`; the card says "Preparing your job list", and a failure offers "Score my jobs again" (collects nothing) with the details folded |
| "New" overflowed its circle | `124528` in a 30 px circle | Locale short form ("125K", "125 mil") with the exact number as the title; the circle becomes a pill when wider |

## Why the scores are lower than before the reset

Not a defect, and nothing was raised to hide it. On the backup taken before
the reset (config v11): jobs WITH a semantic evaluation (7,495, from 7 DeepSeek
runs) averaged 52.1 and held 2,107 of the 2,688 jobs at 55 or more; jobs
WITHOUT one averaged 23.4. The new installation has 0 semantic evaluations
(an evaluation is an explicit run; choosing a provider runs nothing), so every
score is keywords only: average 21.6, 40 jobs at 55 or more. The rest of the
gap is the search itself: 19 phrase groups from the new onboarding against 40
before. Confirmed evidence (148 claims) never enters a score
(`tests/integration/test_evidence_reach.py`).

The list now says which basis its scores have, and the onboarding asks how jobs
are matched before the first search (keywords only by default; an AI provider
needs an explicit choice and a confirmation of what it receives and costs).

## Profile overview

The complete-profile overview put "Your skills" after the whole row, leaving a
hole under the counts beside the long latest-role card; it now shares the
counts' column. The evidence note sat in a 60ch column inside a ~1,060 px card
with no gap to its button, and inherited `.card`'s column direction (so
`flex: 1 1 320px` became a HEIGHT on a phone). It has its own row layout now,
and the wording the owner gave (confirmation never changes the match, checked
against `test_evidence_reach.py`).

## Pre-existing, not changed here

`tests/integration/test_board_discovery.py` takes 17-28 s per test on this
machine, identically on `main`; it slows the full suite, it is not a failure.
