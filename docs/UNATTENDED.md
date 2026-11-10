# Unattended use: `rescore --no-migrate` and `export-jobs`

Two commands for a program that runs Career Agent's work while the app is closed, such
as a scheduled task. Both read or score with the same code the app uses. Neither applies
migrations, calls a model, or reaches the network.

## `career-agent rescore --no-migrate`

The ordinary rescore, behind the checks an unattended caller needs. Without the flag,
`rescore` behaves exactly as before (it still migrates on open, for a person at the
terminal).

- Refuses when migrations are pending, when the database is not personal, or when it is
  missing. It never applies a migration and never creates a file.
- Holds the maintenance lock (`<db>.maintenance.lock`) while it scores, so it cannot
  overlap `refresh` or the app's own update. Refuses while another process is scoring
  (`rescore_in_progress`).
- Prints exactly one JSON object.

| Exit | `status` | Meaning |
|---|---|---|
| 0 | `FINISHED` | The pass ended. `stats.errors` counts postings that failed one by one |
| 1 | `ERROR` | Something else failed; `reason` is the exception type |
| 2 | `REFUSED` | `reason`: `MIGRATIONS_PENDING`, `NOT_PERSONAL`, `NO_DATABASE`, `MIGRATION_LINEAGE`, `CONFIG` |
| 3 | `BUSY` | `reason`: `MAINTENANCE_RUNNING` or `RESCORE_RUNNING` |
| 130 | `INTERRUPTED` | Stopped before the end. The rescore's own heartbeat marks the run as interrupted |

All the mode flags work as before (`--dirty`, `--force`, `--provider`, ...). `--plan`
does not need `--no-migrate` and refuses it.

## `career-agent export-jobs --out FILE`

The Jobs list, exactly as `GET /api/jobs` returns it, written to a versioned JSON file
(`career_agent/jobs_export.py`).

- **Same answer as the screen.** Every item comes from `JobsApi.list_jobs` on read-only
  (`mode=ro`) connections. Nothing is scored or ranked here.
- **Read-only.** A stray write fails. A reader of a WAL database may leave empty `-wal`
  and `-shm` sidecars; the database file itself is unchanged (tested by hash).
- **Complete or absent.** Items stream to `.<name>.<pid>.partial` beside the target and
  are renamed into place after the last page. An interrupted export leaves the previous
  file untouched. If the list moved between pages (`freshness` or `total` changed), the
  export is refused with exit 4 and nothing is written.
- **Bounded memory.** One page (500 cards) at a time: 20,000 synthetic cards exported in
  2.7 s with a 3.9 MB peak (`test_large_export_streams_in_bounded_memory`).
- **Private fields stay out**: the person's notes and the advert excerpt.

Scope: the default list (`lane=shortlist`, deep order), plus every posting whose status
is `SHORTLISTED`, `TO_APPLY`, `APPLIED`, `INTERVIEW` or `OFFER` even when the list hides
it. `--include-unresolved` adds postings that do not say where they hire; on a large
database that population is slow to count, so it is off by default. A posting the person
hid stays hidden.

| Exit | `status` | Meaning |
|---|---|---|
| 0 | `WRITTEN` | The file is complete; the summary below is printed |
| 1 | `ERROR` | Anything else |
| 2 | `REFUSED` | Missing, not personal, migrations pending, or the profile not proven (`PROFILE_*`, `WRONG_PROFILE_*`) |
| 4 | `REFUSED` | `LIST_MOVED` or `INCOMPLETE`: run again |

### File format, schema 2

```text
format            "career-agent-jobs-export"
schema_version    2
items             [card, ...]   job_card fields + caution, availability, deep, readable,
                                 export_scope ("default_list" | "tracked"); no notes, no excerpt
generated_at      ISO 8601, UTC
career_agent      {version, install}            install = install_id(), no path
database          {mode: "PERSONAL", freshness, profile_id}
scoring           {serving, is_current, rescoring, authority}
scope             {lane, sort, include_unresolved, tracked_statuses, excluded, excluded_fields}
completeness      {complete: true, default_list_total, default_list_count,
                   tracked_extra_count, item_count}
```

`items` comes first and the header after it, so the file can be written in one pass. A
reader must check `format`, `schema_version` and `completeness.complete` before trusting
the items. A new field is not a new schema; a removed or renamed one is, and so is a
field a reader must check before trusting the rows.

### Which profile (schema 2)

`database.profile_id` is the local profile the exported rows belong to: `prof-<ULID>`,
opaque, never the profile's name. It is read from the database's own stamp
(`database_identity.profile_id`), not from the profile the app has active, and the export
is refused unless the registry lists that profile with this database file and this
`--config-dir` as its own (`PROFILE_UNBOUND`, `PROFILE_UNKNOWN`, `PROFILE_UNVERIFIED`,
`PROFILE_REGISTRY`, `WRONG_PROFILE_DATABASE`, `WRONG_PROFILE_SETTINGS`). It is `null` only
in an installation without local profiles. `install` names the program folder and is the
same for every profile, so a reader keeping one person's data must check both.

Schema 1 had no `profile_id`; it was bumped to 2 so a schema-1 reader refuses the file
instead of silently treating two profiles as one. The `GET /api/*` responses do not say
which profile they serve (`/api/app` deliberately names none), so they cannot prove it.

`scoring.is_current` false means the scores answer the previous preferences (for the
minutes after a preference edit; see `storage.revisions`).
