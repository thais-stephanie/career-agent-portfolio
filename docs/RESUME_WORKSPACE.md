# Resume Workspace: document and storage

Status: in use. **Resumes** in the sidebar is this workspace (Home, My
resumes, Editor). The previous Resume helper stays reachable as **Legacy
Resume Helper** under Settings, Backup and privacy, Advanced, as a fallback;
its resumes move here only when the person asks. No AI is involved anywhere
below, and nothing here tailors a resume yet.

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
profile's database. It is not run automatically: Resumes says when the
profile has old resumes, shows what would move (contact details, base
resumes, versions for a job, edited drafts, downloads) and that a backup comes
first, and moves them only on "Back up and move". The backup is a verified ZIP
beside the profile's database (`resume_helper_backups/`); without it nothing
moves. Once anything has moved, the old helper only shows its resumes: a
change made there is refused (`409 legacy_moved`) so one resume is never
edited in two places.

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
* **On the Resumes page.** The preview is part of the editor, reached from
  the sidebar with no address parameter.

## The editor

The Resumes page has Home, My resumes and the Editor. Tailor and
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

## Import: PDF and DOCX

`POST /api/resume/import/read` reads an uploaded PDF or DOCX and returns an
`ImportProposal` (`resume_doc.imports`): what the file appears to say, for
review. Nothing is stored by reading it. `POST /api/resume/import/save` saves
the REVIEWED proposal. Importing is not confirmation: imported text is
resume content, never Career Evidence, the candidate's identity, a search
setting or a score input.

* **Intake** (`resume_doc.intake`): only `.pdf` and `.docx`, and the bytes
  must be that container (a PDF starts with `%PDF-`; a DOCX is a ZIP with a
  WordprocessingML main part). Read in memory, never written to disk. Limits:
  10 MB, 20 PDF pages, 400 ZIP members, 20 MB per member, 40 MB in all and
  8 MB for the document body, as declared (the reader never inflates past a
  declared size), 200,000 characters kept (no further PDF page is read once
  that is reached). Refused with a reason the page words: a PDF needing a
  password, a damaged file, macros (`vbaProject`, a macro-enabled main part),
  a container that is not what its name says. Nothing in a file is run or
  fetched: no PDF JavaScript or actions, link targets read as text, DOCX
  external relationships never followed, embedded objects ignored (and said
  so in the report). A PDF with no text layer is `NO_TEXT`, shown as "We
  couldn't read text from this PDF" with the ways on (a DOCX, a PDF with
  selectable text, typing it); there is no OCR.
* **Lines**: PDF and DOCX become one line shape (`SourceLine`), so one parser
  reads both. A PDF line keeps pypdf's text and gains the font size, bold and
  position of the runs it was built from; a DOCX paragraph keeps its style
  (Title, Heading 1, lists), runs, list level and hyperlinks; table cells are
  read in reading order as source text, and their layout is not kept.
