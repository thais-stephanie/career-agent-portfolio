# Resume Tailor v1.1: adaptive structure, writing quality, ATS checks

Branch `claude/resume-summary`, on top of `3cd0f89` (the composed summary).
No migration, no schema change, no model call. Validation and results are in
`RESUME_TAILOR_V1_VALIDATION.md` ("Resume Tailor v1.1").

The rule: a section appears because confirmed data fills it, never because a
template has a slot for it. Nothing is written to complete a structure.

## Section matrix

| Section | Required? | Source of truth | v1.1 behaviour |
|---|---|---|---|
| Name | Always | Master identity | Missing or placeholder name blocks export (`NAME_MISSING`, `NAME_PLACEHOLDER`), as before. |
| Contact | At least one way to reach the person | Master identity (email, phone, links) | `NO_CONTACT` blocks when there is no email, phone or link. Email and phone are alternatives: `EMAIL_MISSING` now fires only when both are absent. |
| Location | Optional | Master identity, as typed | Printed as typed; never a full address. Unchanged. |
| LinkedIn / Portfolio / GitHub | Optional, each | Master identity links, as typed | Printed only when stored; never invented. Now real hyperlinks in the DOCX as well as the PDF, and checked in both (`LINKS`). |
| Headline | Optional | Master | Unchanged: the Master's, never composed. |
| Summary | Recommended when evidence supports it | Composed from confirmed claims (`resume_doc.summary`) or the Master's | 2 to 4 sentences, at most 80 words, at most 1 sentence that repeats a shown bullet. There is no summary, and the report says why (`SUMMARY_MISSING`), when there is no role, no statement, or only the role plus a line the page already shows. The frames follow the language of the person's statements when the document is on the default language. `SUMMARY_SHORT` was removed: a short, specific summary is not a fault. |
| Skills | Optional | Confirmed skill and tool claims | Lab and unknown-origin skills are never shown as a job's (unchanged). **New:** a skill that only an academic or personal experience holds is a project's (`master.claims_by_key`, `tailor.sources`), so it is grouped as "Independent projects" and left out of the summary. No fixed count. |
| Experience | Optional | Career Profile experiences of kind EMPLOYMENT or FREELANCE (and VOLUNTEER, see below) | No entry, no heading. Titles, employers and dates as confirmed. An internship stays an internship by its confirmed title. |
| Projects | Optional | Experiences of kind ACADEMIC or PERSONAL | **New:** a new Master places them under Projects ("{title}, {organisation}"), never under Experience. |
| Education | Optional | Master (typed or imported) | No entry, no heading. Unchanged source: confirmed EDUCATION claims have no typed fields and are still not placed automatically. |
| Certifications | Optional | Confirmed certification claims | Unchanged. |
| Languages | Not supported as a typed section | None | Needs a design decision (below). Never inferred. |
| Company description | Not shown | Profile prose (migration 0040) | Needs a design decision (below). |

## Order, by what the version shows

The Tailor moves sections only when the Master keeps the default order; an
order the person chose is never changed (`tailor.sections_ahead`). The
decision reads counts of shown entries and the job's relevance scores. It
does not guess seniority from titles or dates.

* **No shown role** (new graduate, no formal job, projects only): Education
  first, then Projects, then the rest. Experience has no heading.
* **One shown role and a project that answers the job more strongly than any
  line of it** (trainee, intern): Projects before Experience.
* **Otherwise** (experienced, career changer, returning after a gap): the
  default order, with Summary, then Experience.

The move is recorded as a `REORDER_SECTIONS` change and said in the job
panel in words ("Education, Projects now come before Experience: …").

## Behaviour by profile (synthetic fixtures in `tests/integration/test_resume_personas.py`)

