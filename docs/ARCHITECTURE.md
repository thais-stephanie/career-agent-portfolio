# Architecture and decision: companion Beta

Career Agent: discovery → eligibility → deterministic Search Fit → career evidence → tracking.
Resume Tailor: job analysis → evidence matching → strategy → generation → validation → editing → export.

## Decision
Use the existing Apache-2.0 Resume Tailor implementation as a local companion in the same source tree. A uv path dependency installs it into the same Python 3.12 environment. Its built React interface ships with the ZIP. Career Agent keeps its stdlib HTTP interface; Tailor keeps FastAPI. The main launcher binds both loopback ports before opening the browser and stops both servers together.

This avoids a rewrite and preserves the standalone engine, tests and provenance rules.

### One person, one profile: the profile bridge
Started by the launcher, Resume Tailor follows the active local profile through one in-process object, `career_agent/web/tailor_bridge.py` (`CareerBridge`), passed to `create_app(bridge=...)`. Resume Tailor never imports Career Agent; it uses the bridge through its own protocol (`resume_tailor/integration/career.py`). Started on its own, Resume Tailor is the standalone app, candidate selector included.

- **Identity.** A profile maps to exactly one Tailor candidate by its stable id, never by a display name: the candidate id is the lower-cased profile id and `candidate.json` records `career_agent_profile_id`. Reopening finds the same candidate. A Tailor home holding exactly one unclaimed candidate (used before this bridge) has it adopted, so nothing is duplicated. Each profile keeps its own Tailor home, so one profile's candidate, resumes and runs are never visible from another. A switch rebuilds Tailor on the new profile, and the old bridge refuses every call (HTTP 409 "Reload"), so a page left open cannot read or write the new profile.
- **Handoff contract.** The drawer links to `/resume-tailor?job=<job id>`. The redirect validates the id and forwards it with the active profile id; no posting text, CV or evidence is ever placed in a URL. Tailor reads the posting (title, company, link, description, status) from the bridge, fills the job description with it, and warns when the link was opened for another profile.
- **Source of truth.** Application status is Career Agent's `job_application`. Tailor's Applications screen lists the profile's tracked postings (every status but Found) and changes a status only through Career Agent's own status route, so its validation, applied date and history apply. A tailored resume records `career_job_id` and is listed under that posting; tailoring never changes a status.
- **Evidence handoff.** Only CONFIRMED Career Profile statements cross, verbatim, from `CareerRepo.overview()`. In Tailor they are records with `source_file: career_agent`, the claim key as `source_reference`, and `user_verified` status, because the person confirmed them in Career Agent. An import replaces the previous Career Agent import and never touches details from Tailor's own sources. A suggestion waiting for review, a retired claim or a generated sentence never crosses.
- **Base resume.** "Use my Career Profile" makes a base resume whose bullets are those confirmed statements, verbatim, each carrying its evidence id, and whose headline is the most recent role title as written. Nothing is summarised or generated. An upload accepts PDF, Word (.docx) and Markdown (.md, and plain text); extension, declared type and file signature must agree, the limit is 10 MB, and every refusal is a sentence on screen. Without a base resume, Tailor shows "No base resume yet" with both ways to get one, and Analyze cannot run.
- **No empty candidate.** The page builds every candidate path through one guard, and the server refuses `/api/candidates//...` (and `undefined`/`null`) before any route, with a message.
- **Backups.** Following a profile, a Tailor backup exports that profile's workspace and can only be restored into it (with the name typed to confirm); it is never added as a second, invisible candidate.

## Scoring and provenance
Career Agent gates eligibility separately from Search Fit and data confidence. Missing hiring scope remains unresolved. Remote is not worldwide. Search Fit is deterministic arithmetic over configured preferences and observed posting facts, never a hiring probability. The title does not buy fit points. Quotes remain verifiable substrings of source text. Optional semantic matching lets a provider (DeepSeek, or the person's own Claude Code or Codex) say which parts of the Search Intent a posting supports, with verbatim quotes; a deterministic publication gate accepts only checkable findings, and the same arithmetic scores them. AI interprets; deterministic code validates and scores. See [SEMANTIC_MATCHING.md](SEMANTIC_MATCHING.md).

Career Evidence changes only through explicit review. Resume Tailor's source strength, clause-scoped confirmation, evidence identifiers, conflicts and validator remain in its own engine. Importing a source does not confirm it. Gaps remain visible. Evidence-only export rejects unsupported edits. A generated sentence does not become confirmed evidence in either module. A CV is read as companies, roles and dates before any claim is proposed, and an import can be archived (reversible) or deleted (permanent, keeping what was confirmed). See [CAREER_EVIDENCE.md](CAREER_EVIDENCE.md).

The public source includes unit, integration and browser tests plus synthetic Tailor invariants. Private golden datasets, research branches and operational results are not release dependencies. See VALIDATION.md for measured coverage and exclusions.

### What a posting did not say

Search Fit separates UNKNOWN (the posting is silent) from UNMATCHED (the posting contradicts what the person wants). Audited per component (result schema 11):

- **Seniority:** precedence is a level stated in the title, then a level the body genuinely determines, then MID / PLENO. The MID fallback is SCORED as MID, the same points a stated mid-level posting earns under the same preferences. Before schema 11 it earned zero while saying "treating it as mid-level", below a stated wrong level. That was the one inversion. The stored `job_match.seniority` of an unstated posting is MID too, so a person who EXCLUDES mid-level also stops seeing postings that never stated a level: treated as MID means treated as MID everywhere. The `unevidenced` weight in older search files is ignored.
- **Compensation and contract:** already three-valued. An unstated salary earns the `salary_unknown` points, between "below target" and "meets target". An unstated engagement earns `contract_unknown`, between unwanted and preferred.
- **Work model:** an unstated model earns the neutral share, the same as a stated neutral one.
- **Responsibilities, tools, automation:** these are evidence points for the work a person asked for, and a posting that never mentions that work earns none of them. That is not a penalty, because nothing is subtracted, and it is unchanged.

**Posting completeness** (formerly "Posting detail", `data_confidence`) measures how much the employer wrote down: description, location, hiring scope, engagement, compensation, a stated level, a date. It is reported beside Search Fit and never multiplied into it (ADR-0004). A missing salary or level lowers completeness, not fit.

### The local model reading
Reading a posting with the local model (Ollama, `qwen3:4b` by default) takes minutes on a laptop CPU, so it runs in the background, one reading per posting (`local_ai/runner.py`). `POST /api/jobs/{id}/enrich` starts it, `GET /api/jobs/{id}/local-reading` reports its state, `POST /api/jobs/{id}/enrich/cancel` stops it. The states are NOT_RUN, RUNNING (phase: checking, loading, reading, writing, verifying), SUCCESS, CANCELLED, MODEL_MISSING, OLLAMA_UNAVAILABLE, TIMEOUT and ERROR; each has its own sentence in the drawer.

The request is streamed, over a socket the reader owns and never blocks on for more than 0.2s, so Cancel and the total deadline (6 minutes by default, `OLLAMA_TIMEOUT_MS`) act within a fraction of a second even while the model is still loading and has sent nothing. A cut-off answer is an error, not a result. A successful reading is stored per profile and posting, shown again on every visit, and never changes Search Fit. Switching profiles cancels any reading still running.

## Storage and HTTP boundaries
Each module owns its files. The shared launcher scopes both to this installation, with separate demo and personal roots. Neither HTTP service is a multi-user service. No remote bind or reverse-proxy deployment is supported. See PRIVACY.md for the concrete network and file inventory.