* **Parser** (`resume_doc.parse`), deterministic and conservative:
  * sections by the heading vocabulary the Career Evidence CV reader uses
    (English, Portuguese, Spanish) or by shape (a heading style, or short,
    mostly upper case and bold or larger than the body); "Curriculum vitae"
    is a document title, never a section or a name;
  * the top of the document gives the name (a prominent line that is
    nothing else; never "You"), email, phone as typed (Brazilian and other
    international forms), links (LinkedIn and GitHub by address, others as
    a portfolio; PDF link annotations give the whole address of a wrapped
    one) and a location the places resolver names (LOW when it is not beside
    an email or phone: it could be a company's office); "Resume of" is not
    part of a name, and a name is HIGH only when nothing else near the top
    could be one;
  * entries: a header block (role, organisation, dates) and its lines
    (bullets by list style, glyph or indent); a PDF's wrapped lines are
    joined back; bullet text is kept as written, never shortened or
    corrected;
  * dates: `2024`, `Jan 2024`, `01/2024`, `2024-01`, month names in the
    three languages (only at a word's start), "Present" / "atual" /
    "actualidad"; a year stays a year, an academic year (`2010-11`) is
    never November, and an open end stays open;
  * an entry ends where the next header with its own dates begins, so older
    roles without bullets stay separate; header parts that fit no field
    ("GPA 3.9") are kept as lines marked to check;
  * skills split on list punctuation only, never inside parentheses
    ("Revenue Operations" and "Excel (advanced, VBA)" stay one item);
    `Group: a, b` is a group;
  * text longer than a field holds is split between words, never dropped;
  * anything with no typed home (Languages, Awards, an unknown heading,
    text above the first section that is not the headline) is kept as
    "Other content we found", never dropped.
* **Confidence**: every value is HIGH, MEDIUM or LOW (how sure the reader
  is that this source text belongs to that field; never a percentage) with
  its source ("page 1, line 4" or "paragraph 7", and the text). A value is a
  substring of its source text, except a date (its `YYYY` / `YYYY-MM`
  reading) and a link (the target the file carries). LOW comes with what is
  in doubt and the alternatives: two lines that could be the name, a part
  that could be a place or the company, no role word to tell the role from
  the company, an unknown heading. Rules of thumb: contact details and
  bullets are HIGH; a role and company told apart by a role word, a location,
  a headline are MEDIUM.
* **Review** ("We found this. Check it before saving."): every value is an
  editable field with its confidence in words and a mark (never colour
  alone) and, when not HIGH, the line it came from; LOW fields say "Check
  this" and get focus first; "View extracted text" shows every line read.
  Entries, lines, groups and other sections can be removed or added to.
* **Save**: the server builds the ResumeDocument from the reviewed VALUES
  only (every line `origin=IMPORTED`, no evidence ids, `created_from=IMPORT`,
  `import_id` naming the parser version, format and read), validates it like
  any write and stores it with an `IMPORTED` revision. Missing required
  fields are named, never filled in. Destinations: an imported resume, or
  the Master when there is none; with a Master, "make this my new Master"
  archives the current one (kept whole, with its history) in the same
  transaction, so there is never a second current Master. The document id
  is drawn when the file is read: saving the same proposal again (a lost
  response, a double click) returns the document already saved, and reading
  the file again is a second import.
* **Career Evidence, separately**: after saving, "Propose these details for
  Proof of my work" hands the same file (kept in the page's memory only) to
  the existing CV reading, which stages suggestions to review one by one and
  confirms nothing.
* **Profiles**: a read keeps no server-side state (no token to reuse); the
  saved document lives in the active profile's database like any other.
* **Not carried back**: bold emphasis inside a line (the text is kept).

## My resumes and versions

My resumes is one request (`GET /api/resume/documents`, `?archived=1` for
the archived ones), answered from one summary query: no document body is
read for the list, and no export is looked up one document at a time. The
server groups it: the Master, the imported and other standalone resumes, and
each job's versions, newest first, numbered by the store
(`job:<job id>`, or `jd:<ad hash>` for a pasted ad). The page draws the
groups it is given; it never numbers or groups anything itself.

* **One act per call** (`PATCH /api/resume/documents/<id>`): rename (the
  document's own name; the job it is for, its source titles and every line
  stay), archive, unarchive, prefer (one preferred version per job, enforced
  by the database), make Master.
* **Archive, never delete.** Revisions are append-only and kept by trigger,
  so there is no permanent delete. Archiving hides a resume from the list and
  keeps its history, downloads and any application that names it. The
  current Master cannot be archived from the list.
* **Make Master** is one transaction: the current Master is archived (never
  deleted). An archived Master simply comes back; any other resume is copied
  into a new Master (a document's kind never changes) and is itself
  archived. Both keep their whole history, and there is never a second
  current Master.
* **Duplicate** (`POST .../copy`) is a new document with its own first
  revision and the same content, line ids included. A copy of the Master is
  a draft; a copy of a job's version is that job's next version ("Make
  another version"), a copy to edit differently, not a new tailoring.
* **History** (`GET .../history`) lists the milestones (created, imported,
  version points, before a template change, restored), never autosaves.
  **Restore** (`POST .../restore`) writes the old content as a NEW revision;
  nothing earlier is rewritten. Lines citing evidence that is no longer
  confirmed are returned (`unconfirmed`) and shown in the editor, where the
  person keeps them as their own words or changes them; the next save refuses
  them until then.
* **Compare** (`GET /api/resume/compare?a=&b=`) reads two versions of the
  same job and lists what was added, removed, changed, hidden or shown:
  headline, summary, entries, display titles, lines, skills, hidden sections
  and section order. It writes nothing.
* **Downloads.** Each resume's history lists its downloads with format,
  date, checks and pages; "Download again" only while the private file is
  still there, otherwise "File no longer available". No path is ever sent.

## A version for a job, by hand

Until Tailor V2 exists, "Create a version for this job" (job drawer, Before
you apply) keeps the job ad as an immutable snapshot, copies the Master's
current revision exactly (`created_from: MASTER_COPY`, with that revision's
id) and opens it in the editor. Nothing is chosen, rewritten or added, and
the words on screen say so. Without a Master, the step says to make one
first.

The person can say which resume they used for an application ("I used this
one", `POST /api/resume/jobs/<id>/used`): migration 0049 keeps the job, the
document and the exact revision. A download is never taken to mean a resume
was sent.

## Tailor V2: deterministic, from the Master

"Tailor from Master" (job drawer, or a pasted ad on the Resumes home) builds a
new version for one job from the Master and confirmed Career Evidence only.
No model and no provider is involved: the run records `mode=DETERMINISTIC`
with no provider or model, and the screen says "Built from your confirmed
experience without AI". It sits beside "Create a version for this job" (a copy
made by hand), and a manual version is never labelled Tailored.

* **Inputs, fixed first.** The ad is captured as an immutable snapshot (text,
  title, company, URL, language, hash). The Master's exact revision is
  captured (checkpointed only if it has edits since its last milestone), and
  the version records it; a later Master edit never moves a version.
* **Requirements quote the ad** (`resume_doc/jd.py`). The ad is cut into items
  on lines, bullets, semicolons and sentences, with headings in EN/PT/ES, a
  heading written inline ("Requisitos: ..."), and lines wrapped at a fixed
  width joined back. Each requirement is a span of the snapshot (its
  `source_quote` is found in it, always), with a kind (responsibility, skill,
  tool, experience, education, certification, language, location, work
  authorization, schedule, other), a hardness (REQUIRED only on an explicit
  cue or under a requirements heading; PREFERRED on a nice-to-have cue or
  heading; otherwise UNKNOWN) and an importance (required 3, responsibility
  2, other 1, plus 1 when asked twice). Clearly identical asks merge, keeping
  both quotes; different products of one vendor do not.
* **Support** (`resume_doc/tailor.py`): each requirement is SHOWN_IN_MASTER,
  HAVE_EVIDENCE_NOT_SHOWN or NO_EVIDENCE against confirmed claims of this
  profile and the Master's lines and skills. A named tool or product in the
  ask must be named by the source itself ("CRM" never answers "Salesforce");
  otherwise two shared words (after a small, documented bilingual concept
  table) are needed. Location, work authorization and schedule are
  eligibility: reported apart, never covered or gapped by a resume. Years of
  experience are never judged: such an ask is at most "partly covered".
* **Strategy, then draft.** Confirmed lines the Master does not show are added
  to their own role, verbatim or with a leading first-person pronoun dropped
  (`RULE_REWRITE`), each citing its evidence and the requirement it answers;
  hidden Master lines that answer an ask are shown; within a role, relevant
  lines come first; roles keep their chronology; a role with more than six
  shown lines hides the ones that answer nothing. Headline, summary, titles,
  employers, dates, education and certifications are the Master's.
* **An independent review** checks every changed line against its own
  evidence: grounding, numbers, named terms and words (a set difference: no
  number, tool or word the evidence lacks), and the Master's titles,
  employers, dates and role order. A FAIL saves nothing. Validation then asks
  again whether every cited claim is still confirmed, whether every quote is
  in the ad and whether the Master moved; all of it runs in one write
  transaction, so evidence retired mid-run saves nothing either.
* **Stored**: the TAILORED document (revision `GENERATED`, the next number in
  the job's group, never made preferred on its own), the run with every
  stage's output and timings, and each change as an accepted RULE change.
* **The job panel** (Editor): what the ad asks with Covered / Partly covered /
  Not found, a count ("5 of 10 asks have confirmed support", never a score),
  why the version changed (emphasized, added from confirmed experience with
  its source, hidden), "Before you apply" ("I couldn't find these in your
  confirmed experience"), and Make it better, recomputed on the version as it
  is: a confirmed line or skill to add, a hidden line to show (Apply, an
  ordinary undoable edit), a gap (Add evidence in Proof of my work, never
  Apply), a long line (Edit). Dismiss sets one aside for that version only.
* **Invariants, tested**: the Master, Career Evidence, Search Fit scores,
  search preferences and other profiles are unchanged by tailoring.

Measured on synthetic profiles: about 10 ms (thin), 80 ms (a five-role
senior profile) and 650 ms (fifteen roles, 150 statements, a long ad).

## Tailor with AI: a drafter on top of Tailor V2

"Tailor with AI" (job drawer) is Tailor V2 with one more stage: an AI
provider PROPOSES wording, and Career Agent decides whether to offer it. It
is optional and never runs on its own: opening a job, a resume or a tab sends
nothing. The deterministic path stays beside it, unchanged.

* **The provider is Career Agent's.** Settings, AI & Semantic Matching
  (`config/semantic.local.yaml`, the key in the root `.env`) chooses it through
  `semantic.routing.resolve`; each provider gained one `complete(system, user,
  schema)` method over the client it already had. There is no resume-specific
  key, model or setting. Demo mode sends nothing; with no provider the screen
  says so and offers "Set up AI in Settings" and "Tailor without AI".
* **Said before it is sent.** The screen names the provider and model, how it
  bills, what is sent and what never is, and that it is ONE request. Send is
  the person's act. A metered provider is held to the AI budget per run set
  in Settings (the answer priced at its longest); a price nobody recorded is
  never taken as free, and nothing is sent. A failure (sign-in, limit,
  unreachable, unreadable answer) is said in plain words; Try again is a new,
  explicit request. There is no automatic retry. Cancel stops the request
  before the provider is asked whenever it can; a late answer is kept nowhere.
* **Built on the deterministic run** (`resume_doc/drafter.py`). Snapshot,
  requirements, retrieval, strategy and the deterministic draft run exactly as
  in Tailor V2; that draft is the base. Sent to the provider: the job's title
  and company, the quoted asks this profile has support for (never a gap,
  never an eligibility ask), the base's headline and summary when they rest on
  confirmed evidence (a typed one may hold anything), the lines
  relevant to those asks under their role's title, and the confirmed
  statements retrieved for them. Never contact details, dates, other jobs,
  applications, Search Fit or settings. The ad's text is never sent whole, and
  the prompt tells the model that job text is untrusted data, never
  instructions. A request is 2 to 5 thousand characters.
* **A patch, never a resume.** The answer must be `{"changes": [...]}`, at most
  12, each one of four operations: REWRITE_HEADLINE, REWRITE_SUMMARY,
  REWRITE_BULLET (a line of the base) or ADD_BULLET (under a role), with
  `proposed_text`, `evidence_ids`, `requirement_ids` and a short `reason`. Any
  other field, operation, size or shape fails the whole answer; the only
  repair is removing a Markdown code fence. No operation can reach identity,
  employers, titles, dates, education, certifications, the job, the Master or
  Career Evidence.
* **Checked, never believed** (`drafter.check`, with `tailor.grounding`, the
  reader the deterministic reviewer also uses). Every requirement id must be
  one of this snapshot's that was sent; every evidence id a confirmed, current
  claim of THIS profile that was sent or that the line already cites, and a
  role's line rests on that role's statements only. The wording may hold no
  number (with its unit: "30%" is not "30x"), no tool or name the ad asks for
  or Career Agent knows (in any case), no word of result, scale or quantity
  ("award-winning", "worldwide", "millions", "forty", "a decade"), no word of
  rank or leadership in English or Portuguese unless a source says it in the
  same place ("lead routing" is not leadership; "owned the reporting" is not
  "owned 4 teams") or the role's own title does (for a headline or a summary,
  the current role's), and a number only with what
  it counts ("4 regional teams" is not "4 countries", "4 years" or "4 regional
  systems"; "entry by 30%" is not "30% of teams"). A word the sources lack,
  compared as a whole word of any length, may only be a joining word or one of
  a short, closed list of neutral verbs and work nouns ("automated",
  "configured", "rules", "padronizei"), at most two (four in a summary): a
  result ("improved"), a tool nobody listed ("ai", "snowflake") or a new rank
  is simply not on it. A line
  reworded by AI supports an ask only through what its evidence says, so
  wording never turns a gap into coverage. A proposal that fails is not
  offered: the person sees only how many were left out, and the AI's one-line
  reason is dropped if it holds a link, an address or a number.
* **Reviewed, one by one.** Each change shows Before, After, Why, its source
  and the job's ask, with Accept, Edit and Reject. An edit is held to the same
  checks; wording Career Agent cannot verify is refused, and can still be
  typed in the Editor later, as the person's own line. A decision can be
  changed while the review is open. The decisions are the run's
  `tailoring_change` rows (source DRAFTER).
* **No version until the end.** The run is anchored on the Master
  (`tailoring_run.document_id` is NOT NULL and the version does not exist
  yet): RUNNING while the provider works, PENDING during the review, ERROR when
  cancelled, discarded, stale or failed, DONE once "Create version" runs every
  check again (evidence now, the Master unmoved, the deterministic reviewer)
  and creates the TAILORED document from the Master revision, the
  deterministic plan and the accepted changes. Only then is a version number
  taken, so a cancelled attempt leaves no gap. If the Master changes while the
  provider works, its answer is not used. Accepted wording is `AI_REWRITE`
  with its evidence, its requirements and the original text kept.
* **Recorded without the prompt**: provider, model, the prompt template's
  version and digest, the token counts the provider reported, and every
  stage's output. Neither the prompt nor any credential is stored or logged.
* **Labelled, not ranked.** My resumes and the job drawer say "AI-assisted";
  the job panel says the version was drafted with AI and reviewed by the
  person.

Measured locally with a fake provider (the provider's own time excluded):
preparing about 10 to 20 ms, checking the answer 3 to 6 ms, the decisions
4 to 14 ms, creating the version 6 to 22 ms.
