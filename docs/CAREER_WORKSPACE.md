# Career Workspace: information architecture and audit

The career side of Career Agent, reorganised around how a person thinks about
their career rather than around how evidence is stored. The approved visual
and interaction reference is an external prototype (a local design file, not
part of this repository); this repository stays the source of truth for
evidence integrity, provenance, persistence, safety, eligibility and Search
Fit. Where the two disagree, the product rule wins and the copy says what the
product actually does.

## Target architecture

| Area | Holds | Owns |
| --- | --- | --- |
| Career Profile | Overview, Experience, Skills, Preferences | the canonical professional experiences (`career_experience`) |
| Evidence | Projects, Achievements, Certifications | proof: confirmed statements of those types, each with its source |
| Documents | CV reads and document packages, their review and history | imports: archive, restore, delete, resume a review |
| Settings & Sources | search preferences, eligibility, job sources | unchanged |

One representation of an experience: `career_experience`. The Profile's
Experience tab reads and edits it; imports are reconciled against it;
Evidence links to it. The statements behind an experience stay in
`verified_claim`, `cv_proposal` and `intake_claim`, with every revision and
every source line, exactly as before.

## Audit: prototype against the current product

| Prototype surface | Current surface | Current data / API | Current product rule | Reuse as-is? | UX gap | Decision |
| --- | --- | --- | --- | --- | --- | --- |
| Career Profile header: "Import resume", "Edit profile" | Profile page with tabs, no actions | `/api/profile`, `/api/evidence` | Reading a CV confirms nothing | Tabs yes | No way into import or editing from the profile | Header actions: Import resume (to Documents' guided import) and Edit profile (edit mode on Experience) |
| Overview: identity card with completeness %, top-skill bars | Overview of counts (roles, skills, qualifications) | confirmed claims | No completeness percentage (a bar over a career invents a denominator) | Partly | Looks like an admin summary | Keep counts, no %, no skill scores; cards in the prototype's style |
| Experience cards: role, company, dates chip, description, highlights, skills | Experience tab built from confirmed claims grouped by employer and period | `verified_claim` | Only confirmed statements are drawn on | No: a second, derived notion of an experience | Two "experiences": the Profile's claim groups and the Career workspace's `career_experience` | The tab reads `/api/career` experiences (canonical). Highlights = confirmed statements linked to it; skills = linked skill/tool statements and the tools on its highlights; unconfirmed linked statements shown only as "N details to review" |
| Edit profile mode: yellow banner, in-card form, highlights list, skill chips, add experience | Career workspace metadata form at the bottom of the Evidence page | `/api/career/preview` + `/changes` (create, edit, move, merge, split, confirm, retire, undo) | Organising never rewrites evidence; every change has undo history | Backend yes | Form far from the thing edited; full width; exposes "kind" and a number order | In-card editor (max ~860px), natural field order, "Change type" secondary, year-only dates allowed, "I work here now" disables End. Banner copy says what editing actually changes (not "every match score") |
| Hover actions: Edit / Delete on the card edge, inline pink confirm, Undo toast | Buttons always visible; browser `confirm()` dialogs | career `retire`, `undo` | Retiring is a revision, never a delete | Undo exists server-side | Too many controls at rest; modal dialogs | Actions on hover AND focus-within; always visible in edit mode and on touch; inline confirmation; toast with Undo backed by the history event |
| Skills tab | Skills from confirmed SKILL/TOOL statements | claims | Confirmed only | Yes | Plain list | Chips grouped, each skill once |
| Preferences tab | Preferences (search configuration) | `/api/profile` fields | Eligibility and Search Fit semantics | Yes | none | Unchanged |
| Evidence page: Projects / Certifications / Achievements card grid, type badges, year, results, skills | Career Evidence: career workspace, import review folds, ledger, sources fold | everything | Evidence is confirmed statements; provenance kept | Ledger data yes | A statement-administration page | Evidence = cards of confirmed PROJECT, ACHIEVEMENT and CERTIFICATION statements (and education, collapsed) grouped by type, 300px min columns; low-level employment statements are not primary cards (they are highlights on Experience) |
| Add evidence drawer: type cards, title, year, linked experience, description, results, skills, link/file | Manual claim form in the ledger | `POST /api/evidence` (type, text, employer, period, tools, experience_id) | A manual statement is self-attested and cites nothing | Yes | Form at the bottom | Drawer with the prototype's fields; saved as a self-attested statement ("Written by you"), never presented as a quote; file upload not offered (nothing is stored but text) |
| Evidence card detail | Row with source fold and revisions button | `/api/evidence/<key>/history`, `evidence_ref` | History kept forever | Yes | Source several folds away | Drawer: statement, "From your CV" quote or "Written by you", linked experience, History as a secondary fold, Retire ("Stop using this") with inline confirm |
| Import resume: upload, reading steps, experience 1 of N, reconciliation banner, highlights, source quote, Edit / Don't import / Confirm & next, left rail of steps, "Confirm everything left as is" | CV review (summary then experiences) and a separate package review, both inside Evidence | `/api/cv/*`, `/api/intake/*`, `cv_entry`, grouping | One statement confirmed at a time; reviews saved as they go | Backend yes | Two competing review surfaces; not reconciled with the profile | One guided review for both kinds of import (adapter endpoint): steps Experiences, Skills, Certifications, Education, Summary. Each experience gets a reconciliation state against the canonical profile. Statements reviewed one at a time inside their experience. No "confirm everything"; every answer saved immediately; resumable |
| Reconciliation banners: Already in your profile / No changes / Check the dates / New | none | none | Never invent structure | n/a | Missing | Derived, not stored: NEW, EXISTING_UNCHANGED, EXISTING_WITH_NEW_DETAILS, DATE_CONFLICT, STRUCTURE_UNCERTAIN, shown in human words |
| Date conflict: pick profile or CV dates | none | career `edit`, cv `edit_entry` | Dates as written are kept | Yes | Missing | Both alternatives shown; choosing one updates the experience (history, undo) or the read's entry; Confirm is not hidden |
| Statement review: source quote + understood text; Confirm / Edit / Reject | CV card (Yes / Not quite / No) and package card (Confirm / Correct / Reject / Not sure) | `decide`, `answer` | Confirm one at a time | Yes | Two vocabularies | One card: "From your CV" quote, Edit, Not sure yet, Reject, Confirm; inline update, short feedback, next item, focus kept |
| Documents: resume imports list | "Sources and imports" fold at the bottom of Evidence | `/api/cv/imports`, `/api/intake` | Archive reversible; delete shows its plan first | Yes | Buried | Documents page: each import with name, date, status, experiences, waiting count; Review, Archive, Restore, Delete (plan first, inline) |
| Revision history | "Inspect revisions" buttons | history endpoints | Never destroyed | Yes | Primary UI | Secondary: History fold in the evidence drawer |
| Conflicts (two documents disagree) | Package conflict groups | `/api/intake/<id>/resolve` | Resolve once | Yes | Jargon ("disagreements") | Shown in the review as "Your documents disagree" with both versions |
| Archive / delete | Import rows at the bottom of Evidence | lifecycle routes | As in docs/CAREER_EVIDENCE.md | Yes | Buried | On Documents, distinct words: Archive, Restore, Delete permanently; Reject a suggestion; Stop using (retire) a confirmed statement |

## Prototype behaviours deliberately not built

- **Confirm everything / Confirm everything left as is.** Every confirmed
  statement is one somebody read; the server refuses batches.
- **Nothing is saved until you finish.** A long review would be lost to a
  closed tab. Every answer is saved as it is given and the review resumes.
- **Profile completeness percentage and skill scores.** There is no number of
  facts that completes a person.
- **"Changes here update every match score" / results "weigh more in
  matches".** Confirming or editing evidence does not change Search Fit
  (`tests/integration/test_evidence_reach.py`); the copy says what it does:
  it changes what application preparation can draw on.
- **Delete that erases evidence.** A confirmed statement is retired (a
  revision), never deleted.
- **Uploading a proof file.** Only text is stored; the drawer offers a link.

## Data model changes

- `career_experience.description` (nullable): the one-line summary the
  prototype shows under an experience. Profile prose, not a claim: nothing
  that prepares an application reads it.
- Experience dates accept a year alone (`2015`) as well as a month, so a CV
  that stated only years is not forced into invented months.

Everything else is an adapter over the existing tables.
