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
profile's database, in the same transaction as the tracking data, and then
the files exported from those documents (`resume_exports/`, beside that
database). The shared job catalogue, other profiles, copies already
downloaded elsewhere and the Resume helper's own folder are not touched, and
the confirmation says so.

## Rendering and the live preview

`career_agent.resume_doc.render.render_html(document, mode=...)` is the one
renderer. The preview shows its output and the PDF export prints the same
document. There is no second copy of any template.

* **Semantic HTML with real text.** Header, sections, headings, paragraphs and
  lists, in one column. No tables, images or canvas for content.
* **Stable references.** Every rendered piece carries `data-ref`, the path to
  the object it shows (`experience/<id>/bullet/<id>`), never a position.
* **Safe by construction.** Every value is escaped; inline `**bold**` is the
  only markup a field can produce. The document carries a policy that forbids
  every network load, and in `preview` mode links are text, not followable.
* **Design is not content.** Templates (`clean`, `modern`, `compact`) are sets
  of values for one shared stylesheet. A4 and Letter are their physical sizes;
  margins, font family (Calibri/Arial or Cambria/Georgia), size, line height,
  spacing and accent come from the document's design, within its bounds.
* **Breaks.** Each keep-together unit is a `data-block`; a heading or entry
  header that must stay with what follows is also `data-keep`. Print CSS and
  the preview paginator read the same two attributes. The paginator decides
  where pages end, leaving 1 px of room at each page's foot, and an export
  prints its decisions (`break-before: page` on each page's first block):
  screen and print lay text out a fraction of a pixel apart, and without this
  a line that only just fits could land on different pages.
* **Preview.** `POST /api/resume/render` validates a whole document and
  renders it, saving nothing; the HTML is held briefly in memory and served by
  `GET /api/resume/preview/<token>` as its own page, under a policy with no
  script, no network and no forms, framed only by this app. The page draws it
  in a sandboxed frame, double-buffered so the visible resume is never blank,
  and paginates it into real pages; a block taller than a page is reported as
  overflow, never clipped.
* **Internal for now.** The preview lives on an internal page
  (`?debug=resume-v2`); the Resume helper is still the user's resume surface.

## The editor

The internal page now has Home, My resumes and the Editor. Tailor and
Analyze are not built and not shown.

* **One document object.** Every edit is a function over a copy of the
  document; the form, the preview and autosave all read that one object. The
  renderer is not repeated in the page.
* **Undo and redo.** Whole-document snapshots: typing in one field within
  500 ms is one step, at most 100 steps, a new edit clears redo, and redo
  after undo gives the edit back. Ctrl+Z and Ctrl+Shift+Z (or Ctrl+Y) work
  outside text fields; inside one, the field's own undo applies.
* **Autosave** writes the working copy 600 ms after the last edit, never a
  revision. One save at a time; edits made meanwhile go next as one copy;
  every save names the hash it edits, so a second window gets "changed in
  another window" with Reload and Keep my copy as a duplicate, and nothing is
  overwritten. A failed save keeps the work and retries, and a retried save
  whose first answer was lost is recognised as already saved. Edits the
  server would refuse (a required field left empty) are held in the page and
  shown as not saved: leaving, switching document and version points wait
  until they can be saved, or the person chooses to leave without saving.
  A version point is written on purpose: Save version point, a template change
  (the version before it is what is kept), leaving after edits.
* **Live preview** 120 ms after the last change, newest render wins. A click
  on the paper focuses the field it came from.
* **Provenance.** Rewording a line from evidence keeps its origin and
  evidence and records the wording it replaced; putting the old words back
  makes it untouched again. Hidden lines and sections stay in the document.
  A line typed here is the person's own and says so. "Add from my confirmed
  experience" copies a confirmed statement into this resume, citing it; it
  confirms nothing and writes no Career Evidence. Identity edits change this
  resume only.
* **Check** lists named facts about the document (no name, no email, a long
  line, an empty section, an edited evidence line, a number the evidence does
  not have, a line not from evidence, a block taller than a page). There is
  no score.
* **Design** chooses the template, page, font family, spacing, margins, text
  size, line height, accent and date format, all within the model's bounds.

## Evidence ids at the boundary

A line's `evidence_ids` are Career Evidence claim keys, and so is the
`claim_key` of a project, education or certification entry (the Master sets
it from a confirmed claim; it never holds another kind of id). One function,
`resume_doc.evidence.unconfirmed_lines`, asks whether each is a confirmed,
current (not superseded) claim of THIS profile's candidate, and `ResumeStore`
asks it on every acceptance: creating a document, saving its working copy and
checkpointing it. Every route, the Master and the legacy migration write
through the store, so none of them can skip it; export asks it too, before a
file is made. Restoring a revision is the one write that does not ask, on
purpose (below). A nonexistent, unconfirmed, retired or another profile's id
refuses the write with `evidence_not_confirmed` (`EvidenceNotConfirmed` in
the store) and the ids of the lines or entries concerned; nothing is saved.
Typed and imported lines may cite nothing. An edited evidence line keeps its
ids only while they hold.

Reading never asks. A revision whose evidence was retired later still loads,
as it was written. Restoring it puts that content back unchanged and trusts
nothing: the next save, checkpoint or export refuses the lines that no longer
hold, until the person decides about them.

A claim can be retired after a resume cited it. The editor then says which
lines are affected and offers to keep them as the person's own words (origin
`USER_AUTHORED`, no evidence; an entry loses its `claim_key`); nothing changes
until the person chooses that, and Career Evidence is never written.

