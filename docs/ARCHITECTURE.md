# Architecture and decision: companion Beta

Career Agent: discovery → eligibility → deterministic Search Fit → career evidence → tracking.
Resume Tailor: job analysis → evidence matching → strategy → generation → validation → editing → export.

## Decision
Use the existing Apache-2.0 Resume Tailor implementation as a local companion in the same source tree. A uv path dependency installs it into the same Python 3.12 environment. Its built React interface ships with the ZIP. Career Agent keeps its stdlib HTTP interface; Tailor keeps FastAPI. The main launcher binds both loopback ports before opening the browser and stops both servers together.

This avoids a rewrite and preserves the standalone engine, tests and provenance rules. Native shared persistence would require a reviewed mapping of evidence states, identity and revision semantics. This release intentionally has no such mapping. Handoff is an explicit clipboard copy and a local link. No resume or candidate detail is placed in a URL.

## Scoring and provenance
Career Agent gates eligibility separately from Search Fit and data confidence. Missing hiring scope remains unresolved. Remote is not worldwide. Search Fit is deterministic arithmetic over configured preferences and observed posting facts, never a hiring probability. The title does not buy fit points. Quotes remain verifiable substrings of source text. Optional semantic matching lets a provider (DeepSeek, or the person's own Claude Code or Codex) say which parts of the Search Intent a posting supports, with verbatim quotes; a deterministic publication gate accepts only checkable findings, and the same arithmetic scores them. AI interprets; deterministic code validates and scores. See [SEMANTIC_MATCHING.md](SEMANTIC_MATCHING.md).

Career Evidence changes only through explicit review. Resume Tailor's source strength, clause-scoped confirmation, evidence identifiers, conflicts and validator remain in its own engine. Importing a source does not confirm it. Gaps remain visible. Evidence-only export rejects unsupported edits. A generated sentence does not become confirmed evidence in either module. A CV is read as companies, roles and dates before any claim is proposed, and an import can be archived (reversible) or deleted (permanent, keeping what was confirmed). See [CAREER_EVIDENCE.md](CAREER_EVIDENCE.md).

The public source includes unit, integration and browser tests plus synthetic Tailor invariants. Private golden datasets, research branches and operational results are not release dependencies. See VALIDATION.md for measured coverage and exclusions.

## Storage and HTTP boundaries
Each module owns its files. The shared launcher scopes both to this installation, with separate demo and personal roots. Neither HTTP service is a multi-user service. No remote bind or reverse-proxy deployment is supported. See PRIVACY.md for the concrete network and file inventory.