| Profile | Summary (synthetic example, words) | Sections, in order | Notes |
|---|---|---|---|
| Senior | Role, 3 employers, job skills, 1 statement (36) | Summary, Experience, Certifications, Skills | The strongest number is not always the statement chosen: relevance to the job leads. |
| Mid-level | Same shape (25) | Summary, Experience, Skills | No number needed. |
| Trainee | "Operations Trainee with experience at …", the job's skill only (20) | Summary, Projects, Experience, Education, Skills | Academic project ahead of the single role; its Python and SQL listed as Independent projects. |
| Intern | "Data Analyst Intern with experience at …" (27) | Summary, Experience, Education, Skills | Title as confirmed. |
| New graduate | None (no role), reason said | Education, Projects, Skills | No Experience heading. |
| No formal job | None | Projects, Skills | |
| Projects only | None | Projects, Skills | |
| Career changer | "Science Teacher with experience at …" (20) | Summary, Experience, Skills | Never the target's title. |
| Returning after a gap | Last role (21) | Summary, Experience, Skills | Dates as stored; nothing explains the gap. |
| Insufficient information | None | Skills only (if any) | `NO_CONTACT` blocks export until a contact exists. |
| Complete example (3 links) | Role, employers, skills, 1 statement (32) | Summary, Experience, Projects, Education, Certifications, Skills | One page; three real links in PDF and DOCX. |

## Writing quality

* Summary: identity from a confirmed role, then confirmed skills the job asks
  for, then one or two confirmed statements, verbatim. Numbers and years only
  from confirmed text; years of experience are never computed.
* Clichés ("results-driven", "team player", "passionate", …) are already
  reported by `style.py` as `GENERIC_PHRASE`; the composed summary cannot
  produce them because its only own words are the two frames.
* Bullets are confirmed statements, verbatim. The Tailor does not rewrite
  them into Action + Context + Result: that needs a model or invented
  structure, and the optional AI drafter keeps its existing grounding checks.

## ATS and rendering

Unchanged: one column, real text, conventional headings, simple bullets,
dates beside roles, no images, no photo. New:

* DOCX links are real `w:hyperlink` elements (`export._hyperlink`).
* `ats.check_export` adds `LINKS`: every web address the resume shows as a
  link must be a link annotation in the PDF, or a hyperlink relationship in
  the DOCX. It reports `NOT_MEASURED` when the resume shows no link.
* This is structural compatibility only. No claim is made about any
  particular applicant tracking system.

## Needs a design decision (stopped here, not built)

1. **Languages.** No typed section and no claim category exist.
   `SectionRef` is a fixed pattern, so adding one is a schema change
   (`schema_version` 1.0 → 1.1, regenerated JSON schema), and the level must
   come from a confirmed record. A custom section typed by the person already
   works today.
2. **Volunteer work.** It stays under Experience with its confirmed title.
   A Volunteer section needs a new `SectionRef`, so it is a schema change.
   The alternative, a custom section, loses organisation and dates.
3. **Company description.** Migration 0040 decided that the experience
   description is profile prose that nothing preparing an application reads.
   Showing it needs that decision reversed, or a new confirmed field.
4. **Education from evidence.** EDUCATION claims are free text. Splitting
   them into institution, degree and dates is guesswork, so they are still
   placed by the person (editor or import).
5. **Project claims outside an experience.** A PROJECT claim with no
   experience is a sentence, not a name. It is still left out of the Master,
   with a note, as before.
6. **Project link back to the experience.** A project made from an academic
   or personal experience carries no `experience_id`, because `ProjectEntry`
   has no such field. `evidence_changes` matches it by name, so renaming the
   project makes the experience look new. A field would be a schema change.

## Editorial references, separated from evidence

Qualitative sources: recruiter accounts shared by the owner (Reddit) and
university career centre guides. What they recommend:

* a specific, tailored summary;
* results over adjectives;
* the ad's words when they describe the person's real work;
* plain formatting that parsers read.

None of these was measured here. The figures that circulate with them are
not treated as established, and nothing is built on them:

* doubled interview rates;
* "75% of resumes rejected by ATS";
* an ideal number of skills;
* guaranteed ranking or hiring.

Speculative applications to companies are noted as a possible future
feature. Nothing in this scope sends email or applies on anyone's behalf.
