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
