# Local profiles

One installation, several people. The owner searches for herself; later she can create a profile for somebody else. One person's CV, evidence, preferences, Search Fit and applications never appear in, or influence, the other's.

Local profiles are **not accounts**. There is no login, no password, no PIN and no server. They separate application data inside this app. They are **not a security boundary**: anyone using the same operating-system account can read every file under `data/`.

## Ownership map

Classified from the schema (54 tables at migration 0043), not from table names. `storage/catalogue.py` holds the two lists, and a test fails if any table is in neither or both.

| Class | What | Where |
|---|---|---|
| **Shared public** (candidate-independent) | `job`, `job_raw`, `job_provider_payload`, `job_discovery_source` (sightings: board, URL, id, never a query), `company` and `source_board` (from the shipped `companies.yaml` and from public boards), `board_discovery_lead`, `source_slice_state` (public feed slices), the FTS index `job_search` with `search_index_map` and `search_index_state`, the input revision clock `compute_revision` with `job_input_revision`, the index refresh queue `job_dirty`, posting-side extraction (`fingerprint`, `evidence`, `fp_*`, `provider_observation`, `llm_call`: facts quoted from the posting, no candidate data), `data/cache/http` | `data/shared/catalogue.db` |
| **Profile private** | `database_identity`, `candidate`, `candidate_state` (source choices, including the experimental LinkedIn opt-in), `search_profile_version`, `verified_claim`, `cv_*`, `career_*`, `intake_*`, `job_match`, `job_score_revision`, `job_application` (saved, hidden, notes, status), `job_application_event`, `requirement_review`, `semantic_run`, `semantic_evaluation` (keyed by the intent digest), `job_retrieval_lane` (which of this person's queries found a posting), `job_enrichment` (its prompt carries the person's capability line), `pipeline_run` (a targeted run's stats count what this person's terms found), `config/*.local.yaml` (search intent, role anchors, semantic settings and budget), the Resume Tailor workspace | the profile's own database and folders |
| **Installation secret** | `.env` (the DeepSeek key, Jooble) and the Jooble quota ledger | the installation root; never in a profile, the catalogue or a backup |
| **Shipped config** | `config/*.yaml` tracked in git | `config/`, copied into each later profile's folder |

Two tables were **mixed** in stage 1 and are now split by what they carry. `job_retrieval_lane` only ever recorded a person's targeted queries, so all of it is private; the public fact "this posting exists and was last seen then" is `job.first_seen_at` and `job.last_seen_at`. `compute_revision` carried a public input clock and a population counter moved by both postings and scores: the catalogue keeps the clock and the posting-side counter, and each profile keeps its own score-side counter (`profile_revision`).

## Architecture (stage 1)

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
  - A switch builds a fresh `JobsApi` on the chosen profile's database and settings, after checking the identity, and swaps it in for the next request. Resume Tailor is rebuilt on that profile's workspace behind a switchable ASGI wrapper, with a profile bridge bound to that profile (see ARCHITECTURE.md, "One person, one profile"); the previous bridge refuses every call from then on.
  - A switch is refused while a collection, a scoring pass or a semantic run is writing, and the page reloads afterwards. So there is always exactly one writer per profile database, and no screen keeps the previous person's data in memory.
  - `.\Start-Career-Agent.cmd -ProfileName NAME` starts on a given profile. `career-agent profiles` lists them.
- **Safeguards around a switch:**
  - One lock serialises switching, deleting and every background run start. A switch retires the old app before checking it, so no collection, scoring or semantic run can begin on a profile being left.
  - The registry is updated before the swap, and any failure puts everything back.
  - Each served profile holds an operating-system lock on its database. A second Career Agent window cannot serve the same profile, and a crashed process never leaves the lock stale.
  - Every page sends the id of the profile it was drawn for. A tab left open across a switch is refused (409) and reloads, and other tabs are told to reload when one switches.
  - Resume Tailor is rebuilt on the new profile's workspace explicitly, and its environment is restored if that fails.
- **The command line** keeps `--db` and `--config-dir`. Every command that reads both (serve, rescore, semantic-match, import-job, enrich, daily, backup, the Himalayas and LinkedIn collectors) refuses a database stamped for a profile with another profile's settings folder, so one person's search intent can never score another's jobs. `career-agent backup --profile NAME` picks both.
- **A lost or corrupt registry** is never a lockout. A registry that cannot be parsed is a readable refusal. A missing one is rebuilt from the databases themselves: the original workspace keeps the id already stamped in it, and every profile folder comes back under its stored name.
- **Installation-wide state stays installation-wide:** the `.env` key file and the Jooble daily quota ledger are found from the installation root, never inside a profile's folder.
- **Creating a profile** gives an empty database, empty settings and an empty Tailor workspace. Nothing of any other profile is copied. The shipped employer boards are added to its database.
- **Deleting** is refused for the active profile and for the adopted original, and it requires typing the profile's name. The folder is moved to `data/profiles/.trash/`, not erased, and no other profile's files are touched.
- **Settings isolation.** Search intent, role anchors, semantic matching settings and budget, and the LinkedIn opt-in are per profile. The DeepSeek key stays installation-wide in `.env`.
- **Retrieval provenance** (`job_retrieval_lane`) lives in each profile's own database, so one person's search terms are never visible to another. It stays there in stage 2.
- **Backup** (`career-agent backup --profile NAME`) holds exactly one profile's database and private settings (stage 2 adds a separate catalogue backup, below). The manifest names the profile and states what is not included: other profiles, the Resume Tailor workspace (which has its own per-candidate backup) and credentials. No archive mixes two people.

## Stage 2: the shared public catalogue

