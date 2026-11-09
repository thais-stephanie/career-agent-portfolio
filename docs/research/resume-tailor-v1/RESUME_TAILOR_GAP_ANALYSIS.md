# Resume Tailor v1: gap analysis

Status: research, branch `claude/resume-tailor-v1`. Subject: the owner's proposed
"Career Agent - Resume Tailoring Prompt (v1)" (Part A, a system prompt and JSON
contract for a model; Part B, a rendering spec enforced by code), read as a
proposed specification, not as a mandate to replace existing code.

## What exists today (Resume Workspace V2)

Read in full: `docs/RESUME_WORKSPACE.md`, `docs/CAREER_EVIDENCE.md`,
`src/career_agent/resume_doc/` (models, master, tailor, jd, roles, drafter,
reviewer, review, analyze, check, evidence, render, export, ats, store) and the
migrations behind them (0001 `verified_claim`, 0011 `job_application`, 0023
intake, 0047 resume workspace, 0049 `application_resume`, 0050 acknowledgements).
No ADR in `docs/decisions/` covers resumes (the ADRs there start at 0032 and are
about search and matching); the decisions live in `docs/RESUME_WORKSPACE.md`.

| Area | How it works now |
|---|---|
| Model use | Tailor V2 is deterministic (no model). "Tailor with AI" adds ONE optional request (`drafter.py`, prompt `resume-drafter-v1`) that returns a typed patch (at most 12 changes of 4 operations), never a resume. An optional blind AI reviewer is advisory only. |
| Profile input | `Identity` (name, email, phone, city, region, country, links). No time zone, working hours or page size preference in the identity. |
| Evidence | `verified_claim` revisions; only CONFIRMED and current claims of THIS profile are usable (`resume_doc.evidence.unconfirmed_lines`, asked on every write and export). Draft and intake claims are `PENDING`. |
| Evidence class / Limits | No such fields. A claim has type, text, tools, tags, employer, period. "Needs verification" maps onto `PENDING` (never usable). There is no structured "Limits" field and no lab or synthetic marker. |
| Master resume | Built once from confirmed evidence, verbatim; never written by tailoring; `evidence_changes` reports drift and never applies it. Every confirmed tool becomes a skill item (including a project's tools). |
| Master vs evidence conflict | Evidence wins at the boundary: a line citing evidence that is not confirmed now cannot be saved or exported; a line whose evidence was reworded since is listed by the review before export; the person decides. |
| Job description | Immutable `jd_snapshot`; `jd.analyse` cuts quoted requirements with kind, hardness (REQUIRED only on an explicit cue, PREFERRED, else UNKNOWN) and importance. Eligibility asks are apart. Job text is untrusted data in the AI prompt. |
| Provenance | Every line has an origin (verbatim, rule rewrite, AI rewrite, typed, imported), evidence ids, requirement ids and the original text when edited. Runs and changes are stored (`tailoring_run`, `tailoring_change`). |
| Grounding | `tailor.grounding`: numbers with unit and counted phrase from the same source sentence, named tools, rank words, result and scale words, an allow-list of neutral words, and `_responsibility` (who did the work, participation, agent, frequency, extent, limits, causality). |
| Output contract | `ResumeDocument` schema 1.0 (`schemas/resume-document.v1.json`), layout and design apart from content. |
| Rendering and PDF | One renderer (`render.py`), semantic single-column HTML, real text, CSP with no network, `data-block`/`data-keep` keep rules, preview paginator decides breaks; headless Edge prints; DOCX via python-docx; named read-back checks (`ats.py`: CONTACT, READING_ORDER, HEADINGS, DATES, GLYPHS, ONLY_VISIBLE_TEXT, HIDDEN_ABSENT, SUPPORTED_TERMS, PAGES, LAYOUT, ROUND_TRIP). |
| Approval | Nothing is exported while the review lists an unsupported or reworded statement; the person edits, restores or acknowledges each ("This is my own statement"). AI changes are accepted, edited or rejected one by one. |
| Versions | Tailored versions V1, V2... per job; revisions append-only (trigger); archive, never delete; the Master is never overwritten. |
| Application records | `job_application` (status, dates, notes), `application_resume` (which version and revision was sent). No per-application options. |
| Profile isolation | Every resume table lives in the profile's own database; evidence checks are per candidate. |
| Protecting tests | `tests/integration/test_resume_*` (tailor, drafter, reviewer, review, export, evidence trust, retirement, AI privacy), browser tests, punctuation gate (no em or en dash in project-authored text). |

## Requirement matrix

Severity: how much the gap matters for truth or for the owner's use. Risk: what
an implementation could break.

| # | Attachment requirement | Current implementation | Gap | Severity | Needed change | Regression risk | Recommended decision |
|---|---|---|---|---|---|---|---|
| A1 | Model writes the whole resume JSON from corpus + master | Deterministic plan; AI may only patch 4 kinds of text block | Different by design | n/a | None | High if adopted | **Keep the patch contract.** A whole-resume answer reopens identity, titles, dates and employers to the model. Incompatible on purpose. |
| A2 | Inputs: profile with time zone, working hours, page size | Identity has no time zone or hours; page size is per document design | Missing fields | Medium | New identity fields or reuse of the availability profile | Medium (schema, privacy) | NEEDS DESIGN DECISION (privacy: working-hours availability is an eligibility fact, kept apart from resumes today). |
| A3 | `page_limit` 2, 3 only by an explicit per-application flag | Nothing | Missing | High | Policy + storage + UI | Low (policy), Medium (storage) | Policy implemented (`budget.page_limit`, bool only). Storage NEEDS DESIGN DECISION (no migration authorised). |
| A4 | `company_context` (recruiter notes, transcripts) | Not an input | Missing | Medium | New input, untrusted, private | Medium (privacy) | Defer; same untrusted-data rule as the ad when added. |
| T1 | Only facts in evidence or master | Enforced: confirmed claims only, Master lines verbatim, review before export | None | | | | Keep. |
| T2 | Respect each claim's Limits ("never write X") | No Limits field | Missing data | High | Field or table on claims + review UI | Medium (migration) | Checker implemented (`style.limit_findings`, a risk flag in the report). Storage NEEDS DESIGN DECISION. Implication cannot be checked by code. |
| T3 | Numbers, qualifiers copied exactly | `grounding` amounts (number, unit, counted phrase, same sentence; "about", "together with" kept) | None for AI; verbatim lines are exact | | | | Keep. Measured: dropping "about" or "together with the support team" is refused. |
| T4 | A description never becomes a metric | `grounding` NUMBERS and CLAIMS | None | | | | Keep ("Kept every customer" to "100% retention" is refused). |
| T5 | No merging claims; no moving results | EMPLOYER check (a role's line cites that role's claims), amounts from their own sentence | None | | | | Keep. |
| T6 | Exact authorship | `roles.py` + `_responsibility`: RESPONSIBILITY, PARTICIPATION, AGENT, SENIORITY | None | | | | Keep. Measured on "worked with IT, who installed", "implemented ... designed by", "owned the data behind". |
| T7 | Lab and synthetic work kept apart | Bullets: an AI line under a job cannot cite a claim with no experience. Skills: placed by `master.skill_origin` (job, lab, project, unknown) in Master, Tailor and suggestions; the export review stops an unlabelled lab or project chip | Synthetic work has no marker yet | High | Evidence annotation (`setting`) | Medium (migration) | Applied without migration (2026-11-08); the annotation replaces the lab-word read later. |
| T8 | `Needs verification` never in the resume, listed as questions | PENDING never usable | Questions list missing | Medium | Report field | Low | Implemented: `questions_for_user` = PENDING statements that answer an ask. |
| T9 | Unmet asks reported as gaps, never guessed | Gaps listed in the job panel and Analyze | None | | | | Keep; report repeats them. |
| T10 | Experience length from confirmed framing, never from dates | Years asks never judged (at most "partly") | No "confirmed framing" field | Low | Field | Low | Keep never-judge rule; framing NEEDS DESIGN DECISION with Limits. |
| P1 | Requirement extraction must/nice | REQUIRED/PREFERRED/UNKNOWN | Spec has no "unknown" | Low | Map | Low | Map REQUIRED to must, PREFERRED to nice, UNKNOWN to `unstated` (absence is never "must"). |
| P2 | Coverage strong/partial/none with evidence IDs | COVERED/PARTLY/SAID/NOT_FOUND | Vocabulary | Low | Map | Low | Implemented in `report.py`. |
| P3 | Select by relevance, proof strength, recency; order bullets | Strategy: relevant first inside a role, adds, shows, hides beyond 6 | Similar | Low | None | | Keep. |
| S1 | Section names and order (SUMMARY, SKILLS, PROFESSIONAL EXPERIENCE, INDEPENDENT PROJECTS, EDUCATION AND LANGUAGES, CERTIFICATIONS) | `Layout.section_order` and `Layout.headings` per document; default order differs | Layout preset, Languages section | Low | Preset at tailoring; a languages place | Low (layout), Medium (languages) | Layout preset possible without schema change (rollout). Languages NEEDS DESIGN DECISION (no typed place). |
| S2 | Header: headline 6 to 14 words; location, time zone, hours line | Headline is the Master's; place line from identity | Hours line missing | Medium | See A2 | | NEEDS DESIGN DECISION. |
| S3 | Summary 3 to 5 sentences, 70 to 110 words, proofs with numbers | Summary is the Master's; AI may rewrite with double word allowance | No length rule | Low | Advisory finding | Low | Rollout: an Analyze advisory, never a refusal. |
| S4 | Skills 4 to 6 lines, labels mirror the job | One "Skills" group from the Master | Grouping | Low | Strategy | Medium | Defer. |
| S5 | Role: "Title, Company" then "Location . dates", optional company description line | "Title &#124; Company", "Location &#124; dates"; no description field | Description field missing | Medium | New optional field on `ExperienceEntry` (schema, editor, DOCX, ats, compare) | Medium (contract) | NEEDS DESIGN DECISION. |
| S6 | Bullets per role 5 to 8 / 3 to 6 / 1 to 3 | At most 6 shown with relevance; budget keeps at least 1 | Close | Low | | | Keep; budget enforces at least 1. |
| S7 | Independent projects without dates, data note, public repo link only | `ProjectEntry` has dates (optional), url, no note | Note field | Low | | | Defer with S5. |
| B1 | Bullet pattern "**Label:** text", past tense, 18 to 45 words | Inline bold supported; long line warning at 40 | None blocking | Low | | | Measured: the pattern passes grounding when the label repeats evidence words. |
| ST1 | No em dash; en dash only in ranges | Not checked in content | Missing | Medium | Style check | Low | Implemented: refusal for AI wording, risk flag elsewhere. |
| ST2 | Banned words and phrases; official names exempt | Analyze has a small generic-phrase list | Missing | Medium | Style check with literal exemptions | Low | Implemented. |
| ST3 | No contrastive constructions, rhetorical questions, exclamation, emoji, rating adjectives | Not checked | Missing | Low | Style check | Low | Implemented. |
| L1 | Word budgets 850 to 1,000 (2 pages), 1,300 to 1,450 (3) | Long-document warning at 30 lines | Missing | Medium | Budget | Low | Implemented (`budget.WORD_BUDGET`, `word_count`). |
| L2 | Cut order; never cut header, summary, strongest must-have proof | Nothing | Missing | Medium | Fit | Low (hides only) | Connected (2026-11-09): the export proposes the cuts (`budget.propose`) and hides only what the person accepts (`budget.apply_cuts`). |
| L3 | Third page at least half full or cut back | Nothing | Missing | Low | Note | Low | Implemented as a note. |
| V1 | Every bullet, summary sentence and skill cites an evidence ID | Evidence-origin lines must; typed lines may stand alone, reviewed before export | Different by design | n/a | | | **Keep**: the person's own words are allowed, reviewed and acknowledged. The report flags `NO_CONFIRMED_EVIDENCE`. |
| V2 | Reject any number not in cited evidence | `grounding` (stronger than substring) | None | | | | Keep. |
| V3 | Distinguish achievement numbers from dates, titles, URLs | AI wording: any new number is refused (conservative); verbatim lines need no check | Over-strict for AI | Low | | | Keep fail-closed; documented as a known false refusal. |
| V4 | Date consistency with master | Reviewer DATES and CHRONOLOGY | None | | | | Keep. |
| V5 | Contact URLs mailto, tel, https | Links must be http(s); email and phone are text in the PDF | mailto/tel not clickable | Low | Renderer | Low | Part B template (NEEDS DESIGN DECISION, see R1). |
| V6 | Reading order of extracted PDF text matches JSON | READING_ORDER check against the renderer's blocks | None | | | | Keep. |
| V7 | Render and count pages; send back with cut order | PAGES compares PDF to preview; no limit | Missing limit | Medium | Fit + measure | Low | Connected (2026-11-09): a version for a job over two printed pages gets no file (`PageLimitExceeded`, every format); the fit measures with `pdf_pages`; cuts are done by code on the person's yes, never by the model (incompatible on purpose). |
| V8 | Report shown next to preview; export after approval | Job panel shows the report; the statement review is the export gate | None | | | | Applied (2026-11-08); no second approval. The page count joins the review as a measured fact (2026-11-09). |
| R1 | Typography: Liberation Sans/Arial/Helvetica 9.5 pt, lh 1.24, name 18 pt, titles 10.5 pt, margins 0.5 in / 0.55 in, gradient divider, link color | Templates clean/modern/compact; Calibri/Arial; one margin value | New template, asymmetric margins | Low | New `Design.template` value, CSS | Medium (schema, rollback) | NEEDS DESIGN DECISION: a new template value is a schema change a rolled-back build cannot read. Measured feasibility below. |
| R2 | Single column, no tables, selectable text, embedded fonts | Yes | None | | | | Keep (fonts embed as subsets, measured). |
| R3 | Keep role title with first lines | `data-keep` + paginator | None | | | | Keep. |
| R4 | En dash in date ranges | Hyphen; ATS DATES check warns on non-ASCII separators | Conflict | Low | | | Keep the hyphen (existing named check). Incompatible, minor. |
| F1 | File name `First_Last_CV_Target_Role.pdf` | `Full_Name_Target_Title_Resume.pdf` | Naming | Low | One function | Low (tests assert the name) | Owner's choice; trivial to change, not changed here. |

## Findings that matter beyond the attachment

1. **A lab project's tool became an unlabelled skill.** `master_from_career_profile`
   turned every confirmed claim's tools into skills, so a PROJECT claim with no
   experience ("deployed a cluster in a home lab") put that tool in Skills next
   to production tools. Fixed on 2026-11-08 without a migration: the skill's
   confirmed evidence decides its group (job, lab, project) and a skill nothing
   places is kept off the page; an old document's unlabelled chip stops the
   export until moved or hidden.
2. **Ad lines under a heading are asks even when they are instructions.** An
   injected sentence under "Nice to have" becomes a quoted `nice` ask and a gap.
   It is never acted on (data only). Since 2026-11-08 `tailor.retrieve` drops
   lines written to a model (`tailor.ADDRESSED_TO_A_MODEL`, narrow phrase
   patterns) for every consumer (Tailor, job panel, Analyze, AI context, report),
   and the report lists them as the risk `AD_TEXT_ADDRESSED_TO_A_MODEL`.
   `jd.analyse` itself is unchanged.
3. **The grounding reader is conservative around numbers.** "Cut onboarding time
   from 10 days to 6 days" for evidence "Reduced customer onboarding time from 10
   days to 6 days" is refused (new verb in the number's sentence). This is a
   false refusal, never a false acceptance; the spec's "**Label:** evidence
   words" pattern passes.
4. **Limits in the owner's style are mostly prose**, not phrases: framing rules
   ("write N+ years"), scope notes, authorship notes. Only "never write X" is
   mechanically checkable; the rest is the person's review.

## Status by requirement (2026-11-09, after the final functional gates)

Connected: implemented and reached by the app. Built: implemented, not yet
reached by the app. Tested: covered by tests. Pending: not built. Blocked:
waits for an architecture decision.

| # | Status | Notes |
|---|---|---|
| A1 | Kept by design | The model patches; it never writes the resume. |
| A2 | Blocked | Time zone, hours: privacy decision (design document, "Template, language and privacy"). |
| A3 | Built, tested; storage blocked | `page_limit` is 2 everywhere; per-job third page needs the proposed table. |
| A4 | Pending | Company context not an input. |
| T1 | Connected, tested | |
| T2 | Built, tested; data blocked | `limit_findings` works when given limits; no field holds them (annotation proposal). |
| T3, T4, T5, T6 | Connected, tested | Grounding unchanged; must-refuse cases pinned. |
| T7 | Connected, tested | Skill origin in Master, Tailor, suggestion and export review. The Master keeps a skill nothing places in "Other skills" and Analyze asks where it comes from; versions for a job leave it out. Synthetic marker blocked (annotation). |
| T8 | Connected, tested | `questions_for_user` in the job panel. |
| T9 | Connected, tested | |
| T10 | Connected (never judged); framing blocked | |
| P1, P2 | Connected, tested | In the report shown by the job panel. |
| P3 | Connected | Existing strategy. |
| S1 | Pending (layout preset); languages blocked | |
| S2 | Blocked | Hours line (privacy). |
| S3, S4 | Pending | Advisory only, not built. |
| S5, S7 | Blocked | New content fields (schema). |
| S6 | Connected | Existing strategy; budget keeps one line a role. |
| B1 | Connected, tested | |
| ST1, ST2, ST3 | Connected for AI wording (refusal); connected as report flags elsewhere | |
| L1 | Connected (word count in the report) | |
| L2, V7 | Connected, tested | Two-page gate at export, proposal and approval in the editor (design document, "Two-page gate"). |
| L3 | Built, tested; not reachable | The third page is not offered (A3). |
| V1 | Kept by design; flagged | `NO_CONFIRMED_EVIDENCE` in the panel. |
| V2, V4, V6 | Connected, tested | |
| V3 | Connected (conservative); proposal documented | `grounding_v2`. |
| V5 | Blocked | Template. |
| V8 | Connected, tested | Report in the job panel; before a file, the statement review and the measured page count. |
| R1, R4 | Blocked | Template; en dash needs the DATES check changed. |
| R2, R3 | Connected | |
| F1 | Blocked (owner's choice) | Proposal in the design document. |
