# Resume Tailor — architecture

Developer documentation. Users never need any of this; the app's normal UI speaks
plain product language.

## Overview

```
frontend/ (React + TS + Vite)  →  built into src/resume_tailor/ui/static_v2
src/resume_tailor/
  api/          FastAPI app: candidate-scoped routes, drafts, material, backup
  workspace/    local candidate workspaces (~/.resume-tailor), migration, demo data
  presentation/ the ONLY place engine vocabulary becomes product language
  importing/    resume/document parsing into reviewable suggestions
  core/         the deterministic engine (below)
  export/       Markdown / HTML / DOCX / PDF exporters, Word/LibreOffice page verification + local PDF
  storage/      run persistence
```

## The deterministic engine (`core/`)

`pipeline.TailorService.run` executes: **analyze → match → plan → generate →
validate → trim → lint → diff**, then verifies the real DOCX page count.

- **Job analysis** (`job_analysis/analyzer.py`): section-aware parsing with
  qualification-heading semantics (longest cue wins; pay/office/location sections
  are employment conditions, never requirements), plain qualification lines,
  narrative-JD decomposition, platform listings, job metadata, role-title
  extraction with modality-suffix stripping, seniority on token boundaries, and a
  technology / capability / domain taxonomy (`lexicon.term_kind`).
- **Matching** (`matching/`): literal-term matching with proficiency ceilings; a
  concept-group notion of TRANSFERABLE; requirement-level rules (majority/union
  coverage, unevidenced specializations, operate-vs-integrate, practitioner
  domains); pure OR-lists (`disjunctive.py`); mixed AND/OR conjunctive groups
  (`groups.py`); behavioural clause matching (`behavioral.py`); binary named
  credentials (`credentials.py`); category-bounded "or similar" alternatives
  (`similar.py`); and cross-context composition safety (`composition.py`) so a
  compound claim is never stitched together from unrelated projects.
- **Provenance** (`evidence/provenance.py`): user-confirmed > primary case study >
  corroborated > multi-self-source > portfolio-only > LinkedIn-only > resume-only.
  Secondary-only evidence cannot carry a DIRECT verdict, never becomes a hero
  bullet, cannot establish years, and has ownership capped.
- **Strategy** (`resume_strategy/`): a recruiter value model (relevance ×
  ownership/impact/scope/depth × recency, boosted for direct must-have coverage),
  hero selection, per-position bullet budgets.
- **Validation** (`validation/validator.py`): every sentence checked in pure code —
  evidence existence, clause bindings, term support, number support and scoped
  numbers, overstatement/upgrade cues, plausible years, structural integrity.
  Failing bullets fall back to source wording or are removed, never shipped.
- **Page truth** (`export/pagination.py`): the "two pages" promise is verified by
  rendering the DOCX in Word (COM) or LibreOffice when available, with an
  evidence-aware trim loop; otherwise the calibrated estimate is labelled as an
  estimate.

## Candidate workspaces

One directory per candidate under `~/.resume-tailor` (`RESUME_TAILOR_HOME`
overridable), schema-versioned, cached per candidate by file mtimes. There is no
server-global active candidate: every API request names its candidate. Backups are
versioned ZIPs with zip-slip protection.

## Product-language contract

`presentation/labels.py` and `frontend/src/labels.ts` are the single mapping from
engine vocabulary to product language (Strong match / Related experience /
Not evidenced / Confirmed by you / …). Simple-mode payloads and DOM must never
contain internal identifiers — tests assert it.

The contract covers what the *app* writes: labels, statuses, reasons, filenames,
notes the app composes. Text the user typed themselves (a note on a confirmation,
a corrected detail) is shown exactly as written, even if they chose to mention an
identifier in it; the app never rewrites user text.

## Testing

`uv run pytest` runs the public suite against the synthetic demo candidate
(`workspace/demo.py`), covering source strength, scoped confirmation, certification
conflicts, boolean semantics, credentials, composition safety, trimming, isolation,
backup and evidence-safe editing. `cd frontend && npm test` runs the UI tests.
