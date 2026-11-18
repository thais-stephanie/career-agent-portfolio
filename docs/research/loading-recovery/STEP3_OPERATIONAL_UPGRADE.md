# Step 3 operational upgrade: migrations 0062/0063 on the live install

Prepared on 2026-11-18, after Step 3 was published (`origin/main` = `8ec7263`).
**Not executed.** Publishing changed only GitHub. The live checkout and its
databases are unchanged: schema 61, local `main` = `fe8d946`.

## 0. Why a procedure

The published code needs migrations 0062 (catalogue) and 0063 (each profile).
`rescore._missing_or_stale` names their indexes with `INDEXED BY`, so on an
unmigrated database `rescore.plan` and `/api/home/outdated` raise "no such
index". Every product path migrates before planning:

- `serve` and a profile switch call `migrate`;
- the CLI opens through `_open_personal`;
- `rescore --no-migrate` is refused by `preflight` while any migration is pending.

So the normal start of the new code applies them by itself. This procedure
applies them deliberately instead, before the first start. That way the
migration time is not mixed with startup, and each step is checked.

Code rollback and schema rollback are different:

| Code | Schema | Safe? |
|---|---|---|
| new (`8ec7263`) | 63 | yes |
| old (`9636ecf` or earlier) | 63 | yes: the extra indexes are unused, and the old migration lists find nothing to do |
| old | 61 | yes: the state today |
| **new** | **61, or 62 without 63** | **no**: `plan` raises; the normal start migrates first, so only a hand-swapped catalogue gets here |

## 1. Blocker before anything: the checkout's branch

The live checkout runs the local `main` (`fe8d946`). That is Step 1c plus two
geography commits that were never published, and it has diverged from
`origin/main`. Installing the published code needs the owner's decision on
geography first (release review, section 11):

- **Geography approved:** integrate it on top of `8ec7263` in a disposable
  worktree, run the suites, publish, then move the local `main` there.
- **Geography not approved:** keep `fe8d946` on its own branch
  (`claude/geography-pending`), then point the local `main` at `origin/main`
  with `git reset --keep origin/main`. That needs explicit approval.

Do not run `git pull` on the divergent `main`.

## 2. Preconditions

- The owner is present. The app and every writer are closed:
  - quit the app (the window, or `POST /api/app/quit`);
  - confirm no process whose command line has `launch.py`, `desktop.py` or `career_agent`;
  - confirm port 8765 is free;
  - confirm no scheduled rescore or collection is due.
- Free disk: at least the size of one full backup plus 1 GB. Today that is
  about 16 GB of databases, and the indexes add about 43 MB.
- The checkout is at the code to install (section 1), and `uv sync --frozen` is done.

## 3. Backup (one, verified)

Make one verified full snapshot of the three databases in `data/backups/`,
with a `MANIFEST.json` in the existing format:

- the commit;
- the reason;
- per file: the sha256, `quick_check`, and the row counts of `job_match`,
  `job_score_revision`, `job_input_revision` and `schema_migration`.

Name it `pre-0062-<date>`. Keep only one full backup of the current state, as
the project rules say: delete an older redundant one only after this one is verified.

The product's own `career-agent backup --profile <name>` and
`career-agent backup --catalogue --profile <name>` make portable archives of
a profile and of the catalogue. Either form is acceptable if it is verified
before step 5.

## 4. Before the migration

Record, for each of `data/personal.db`, `data/profiles/<id>/personal.db` and
`data/shared/catalogue.db`:

- `PRAGMA quick_check` = ok;
- the highest `schema_migration` version, 61;
- the row counts above;
- the WAL size, 0.

Optionally, record the outdated count read-only with the old code (the
Phase 4 harness, `realout.py` code A), to compare it after the upgrade.

## 5. Apply, by the official path

```powershell
.venv\Scripts\python.exe -m career_agent.cli migrate --db data\personal.db
```

Expected output: `applied 0062_job_input_revision_cover` and
`applied 0063_job_score_revision_cover`. The catalogue is migrated on its own
connection first; then the profile records 0062 and applies 0063. The measured
time on a copy of this install was 3.35 s.

```powershell
.venv\Scripts\python.exe -m career_agent.cli migrate --db data\profiles\<id>\personal.db
```

Expected output: `applied 0063_job_score_revision_cover` only, because the
catalogue already has 0062. Running either command again prints `database is
already up to date`.

## 6. Validate

- Each ledger holds 62 and 63.
- `idx_job_input_revision_cover` exists in the catalogue only.
- `idx_job_score_revision_cover` exists in each profile only.
- `PRAGMA quick_check` = ok on all three.
- The row counts are equal to step 4.
- `EXPLAIN QUERY PLAN` of the receipts query shows both `COVERING INDEX` names.

## 7. Start and check the app

Start the app the usual way. If the last update was more than 6 hours ago,
opening the UI starts a collection: that is the product's normal behaviour,
and fine here.

- Home draws.
- The "scores to update" count appears and equals step 4's count, if recorded.
- Find jobs, Applications and the Resume Tailor open.
- Switching to the second profile works.
- The server log has no "no such index".

## 8. If something fails

- **The migration fails** (a `MigrationError`): each migration runs in one
  transaction with its ledger row, so nothing half-applied remains. Fix the
  cause (usually a lock: something was still open) and run it again.
- **The app misbehaves after the migration**, and the indexes are the cause.
  First choice: run the old code (`9636ecf`); it is safe on schema 63 (section 0).
  Second choice: restore the backup from step 3, with the old code.
- **Never** drop the indexes while the new code is installed. To remove them:
  install the old code first, then drop both indexes and delete the ledger
  rows 62 and 63 on each database. The upgrade test in
  `tests/integration/test_rescore_receipt_indexes.py` exercises exactly this
  path.