## Export: PDF, DOCX and JSON

`resume_doc.export.export_revision` makes one file of exactly what the page
has saved, and `POST /api/resume/documents/<id>/exports` is its route.

* **One revision.** The request names the hash of the saved copy; a different
  copy is a 409 and nothing is made. The working copy is checkpointed with
  reason `EXPORTED` (a copy equal to the latest revision IS that revision), and
  the file is made from that immutable revision only. The `resume_export` row
  names the revision, format, template, engine, file hash, page count and the
  check result. Exporting changes no content.
* **Refused, not attempted,** without a real name (blank, "You" and the like),
  or while a line cites evidence that is not confirmed now.
* **PDF** prints the renderer's print HTML in headless Microsoft Edge (Chrome
  when Edge is absent): a throwaway profile in a temp directory, no window, no
  header or footer, a time limit, and every network
  address routed to a closed local port (the document's own policy forbids any
  load as well). Links stay links in the PDF; printing visits none of them.
  With no browser the export fails as "could not be made", never as checked.
  Cleanup: `resume.html` and `resume.pdf` (a name, contact details, the
  resume) are deleted on their own before the folder, which is removed with
  retries for up to 10 seconds. A print that times out or fails stops its
  browser: the launched process, and on Windows the detached Edge found by
  THIS export's temp folder in its command line (exact match, never a
  process name, so the person's own Edge and other exports are untouched).
  A browser still holding the folder after the retries is stopped the same
  way and the cleanup is tried once more. A folder that still cannot go is
  swept by a later export once it is an hour old (`career-agent-pdf-*` only).
* **DOCX** writes the same rendered blocks with real Word styles (Title,
  Heading 1, Heading 2, List Bullet, Normal), A4 or Letter, the document's
  margins, font and spacing; no tables, text boxes, shapes or icons. Links are
  their visible text. Word lays out its own pages, so its page count is not
  measured.
* **JSON** is the revision's ResumeDocument (schema version included,
  deterministic UTF-8) and reads back through `upgrade_resume_document`.
* **What each format holds.** JSON is the document: hidden lines and hidden
  sections are in it. PDF and DOCX are the visible resume: anything hidden is
  absent, and a visible line the person typed is present.
* **Files** go to the profile's private folder, `resume_exports/<document>/
  <export>.<ext>` beside its database; the row stores that relative path and a
  download (`GET /api/resume/exports/<id>/file`) resolves it there or not at
  all. The browser never names a path. The downloaded file is named after the
  person, `Full_Name_Resume.pdf`, plus the job title for a tailored version;
  characters no filesystem accepts become `_` and the length is bounded.

### Named checks, never a score

Every file is read back (pypdf for a PDF, python-docx for a DOCX) and the
result is a list of named checks (`resume_doc.ats`), each PASS, WARNING, FAIL
or NOT_MEASURED. Real applicant tracking systems differ, so there is no
percentage and no prediction. NOT_MEASURED is shown as such and never counted
as a pass; an export is "checked" only when no check failed. A file that was
made but failed a check is kept and says exactly what failed.

| Check | What it verifies |
|---|---|
| CONTACT | the name at the top; email and phone when the resume shows them |
| READING_ORDER | every visible paragraph, in the layout's order |
| HEADINGS | every visible section heading |
| DATES | every visible period, written with plain separators |
| GLYPHS | no replacement characters, `(cid:)` artifacts or private-use icons |
| ONLY_VISIBLE_TEXT | no word the visible resume does not say |
| HIDDEN_ABSENT | no hidden line |
| SUPPORTED_TERMS | lines linked to a job requirement keep their words (not measured until lines carry requirements) |
| PAGES | PDF pages equal the preview's (not measured for DOCX, or without the preview's count) |
| LAYOUT | no block taller than a page, no nearly empty last page, no heading at a page foot (PDF) |
| ROUND_TRIP | JSON reads back as the same revision |

In the editor, Download (PDF, DOCX, JSON) first finishes saving, refuses
edits that cannot be saved or a copy changed in another window, then shows
the checks. My resumes shows each document's last export.
