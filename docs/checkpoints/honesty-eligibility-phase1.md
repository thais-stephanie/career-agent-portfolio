# Honesty and eligibility, phase 1: integration record

Branch `claude/honesty-eligibility-phase1`, based on `181b639` (origin/main,
re-fetched 2026-10-08: unchanged, merge base identical). Not pushed, not
merged.

## What changes on first open

- **Migration 0050** (`resume_statement_acknowledgement`, `-- scope: profile`)
  is applied to each profile database the first time this build opens it.
  Additive: one new table, nothing altered or dropped. `0050` is free on main
  and on every remote branch (checked 2026-10-08). The shared catalogue gets
  no migration.
- **Scores become stale, not wrong.** `READER_IDENTITY` is `readers-4` and the
  configuration digest gains the new eligibility fields, so no stored score is
  current. The list keeps serving the old scores and offers "Recalculate"
  (INSTALL.md, "When the update changes how postings are read"). A targeted rescore resumes if interrupted.
- **Settings files.** A `search.local.yaml` is read as before; it is rewritten
  only when the person saves a settings change, and then it carries the new
  `eligibility` keys. A profile without `semantic.local.yaml` that has used AI
  keeps Auto; one that never did is deterministic.

## Pre-existing browser failure (not introduced here)

`tests/browser/test_hide_a_job.py::test_the_notice_counts_what_she_hid_apart_from_what_was_hidden_for_her`
fails intermittently: 1 of 3 runs on this branch, 1 of 4 on an untouched
`git archive` copy of `181b639`. It passes in isolation. It is a timing flake
in the notice's wait, not a behaviour change, and is recorded rather than
fixed in this phase.

## Backup before updating

Stop Career Agent first (Windows will not copy a database another program
holds open cleanly, and the app migrates on open). Then, for each profile:

```powershell
uv run career-agent backup --profile "<profile name>"
uv run career-agent backup --catalogue --profile "<profile name>"   # once per installation
```

The profile archive holds a consistent SQLite snapshot (`Connection.backup`)
and every `*.local.yaml` in the profile's config folder, including
`search.local.yaml` and `semantic.local.yaml`. `backup` does not migrate the
database, so it is safe to run with either build. **No key is copied**: the
DeepSeek key stays in `.env` or in your system environment variables; note
which one, nothing else is needed.

Check the archive lists the `.db` and the yaml files:
the command prints its contents, and the manifest inside says how to restore.

## Recovery

| Situation | What to do |
|---|---|
| Go back to the previous build, keep the data | Check out the previous build. It opens the migrated database without error (a ledger row beyond its files is read as a newer database, and the extra table is ignored; verified on a synthetic database). **Restore the settings files from the backup**: a `search.local.yaml` saved by this build carries keys the previous loader refuses ("search.local.yaml is not valid"). Then press Recalculate: scores written by this build are not current for the previous one. |
| Undo everything, including the database | Stop the app, unzip the profile backup, put the `.db` back at the profile's database path and the `*.local.yaml` files back in its config folder, check out the previous build, start. |
| The catalogue | This phase does not change its schema or its postings. Restore it only if something else went wrong: unzip the catalogue backup to `data/shared/catalogue.db` with the app stopped. |

Never restore over a running app, and keep the backup until a week of normal
use has passed on the new build.

## Markets and geography: where a remote search was narrowed

Traced 2026-10-08 from the profile to the screen, with six synthetic remote
postings for a candidate living in Brazil (`scratchpad` probe, not
committed; the unit test `test_what_is_left_to_confirm_...` keeps the rule).
Four facts stay apart: where she lives (`candidate_country`), where
employers may hire her from (`eligible_countries`, `eligible_scopes`), how
she works (`preferences.remote`), and where the posting allows (its own
text and fields). The company's headquarters is never read as eligibility.

| Posting | Matching | Shown by default, before | After |
|---|---|---|---|
| Remote, no country; HQ in Berlin | UNRESOLVED: does not say where it hires (HQ ignored) | hidden | shown, "To confirm: where they hire" |
| Remote, anywhere in the world | VERIFIED_ELIGIBLE | shown | shown |
| Remote, Europe or Latin America | VERIFIED_ELIGIBLE (Latin America holds Brazil) | shown | shown |
| Remote, must overlap CET hours | UNRESOLVED (a time zone is not a place) | hidden | shown, "To confirm: where they hire" |
| Remote, EU residents only | VERIFIED_NOT_ELIGIBLE (the employer's own limit) | hidden | hidden, one switch away |
| Remote worldwide, US work authorization stated | pending her answer (or her own blocker refuses it) | hidden, labelled "never said where it hires" | shown, "To confirm: permission to work" |

What restricted what:

- **Eligibility: correct, unchanged.** No improper restriction found.
- **Visibility: restricted, fixed.** The list hid every UNRESOLVED posting by
  default, which hid most remote work that does not name a country, and
  since phase 1 also hid postings pending only her answer, under a sentence
  that said the posting never said where it hires. Now shown by default; the
  card and the drawer say what is left to confirm, by the same rule that
  decides the status (`match.gates.pending_gates`); the drawer marks that
  gate "Left to confirm" instead of "Not in the ad", and the status reads
  "To confirm" (was "Did not say", untrue for a requirement she has not
  answered). Her stored choice to hide them wins, and a flag that is on by
  default is no longer written into the address. This reverses the V1.5
  default; on the demo corpus a match of 55+ with an open question is now
  listed (5 roles -> 6).
- **Discovery: narrowed in two places, not changed here.** (1) The refresh
  plan pauses a regional board of another market (`sources.scheduling`,
  from where she lives): for a candidate in Brazil that is Arbeitnow, the
  one operational European board. It is reversible per source in Settings
  & Sources, and an explicit choice there wins. Changing the rule is an
  architecture decision: ADR-0019's guard pins the scheduler's inputs to
  sources, countries and overrides. (2) The two targeted search lanes
  (Himalayas search, additive to its unrestricted walk; experimental
  LinkedIn) search only her country and the regions in `eligible_scopes`.
  Every other collector walks its whole feed whatever she configured.
- **Desired markets have no reader.** `regions.target` and
  `regions.opportunistic` are written by `setup` and read by nothing. Wiring
  them in (as the source of market priority instead of residence) is the
  owner decision the two discovery points above wait on.
