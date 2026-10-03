# Resume Workspace: document and storage

Status: foundation only. Nothing on screen uses this yet; the Resume helper
keeps its own files until a later change moves it here.

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
* `canonical_json` (sorted keys, no whitespace) is the one form that is stored
  and hashed.

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
