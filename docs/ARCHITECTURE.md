# Architecture

Career Agent: discovery → eligibility → deterministic Search Fit → career evidence → tracking.
Resumes: one document model → Master and imports → versions per job → editor and preview → export with named checks → deterministic tailoring → Analyze, with optional AI drafting and review.

## Decision: one application, one runtime
Resumes (Resume Workspace V2, `career_agent/resume_doc`, `web/resume_api.py`) is a part of Career Agent: the same stdlib HTTP server, the same profile database and the same page. The launcher starts ONE server on ONE port and stops it with Ctrl+C, Quit or the desktop window closing.

Until v0.2.0-beta.2, resumes were made by Resume Tailor, an Apache-2.0 companion engine run in the same process on the next port and reached through a proxy (`/rt/api`) and a profile bridge. Resume Workspace V2 PR 12 retired it: the second server, the proxy, the bridge, its interface and its dependencies (FastAPI, uvicorn, python-multipart) are gone, and nothing in Career Agent starts or imports it. Retiring the engine is not deleting its data:

- **Its files stay.** Each profile's old workspace (`data/tailor-personal` for the first profile, `data/profiles/<id>/tailor` for the others) is left exactly as it was, by the retirement, by a move into Resumes and by `forget everything`.
- **Its resumes move only when asked**, after a verified backup, by `resume_doc/legacy.py`. The files are read by `resume_doc/legacy_format.py`, a frozen reader of the v0.2.0-beta.2 workspace shapes (adapted from the engine's own models under Apache-2.0); anything newer or unknown is refused per item, never guessed. `tests/fixtures/legacy_resume_helper` is the compatibility contract: a synthetic workspace the engine wrote, and the rows the migration wrote from it while it still ran on the engine's readers.
- **Old addresses still land.** `/resume-tailor?job=<id>` opens Resumes with that posting's drawer; the old page address `#resume-legacy` opens Resumes.
- **Downloads come back from where they are.** A moved download is served from that profile's old `exports` folder only, and only while it is the file that was recorded.

Details: [RESUME_WORKSPACE.md](RESUME_WORKSPACE.md).

## Scoring and provenance
Career Agent gates eligibility separately from Search Fit and data confidence. Missing hiring scope remains unresolved. Remote is not worldwide. Search Fit is deterministic arithmetic over configured preferences and observed posting facts, never a hiring probability. The title does not buy fit points. Quotes remain verifiable substrings of source text. Optional semantic matching lets a provider (DeepSeek, or the person's own Claude Code or Codex) say which parts of the Search Intent a posting supports, with verbatim quotes; a deterministic publication gate accepts only checkable findings, and the same arithmetic scores them. AI interprets; deterministic code validates and scores. See [SEMANTIC_MATCHING.md](SEMANTIC_MATCHING.md).

Career Evidence changes only through explicit review. Importing a source does not confirm it. Gaps remain visible. A resume line that cites evidence that is not confirmed now cannot be exported. A generated sentence, by rule or by an AI, never becomes confirmed evidence. A CV is read as companies, roles and dates before any claim is proposed, and an import can be archived (reversible) or deleted (permanent, keeping what was confirmed). See [CAREER_EVIDENCE.md](CAREER_EVIDENCE.md).

The public source includes unit, integration and browser tests plus the frozen legacy workspace fixture. Private golden datasets, research branches and operational results are not release dependencies. See VALIDATION.md for measured coverage and exclusions.

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
