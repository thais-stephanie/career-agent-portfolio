# Resume Workspace: document and storage

Status: foundation, Master and migration bridge. Nothing on screen uses this
yet: the Resume helper keeps working from its own files, and stays the one
place a person edits a resume, until a later change moves it here. No AI is
involved anywhere below.

## One document model

`career_agent.resume_doc.models.ResumeDocument` (schema `1.0`) is the one
resume model. Building, importing, tailoring, editing, checking and exporting a
resume will all read and write it; there is no second document schema.
`schemas/resume-document.v1.json` is generated from the Python models by
`scripts/make_resume_schema.py`, and a test fails when the committed copy is
stale.

* Every item has a stable ULID. Positional ids are refused.
* Identity holds a name, contact details, a place and links. It has no photo,
  age, gender, marital status or nationality. An empty or placeholder name
  ("You") is stored as it is and reported by `Identity.name_finding`; it is
  never replaced by a default.
* A tailored document carries a `target` (the job's title and company). A
  role keeps two titles: `source_title`, as confirmed in Career Evidence, and
  `display_title`, how this resume presents it. Tailoring may propose the
  second and never changes the first.
* Text records where it came from. Evidence-derived text (verbatim, rule
  rewrite, AI rewrite) must name its evidence. Typed or imported text may
  stand alone, and saving it never turns it into Career Evidence.
* Dates are a year and an optional month, never a free string.
* Layout (order, hidden sections, headings) and design (template, page,
  typography) are kept apart from content, from closed vocabularies. A font is
  a family name, never a file path.
* Every stored document is read through `upgrade_resume_document`, which
  refuses a schema version it does not know.
* `canonical_json` (sorted keys, no whitespace) is the one form that is
  stored, with text exactly as typed. Hashes read text as Unicode NFC, so the
  same letters typed as one code point or as a letter plus an accent hash
  alike.

## Profile-private storage

Migration 0047 adds seven tables to each profile's own database: none reach
the shared job catalogue. `career_agent.resume_doc.store.ResumeStore` is the
only code that touches them.

* **Working copy and revisions.** `save_working_copy` is the cheap autosave
  and requires the hash the caller last read; a stale writer gets
  `StaleDocument` instead of overwriting. `checkpoint_revision` appends a
  milestone. Revisions are append-only (a trigger refuses updates), and
  restoring an old one appends a new revision.
* **Job ad snapshots** are immutable. Capturing identical content (text,
  title, company, job, URL, language) returns the snapshot already held; any
  difference is a new snapshot.
* **Versions.** Tailored documents for one job are numbered V1, V2, V3 inside
  the write transaction, with a uniqueness constraint as the backstop. The
  group is the Career Agent job, or the ad's text hash for a pasted ad. An
  archived version keeps its number. At most one version per job is
  preferred, enforced by a partial unique index.
* **Master.** A tailored document copies from a named master revision.
  Nothing in the store writes a master as a side effect of tailoring.
* **Tailoring runs, proposed changes, export metadata and dismissed
  findings** have storage now and no producer yet. A dismissal belongs to one
  document.
* Archiving is soft; there is no delete.

## The Master resume and identity

`career_agent.resume_doc.master`.

* **One Master per profile.** `get_or_create_master` returns the current
  Master, or makes one the first time; migration 0048 refuses a second
  current Master at the database level.
* **Made from confirmed evidence only.** Every confirmed experience becomes an
  entry (`source_title` and `display_title` both start as the confirmed
  title), every confirmed statement a bullet, verbatim, citing its claim key;
  confirmed skills and tools become one skills group, one item per name;
  confirmed certifications are listed by name. Nothing waiting for review,
  retired or archived is used, and nothing is summarised or rewritten. A
  confirmed fact with no clean typed place (an education sentence, a
  statement outside any experience) is left out and named in the notes.
* **Comprehensive, not tailored.** No search setting decides what it holds.
* **Changes are reported, never applied.** `evidence_changes` lists the
  statements and experiences that were added, changed or removed since the
  Master was made; the Master itself only changes when the person changes it.
* **Identity** is the Master's: name, email, phone, city, region, country and
  links. Its name is the person's own: a resume contact name, else Career
  Agent's display name, else blank. A placeholder such as "You" or the local
  profile's label is never taken as a name. Contact details come only from an
  explicit contact record, never from evidence text. `update_resume_identity`
  saves a change as a new revision, with the same stale-write check as every
  save; it writes no Career Evidence, search setting, score or eligibility.
* **API.** `GET /api/resume/master` (never creates), `POST /api/resume/master`
  (get or create) and `PATCH /api/resume/master/identity` (identity plus the
  hash it edits; 409 when stale).

## Migrating the old Resume helper

`career_agent.resume_doc.legacy` reads one Resume helper workspace into one
profile's database. It is not run automatically.

* **Backup first.** `backup_legacy_workspace` zips the whole workspace and
  checks every file in the archive. `migrate_legacy_workspace` refuses to
  start unless that backup still matches the workspace file for file.
* **Read-only.** No legacy file is written, moved or deleted.
* **Idempotent.** Every migrated row's id is derived from what it came from,
  so a second run writes nothing.
* **One profile's workspace.** A workspace that names another profile is
  refused before anything is written.
* **Evidence is re-checked.** A migrated line cites a claim only while this
  profile still has that claim confirmed; otherwise it is imported text.
* **Mapping.** The contact record's email, phone, place and links become the
  Master's identity. Its `name` field is the workspace's name (a profile
  label), so the person's name comes from the evidence bank or from Career
  Agent instead, never from it. The default
  base resume becomes the Master when the profile has none, and every other
  base resume an imported document. Each finished run becomes a job ad
  snapshot, a tailored version numbered per job in run order, and a
  tailoring run whose old analysis is kept for history and marked as legacy
  and untrusted. An edited draft becomes a second revision and the working
  copy, with its edits marked as the person's and the lines it hid kept,
  hidden. A draft edited in the old helper after it was migrated is
  reported, never applied. An exported
  file is recorded where it is, attached only to the one version whose file
  name it carries, and never marked as checked.
* **Failures are named.** Each unit (identity, one base resume, one run, one
  export) is one transaction, and nothing runs after a failed identity unit. A unit that cannot be read leaves nothing
  behind and is listed in the report with the reason; the rest migrate, and
  a later run completes it.

## Forgetting

`career-agent forget everything` also deletes every resume row in that
profile's database, in the same transaction as the tracking data. The shared
job catalogue, other profiles, files already exported and the Resume
helper's own folder are not touched, and the confirmation says so.
