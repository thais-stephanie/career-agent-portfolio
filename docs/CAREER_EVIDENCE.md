# Career Evidence

How Career Agent turns a CV into evidence a person has confirmed, and what
each action on an import does. Written for Career Evidence V2
(migration `0039_career_evidence_structure.sql`).

Three rules hold everywhere below:

- **Reading confirms nothing.** An import produces suggestions. A suggestion
  becomes a `verified_claim` only when the person confirms it, one statement
  at a time. There is no "accept all" and no batch confirmation, in the
  interface, the API or the terminal. `career-agent cv-import` stores only
  through `--review`, which asks about each proposal with no default answer
  (accept, edit, reject or stop); the old `--accept-all` flag was removed.
- **Provenance is never rewritten.** Every suggestion keeps the line it came
  from, both as plain words (`evidence`) and exactly as written, Markdown and
  all (`source_text`, with `source_line`). Editing, moving, merging or
  splitting changes where a suggestion is organised and never those fields.
- **Nothing here reaches Search Fit or eligibility.** Neither reads a claim
  (`tests/integration/test_evidence_reach.py`). Resume Tailor keeps its own
  evidence store and nothing is synchronised with it.

## Reading a CV: structure before claims

`cv/markdown.py` classifies every line (heading and its level, list item,
text, rule, fence, code) and turns inline Markdown into plain words: `**bold**`,
`__bold__`, `*italic*`, `_italic_`, `[text](url)`, `` `code` ``, `> quote`.
Horizontal rules, fences and code blocks never become suggestions. Ordinary
punctuation is left alone: `5 * 3`, `snake_case`, `__init__.py`, `C#`, `R&D`.
Imported text is always shown as text and never rendered as HTML.

`cv/structure.py` reads the jobs before any claim:

| The document says | Read as |
| --- | --- |
| `### Teem` then `#### Business Operations / RevOps` | company Teem, role inside it |
| `### Fabrikam Cloud - Senior Solutions Consultant` | company and role (a title word decides which is which) |
| `Consultant, Company A, Jan 2020 - Dec 2021` | company, role and dates on one line |
| `Globex Logistics` alone, then `Operations Director` and a date line | one company, several roles (promotions) |
| `Mar 2022 - Present / Remote` under a job | the job's dates, and where |
| A heading with nothing that says company or role | left as a label, marked unresolved |

It never invents structure. A field it cannot read stays empty and the
review says so ("Career Agent could not read the role"). Lines in an
Experience section with no job above them stay "not placed in an
experience" rather than being given an empty, invented one. Headings are
matched in English and Portuguese, accents included ("Experiência
Profissional", "Formação Acadêmica").

Dates keep what was written (`period_text`) and the months when a month was
stated (`period_start`, `period_end`). A year-only span keeps its years for
ordering and never gains an invented January.

## The model

```
cv_import        one read of one document     archived_at, deleted_at
  cv_entry       one job it describes         company, role_title, dates, source lines
  cv_proposal    one suggestion               entry_key, text, evidence, source_text, source_line
verified_claim   one confirmed statement      employer and months from its job, evidence_ref
```

Company, then role, then dates, then evidence. A company may hold several
roles. Order comes from the dates: a role still held first, then by when each
ended, newest first; undated last, in document order. `display_order` is no
longer something a person types.

A confirmed claim carries its job's company and months, as the review showed
them and as the person corrected them.

## Reviewing: a career, not a wall

The review opens on a summary ("6 experiences found / 142 suggestions / 18
need attention") and one row per experience with company, role, dates, how
many wait and how many are confirmed. Inside one experience a person can:

- **Confirm**, **Edit** (confirm in their own words) or **Reject** a suggestion;
- **Move** suggestions to another experience, or to none;
- **Split** chosen suggestions into a new experience;
- **Merge** an experience into another (its suggestions and source lines move with it);
- **Correct** the company, role or dates, or **add** a missing experience;
- **Delete** a suggestion or an empty experience permanently;
- go to the **next item needing review** across the whole read.

Batch actions move, split, reject or delete. None of them confirms.

"Needs attention" is a suggestion still unanswered that is a duplicate of an
earlier one in the same read, sits under a job whose company, role or dates
could not be read, or is work not placed in any job.

## Archive, restore, delete

| Action | What happens | Reversible |
| --- | --- | --- |
| **Archive** a CV read or package | Every row stays. It leaves every queue and counter. It cannot be answered until restored. | Yes: **Restore** |
| **Delete** with nothing confirmed | The import, its jobs and every suggestion are removed. | No |
| **Delete** with some confirmed | Every unconfirmed suggestion is removed. The confirmed ones keep their rows, and the jobs they sit under, as the source their claims cite; the import is hidden everywhere else. | No |
| **Retire** a confirmed claim | A new revision with `verified = 0`, in Career Evidence. Unrelated to imports. | Yes: confirm it again |

Delete first asks the server for its plan and shows it: how many unanswered,
how many rejected, how many confirmed are kept. Nothing changes until the
person presses "Delete permanently". "I uploaded the wrong CV" is one Delete.

"Discard" on a CV read used to delete it and every suggestion, including the
rows its confirmed claims cited. It archives now.

## Claim states

A claim is a chain of revisions, and `verified` on the current revision does
not say everything. The whole chain does (`ClaimRepo.states`):

| State | Current revision | Any revision ever verified | Meaning |
| --- | --- | --- | --- |
| Confirmed | verified | yes | She stands behind it. |
| Retired | not verified | yes | She confirmed it once and withdrew it. |
| Draft | not verified | never | Recorded but never confirmed, such as a `verified: false` career fact. Live, and waiting for review. |

No schema change was needed: retiring is the only path that un-verifies a
confirmed claim, and editing a retired claim carries the retirement forward.

## One definition of "needs review"

`storage/review_counts.py`: a statement needs review when nobody has answered
it and it is live: a suggestion from a CV read that is neither archived nor
deleted, a suggestion from the intake package in force, or a draft claim.
`/api/firstrun` (Home and the setup), the Career Evidence summary,
`/api/cv/imports`, `career-agent status` and `career-agent evidence` all read
it from there, and `tests/integration/test_career_evidence_v2.py` checks that
they agree.

## Needs organizing

Live evidence with no experience yet: a confirmed claim, a draft claim, or an
unanswered or unsure suggestion from an import she is working on. Never a
rejected suggestion, a retired claim, anything from an archived or deleted
import, or a row kept only as the provenance of something confirmed. The
button's count and the list it opens use the same rule; retired and rejected
items are reached by asking for that state.

## Existing workspaces

Migration 0039 adds columns and one table; it rewrites no row. A CV read from
before it has no jobs: its suggestions appear under "Work not placed in an
experience", every answer stands, and confirmed claims stay confirmed with
their provenance. Reading the file again produces the structure.