```
data/shared/catalogue.db          public job data, one copy for the installation
data/personal.db                  the original profile: private state only
data/profiles/<id>/personal.db    every later profile: private state only
data/legacy/                      the pre-split database, kept as the rollback
```

- **How a profile reads it.** A split profile database carries a `catalogue_link` row naming the catalogue's id. `storage.db.connect` ATTACHes `data/shared/catalogue.db` as `catalogue` and checks that id; a missing catalogue, or another installation's, is a readable refusal. SQLite finds a table named without a schema in the profile first and then in the catalogue, so every existing query works unchanged and a join between a score and a posting crosses the files on its own. A database without a link (the demo, the tests, a profile not split yet) holds everything itself and behaves exactly as before.
- **Foreign keys across files.** SQLite cannot enforce a foreign key into another file, and refuses a write whose parent table is not in the same file. The split rebuilds the private tables that pointed at `job` or `job_raw` without those keys, keeping every column and row; `career-agent integrity` checks the references instead (`catalogue_reference`, next to the existing orphan checks). Postings are never deleted, so no cascade is lost.
- **Triggers stay in their file.** The triggers that advance the input clock and fill the index refresh queue live with the public tables in the catalogue. The triggers on `job_match` live in the profile and advance its own `profile_revision`. The serving cache key reads both.
- **Scoring is per profile.** Whether a score is current is decided from the profile's own receipts (`job_score_revision`) against the catalogue's input revisions, in every rescore mode (a split profile's `DIRTY` pass checks its own receipts too). One profile's pass clears the shared index refresh queue, never another profile's need to rescore; tests prove it. A person's own rescore requests (a published semantic finding, a forced or explicit rescore) go to their profile's `profile_request` table, never to the shared queue: they would reveal which postings that person evaluated and force every other profile to rescore.
- **Writes and locks.** One process serves one profile at a time, as in stage 1. Two windows serving two profiles may both write the catalogue: SQLite serialises every transaction, and on top of that every collection holds an operating-system lock on the catalogue (`data/shared/catalogue.db.collect.lock`) from its run's start to its finish, so two collections never interleave; the second is refused with a readable message. Scoring takes no such lock. A write that spans both files is atomic per file, not across them: public data is written first and private second, and both are idempotent, so a crash between them leaves a public posting with no private row yet, which the next pass completes. **Known cost of two windows at once:** every write transaction (`BEGIN IMMEDIATE`) takes the write lock of both files, so while two windows serve two profiles at the same time, a private save in one waits for the other's current catalogue transaction. That is seconds during a collection. A full search-index rebuild, which runs once after a migration or on request, can make it wait longer, up to the two-minute limit, after which the save fails with a readable error and can be repeated. One window, the ordinary case, is unaffected: it was always one writer. Taking only the profile's lock for private-only writes is a planned follow-up.
- **Targeted retrieval privacy.** A targeted search may add a public posting to the catalogue; every profile can then see that the posting exists. Which person's query found it (`job_retrieval_lane`), the run's statistics (`pipeline_run`) and the query text itself stay in the searching profile's database. A test runs a synthetic search from profile B and checks, byte for byte, that B's phrase appears in neither A's files nor the catalogue's.
- **Migrations.** From migration 0044 on, every migration declares `-- scope: profile` or `-- scope: catalogue`, and the runner refuses one that does not. A split profile runs its own scope and records the other as considered; it first brings the catalogue up to date on the catalogue's own connection. A one-file database runs every migration.
- **Backups.** `career-agent backup --profile NAME` holds one profile's database and private settings; for a split profile the manifest says the postings are not in it. `career-agent backup --catalogue --profile NAME` holds the shared catalogue and no profile's data; it refuses to archive a catalogue that contains a private table.
- **New profiles and fresh installations.** A profile created while the catalogue exists holds only private state and reads the catalogue. A fresh installation starts that way. An installation whose original profile still holds its own postings keeps the stage-1 layout until it is split.

### Cutover and rollback

`career-agent catalogue split --db data/personal.db` never changes the database it reads, refuses to run while Career Agent has the profile open, refuses a database not yet at this build's schema, and holds the source exclusively from before its copies until it is moved aside, so a command started meanwhile is refused rather than writing to the file being retired.

1. The database is copied twice with `VACUUM INTO`. One copy becomes the catalogue: every private table is dropped with `secure_delete` on and the file is compacted, so none of a person's rows survive in its free pages. The other becomes the profile: every public table is dropped, the foreign keys above are rebuilt, and the link is stamped.
2. Both files are compared with the source: row counts of every table; value digests of postings, texts, scores, applications, semantic findings, retrieval provenance, source choices, confirmed evidence and identity; `integrity_check` and `foreign_key_check` on both; and every private row that names a posting finding it in the catalogue.
3. Without `--execute` it stops there (a rehearsal) and removes the staging files. With `--execute` it moves the catalogue into `data/shared/`, the original to `data/legacy/personal.pre-catalogue-<time>.db` (a rename, never a copy or a delete) and the new profile to where the original was. Every move is recorded; if one fails (Windows refuses to rename a file another program has open), every file is put back. Nothing in the registry changes: the profile keeps its id, name and paths.

`career-agent catalogue rollback --legacy data/legacy/<file>` puts the original back and keeps the split profile beside it. **The bridge's removal condition:** once the owner has used the split installation and is satisfied, the file in `data/legacy/` can be deleted by hand. Nothing deletes it automatically.

### What stage 1 still covers

A profile created in stage 1, before any catalogue existed, holds its own postings. It keeps working exactly as before, isolated. Only the FIRST catalogue can be made from such a profile with `catalogue split`; merging a second profile's postings into an existing catalogue (deduplicating by the collectors' identity rules and re-pointing that profile's private rows) is not implemented yet and is refused rather than attempted.
