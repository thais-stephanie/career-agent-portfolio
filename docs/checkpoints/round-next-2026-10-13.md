# Speed, optional preferences, contractor and pay defaults (2026-10-13)

Branch `claude/round-next-fixes`, from `858f22d` (the reader-notice fix, not
yet on the installation's `main`). Nothing here was run on the owner's data:
measurements use a synthetic corpus (`scripts/synthetic_volume.py`) and the
screens a small synthetic profile.

## What changed, and why

**Home and the deep reading were slow every time.**
- `/api/home` asked the recalculation plan on every visit. The plan reads which
  reader made every score from `job_match.input_digest`, the last column after
  the large `result_json`, so it walked every score's overflow pages.
  Migration 0053 indexes `(config_id, config_version, input_digest)`; Home's
  whole answer is remembered under a key of everything it shows
  (`_jobs_freshness`, the latest run, confirmed evidence, the profile).
- `/api/deep` ran the main-list query up to six times, read every advert to
  measure its length, ran a full-text ordering it then discarded, and did it
  all before the panel could draw. The panel is now `/api/deep` (fast) and the
  counts `/api/deep/coverage`: one read of the list, lengths from the record
  header (`octet_length`, exact up to the 200-character threshold), remembered
  by profile, score revision, configuration digest and `_jobs_freshness`. The
  page draws the panel at once and says "Counting" until the counts land; a
  late answer from an earlier open is dropped; a failure is a failure.
- The reader-count memo (`api._reading_moved`) is keyed by the profile's
  database, the configuration, the reading identity and `population_token`, so
  scores written by any process, closings and reopenings all invalidate it.
- Tried and withdrawn: keeping the previous Home on screen while it reloads.
  It let old and new parts of the page mix (the first-run steps and the
  guided setup's last card failed in the full browser suite, never alone), and
  the remembered answer already brings Home back in a quarter of a second.

Synthetic corpus, 172,000 open postings, split profile and catalogue, same
machine, a fresh process each time (second process, disk cache warm):

| request | before (858f22d) | after |
|---|---|---|
| Home, first open (health + home + first run) | 47.5 s | 1.9 s |
| Home, coming back | 19.6 s | 0.26 s |
| Deep reading panel, first open | 77.0 s | 0.36 s |
| Deep reading counts, first open | (inside the 77 s) | 3.0 s |
| Deep reading, coming back | 77.2 s | 0.6 s |
| Find jobs, first open / coming back | 3.2 s / 0.57 s | 1.05 s / 0.36 s |

Cold disk (the first process after building the index): Home 10.7 s, deep
counts 21.6 s; the panel itself 0.65 s.

**Optional preferences only add** (ADR-0036, readers-11, schema 14, revised
with the owner's decisions A1 + B1 on 2026-10-13): each preference the posting
shows is one piece at the strength of where it shows (duty, requirement or
passing mention), so repetition and splitting the same content into items add
nothing; one clear central work match completes "Work you want"; once a
component has a preference, naming more never lowers a posting, while the
first preference of an empty component brings a new criterion into the %
(said in "How this score works"); tools read where the work is (held at half
when the wanted work is neither a duty nor a requirement, nothing in a
sentence about avoided work). On the 2026-09-27 benchmark, EXPLORATORY because
its holdout was consulted while choosing: GOOD+ false positives 22 / 10 (dev /
holdout), valid kept 65 / 28. **The contractor default** and **USD pay
periods** (same ADR); the Contract filter reads the engagement (stated or the
default, `job_match.engagement`), the Hours filter only full-time and
part-time. **Explanations**: the
drawer lists, per kind of preference, what was marked, what the ad mentions and
what it does not; "These lower the %" holds only what took points (a level, pay
below target, an unwanted contract or way of working, an avoid phrase). A score
made by earlier rules says so.

**Find jobs**: the search sits on the list's centred column on wide screens; a
header sorts ascending, descending, then back to best match, over the whole
filtered list before paging, empty values last, text without case; the status
sorts in the order the work moves. "Export CSV" adds the whole stored job
description (fetched for the page's rows); "Export best fits" adds it too.

**Adverts in Markdown** (4 Day Week, e.g. Oyster) render headings, bold,
italics, lists and safe links; the stored text is untouched. **The deep
reading's introduction** wraps at the width it has (640px measure), and the
"How this score works" panel takes the list's whole width: it was capped at
640px (`.evalmode__body`), so on a wide window every explanation broke early
beside empty space. No `<br>`, no `white-space` rule and no inserted breaks
were involved; one paragraph per explanation, the label in bold on its line.

## Needs a recalculation

Yes, once, after integration: readers-11 changes the score arithmetic, the
engagement and pay readings and the contract facet. It supersedes the pending
readers-10 recalculation (one pass covers both). The seniority wording, the
pay formatting, the sorting, the export, Markdown and the speed work need none.
Migration 0053 runs on the first start: one index, 23 s on the synthetic
corpus. On the owner's corpus, reading the column it indexes took 104.5 s with
a cold disk (read-only measurement, 295,552 scores), so the first start after
integration pays about that once; every later page open stops paying it.
