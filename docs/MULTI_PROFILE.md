# Local profiles

One installation, several people. The owner searches for herself; later she can create a profile for somebody else. One person's CV, evidence, preferences, Search Fit and applications never appear in, or influence, the other's.

Local profiles are **not accounts**. There is no login, no password, no PIN and no server. They separate application data inside this app. They are **not a security boundary**: anyone using the same operating-system account can read every file under `data/`.

## Ownership map

Measured on 2026-09-25 against a real workspace (6.4 GB, 154,416 postings, 42 migrations).

| Class | What |
|---|---|
| **Shared public** (candidate-independent) | `company`, `source_board`, `job`, `job_raw`, `job_provider_payload`, `job_discovery_source`, `board_discovery_lead`, `source_slice_state`, the FTS index `job_search`, `job_input_revision`, `pipeline_run`, job-side extraction tables, `data/cache/http` |
| **Profile private** | `candidate`, `candidate_state` (source choices, including the experimental LinkedIn opt-in), `search_profile_version`, `verified_claim`, `cv_import`, `cv_proposal`, `cv_entry`, `career_*`, `intake_*`, `job_match`, `job_score_revision`, `job_application` (saved, hidden, notes, status), `job_application_event`, `requirement_review`, `semantic_run`, `semantic_evaluation` (keyed by the intent digest), `config/*.local.yaml` (search intent, role anchors, semantic settings and budget, profile facts), the Resume Tailor workspace, backups |
| **Mixed** | `job_retrieval_lane`: the posting is public, but "found by my query Y" reveals intent and is private. `compute_revision` and `job_dirty`: shared input revisions next to a single-consumer scoring queue. |
| **Installation secret** | `.env` (the DeepSeek key, Jooble). Never copied into a profile or a backup. |
| **Shipped config** | `config/*.yaml` tracked in git |

## Architecture (stage 1, this release)

```
data/profiles.json                 registry: ids, names, which one is active
data/personal.db                   the original profile, adopted where it was
config/                            its private settings (and the shipped files)
data/tailor-personal/              its Resume Tailor workspace
data/profiles/<id>/personal.db     every later profile's database
data/profiles/<id>/config/         its private settings, plus copies of the
                                   shipped files refreshed from config/
data/profiles/<id>/tailor/         its Resume Tailor workspace
data/profiles/.trash/              deleted profiles, moved, not erased
```

- **Identity.** Each profile has an immutable id (`prof-<ULID>`) separate from its renameable label. Migration 0043 adds `database_identity.profile_id`. A profile stamps its id into its database on first open, and a database stamped for one profile is refused when opened as another (`ProfileMismatch`).
- **Adoption, not migration.** On its first start the launcher registers the existing workspace as the first profile, "My profile", pointing at the files exactly where they are. Nothing is moved, copied or rewritten. The only change to the database is the new, empty `profile_id` column and then the id in that one row. A copy of the real workspace was adopted first, and every table's row count was compared before and after.
- **Switching in the one running app.** The launcher runs Career Agent and Resume Tailor in one process.
  - A switch builds a fresh `JobsApi` on the chosen profile's database and settings, after checking the identity, and swaps it in for the next request. Resume Tailor is rebuilt on that profile's workspace behind a switchable ASGI wrapper.
  - A switch is refused while a collection, a scoring pass or a semantic run is writing, and the page reloads afterwards. So there is always exactly one writer per profile database, and no screen keeps the previous person's data in memory.
  - `Start-Career-Agent.ps1 -ProfileName NAME` starts on a given profile. `career-agent profiles` lists them.
- **Safeguards around a switch:**
  - One lock serialises switching, deleting and every background run start. A switch retires the old app before checking it, so no collection, scoring or semantic run can begin on a profile being left.
  - The registry is updated before the swap, and any failure puts everything back.
  - Each served profile holds an operating-system lock on its database. A second Career Agent window cannot serve the same profile, and a crashed process never leaves the lock stale.
  - Every page sends the id of the profile it was drawn for. A tab left open across a switch is refused (409) and reloads, and other tabs are told to reload when one switches.
  - Resume Tailor is rebuilt on the new profile's workspace explicitly, and its environment is restored if that fails.
- **The command line** keeps `--db` and `--config-dir`. A database stamped for a profile is refused with another profile's settings folder, so one person's search intent can never score another's jobs. `career-agent backup --profile NAME` picks both.
- **A lost or corrupt registry** is never a lockout. A registry that cannot be parsed is a readable refusal. A missing one is rebuilt from the databases themselves: the original workspace keeps the id already stamped in it, and every profile folder comes back under its stored name.
- **Installation-wide state stays installation-wide:** the `.env` key file and the Jooble daily quota ledger are found from the installation root, never inside a profile's folder.
- **Creating a profile** gives an empty database, empty settings and an empty Tailor workspace. Nothing of any other profile is copied. The shipped employer boards are added to its database.
- **Deleting** is refused for the active profile and for the adopted original, and it requires typing the profile's name. The folder is moved to `data/profiles/.trash/`, not erased, and no other profile's files are touched.
- **Settings isolation.** Search intent, role anchors, semantic matching settings and budget, and the LinkedIn opt-in are per profile. The DeepSeek key stays installation-wide in `.env`.
- **Retrieval provenance** (`job_retrieval_lane`) lives in each profile's own database, so one person's search terms are never visible to another.
- **Backup** (`career-agent backup --profile NAME`) holds exactly one profile's database and private settings. The manifest names the profile and states what is not included: other profiles, the Resume Tailor workspace (which has its own per-candidate backup) and credentials. No archive mixes two people.

## The bridge, and stage 2 (shared public catalogue)

**Today each profile's database also holds the job postings that profile collected.** A new profile starts with an empty job list and fills it with its own "Find jobs". This is a documented temporary bridge. It keeps every query and every foreign key working unchanged, and no private data can leak through it, because nothing is shared. Its cost is disk space and repeated collection per profile.

The target is one shared public catalogue plus private per-profile state:

```
data/shared/catalogue.db   job, company, source_board, job_raw, payloads, FTS
data/profiles/<id>/profile.db   everything in the private column above
```

Each connection would open the profile database as `main` and ATTACH the catalogue. It is staged separately because the extraction needs all of the following, each measured before it is trusted:

1. Rebuilding the private tables whose foreign keys point at shared tables (`job_match`, `job_application`, `job_score_revision`, `requirement_review`, `job_retrieval_lane`). SQLite does not enforce foreign keys across attached databases and rejects writes whose parent table is missing, so integrity moves to `storage/integrity.py` checks.
2. Splitting `compute_revision` into a shared input revision and a per-profile population revision. Migration 0035's triggers on `job_match` must stay private, because triggers cannot reach another schema.
3. Replacing the single-consumer `job_dirty` queue with a per-profile comparison of `job_input_revision` (shared) against `job_score_revision` (private). One profile's rescore must never clear marks another still needs.
4. Splitting `job_retrieval_lane` into a public sighting and a private "my query found it" row.
5. Writing new migrations per schema, running `PRAGMA ... journal_mode=WAL` per attached file, and making backups schema-aware. A write spanning both files is atomic per file, not across files, so every write goes shared first, private second, and stays idempotent.
6. Re-measuring the Discover query across the ATTACH boundary. It is tuned for one file: 23 s against 0.3 s depending on the plan.

Removal path for the bridge: once the catalogue exists, each profile database's public tables are merged into it (deduplicated by the same identity rules collection uses) and then dropped from the profile database.
