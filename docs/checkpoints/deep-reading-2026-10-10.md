# Deep reading: audit, build and pilot plan (2026-10-10)

Branch `claude/deep-matching`, from `claude/brazil-relevance` (6235ac0), so it
inherits the geography cleanup and the repost election. Decision record:
ADR-0035. Nothing here was run against the owner's data or sent to any
provider: every test uses synthetic postings and a fake provider.

## 1. What existed (audited on main dacaf4d)

| Fact | Where | Status |
|---|---|---|
| A Claude Code provider runs `claude -p --output-format json --json-schema ... --tools "" --strict-mcp-config --no-session-persistence --setting-sources ""` in an empty temporary directory, prompt on stdin | `semantic/providers/local_cli.py` | proven |
| The child gets an ALLOWLISTED environment: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CODEX_API_KEY`, `OPENAI_API_KEY` never reach it | `CHILD_ENVIRONMENT` | proven, tested again here |
| Only `authMethod == claude.ai` is used; an API-key sign-in is reported, not used | `ClaudeCodeProvider.availability` | proven; owner's machine: Max, `claude.ai` |
| Choosing a provider writes `semantic.local.yaml` and sends nothing; runs start from Settings, AI & Semantic Matching, Estimate, Start, or `career-agent semantic-match --run` | `web/semantic_api.py` | proven; owner's profile `mode: claude_code`, 0 evaluations |
| Findings only RAISE: each signal takes the larger of literal and semantic; a "none" verdict is never read | `match/score.py` | defect for this goal |
| Candidates come from a full-text proximity filter, include to-confirm locations and every repost | `semantic/runner.py` | defect for this goal |
| Skip ignores the provider, so two providers cannot be compared on one sample | `semantic/store.py` | limit |
| A limit stopped a run only after three failures; a cancel waited up to 180 s for the call in flight | `semantic/runner.py`, `local_cli.py` | fixed here |
| The intent never carried the person's history, by a written privacy decision | `semantic/intent.py` | kept; the deep reading adds history behind consent |

On the owner's machine (presence only, no values read): `ANTHROPIC_API_KEY`,
`GOOGLE_API_KEY` and `DEEPSEEK_API_KEY` are set at Windows user level;
`claude` 2.1.286 is installed and signed in with a subscription; `codex`,
`gemini` and `cursor-agent` are not installed.

## 2. What was built

* `semantic/deep.py`: contract `deep-1`, verification, priority (ADR-0035).
* `semantic/deep_run.py`: main-list population, three strata, the evidence
  packet with redaction, storage, a sequential run with cancel, pause on a
  limit and resume by skipping what was read.
* Migration 0051 `deep_review`; runs in `semantic_run` with mode `deep`.
* `web/deep_api.py`: `GET /api/deep`, `POST /api/deep/consent`,
  `POST /api/deep/preview`, `POST|GET /api/deep/run`,
  `POST /api/deep/run/cancel`, `GET /api/deep/reviews`,
  `GET /api/jobs/<id>/deep`; the job payload carries `deep_review`.
* Interface: a "Deep reading" button beside Today in Find Jobs opens a panel
  with access and sign-in, consent, preview (postings, strata, exactly what
  leaves, a rough size), a confirmation box, progress, cancel, the last run
  and the results by level; each job drawer has a "Deep reading" section
  with the level, the comparison with the local score, quotes, requirements
  against evidence, conditions to confirm, divergences, tools, and
  provenance (access, model, date, contract, profile version, stratum,
  removed claims). PT and EN.
* Fixes on the findings path: pause on the first limit, cancel ends the
  call in flight.

## 3. Providers (checked 2026-10-10, official documentation only)

| Access and billing | Usable by the app | On this machine | Notes |
|---|---|---|---|
| Claude Code, Claude subscription | yes, already integrated | installed, signed in (Max) | One pool with claude.ai (five-hour and weekly limits). In `-p`, `ANTHROPIC_API_KEY` is always used when present, which the allowlist prevents. "Extra usage" can bill past the limit and is an account setting the CLI does not expose. The Agent SDK overview says third-party products may not offer claude.ai login; the owner running her own binary under her own login is personal use, and other users of this open-source app would sign in themselves. |
| Anthropic API, credits | not in `semantic/` | key present | Haiku 5.5 $0.10 in / $0.50 out per million tokens, Batch half; Sonnet 5.5 $2 / $10. Not used: no paid call in this stage. |
| Codex CLI, ChatGPT subscription | integrated | not installed | `codex exec` reuses the ChatGPT login; after the plan allowance, purchased credits (auto-reload optional). Precedence of `CODEX_API_KEY`/`OPENAI_API_KEY` over the login not verified in the docs; the allowlist removes both. |
| Gemini CLI | no | not installed | The docs say free and Google One accounts moved to the Antigravity CLI on 2026-06-18; Antigravity terms not verified. |
| Gemini API free tier | no | key present | Free-tier content is used to improve products and may be read by reviewers: unsuitable for profile data. |
| DeepSeek API, credits | integrated (findings only) | key present | Data processed in the People's Republic of China. Refused by the deep reading (metered). |
| Cursor CLI | no | not installed | Whether it draws on the plan is not stated. |

Sources (accessed 2026-10-10):
https://code.claude.com/docs/en/authentication ,
https://code.claude.com/docs/en/cli-reference ,
https://code.claude.com/docs/en/agent-sdk/overview ,
https://code.claude.com/docs/en/legal-and-compliance ,
https://support.claude.com/en/articles/12429409-manage-extra-usage-for-paid-claude-plans ,
https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan ,
https://platform.claude.com/docs/en/about-claude/pricing ,
https://learn.chatgpt.com/docs/non-interactive-mode ,
https://learn.chatgpt.com/docs/auth ,
https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan ,
https://geminicli.com/docs/get-started/authentication/ (quota page dated 2026-06-18),
https://ai.google.dev/gemini-api/terms (updated 2026-04-28),
https://ai.google.dev/gemini-api/docs/rate-limits ,
https://cursor.com/docs/cli/headless ,
https://api-docs.deepseek.com/quick_start/pricing ,
https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html (2026-02-10).

## 4. Evaluation set

60 postings from the relevance COPY (read-only), main list representatives
with readable text (1,526 after removing the 120 development postings and
their groups; 2,242 of the copy's 4,198 eligible postings have under 200
characters of text and cannot be read deeply), 15 per stratum: top local
score, closest wording, local score 35 to 54, under 35. Written to
`data/evaluation/deep-reading-v1/` in the live install (gitignored): the
labelling page `rotular.html` (no score, no stratum, order by hash), the
postings, and `key.private.json` with each posting's stratum and local
score. The agent did NOT pre-label: the pilot's reader is the same model
family, so its labels would not be independent.

## 5. Pilot proposal (not run)

* Provider: Claude Code, Claude subscription, sign-in checked `claude.ai`.
* Sample: the 60 labelled postings, read in runs of 25, 25 and 10, with and
  without evidence (`share_evidence_with: [claude_code]` toggled), so the
  two readings are compared on the same postings and the same profile.
* Sent: the search phrases, preferred levels, title and text of each
  posting; with consent, up to 40 skills and tools and up to 10 relevant
  experience lines per posting, redacted, under local ids. The preview shows
  exactly this before Start.
* Cost: no API charge when "extra usage" is off (the owner checks claude.ai,
  Settings, Usage; the app cannot). Quota use is unknown until measured; a
  hypothesis from the 2026-09 benchmark of the findings contract is about
  7,500 input and 1,000 output tokens and 12 s per posting, to be replaced by
  the pilot's own `input_tokens`, `output_tokens` and `latency_ms`.
* Measured against the owner's labels: relevant postings in the first 10
  and 20 under each ordering (local; deep level then local); relevant
  postings the reading raised from the control and middle strata; irrelevant
  postings it placed HIGH; claims removed by verification; tokens and time
  per posting.

## 6. Integration

1. Close the app; back up as before (`pre-deep-matching-*`).
2. Fast-forward main to this branch (it contains `claude/brazil-relevance`,
   so the full recalculation that branch needs is still required; the deep
   reading itself needs none: migration 0051 is additive).
3. Open through the shortcut; Find Jobs, Deep reading.

Verification on the branch: see the commit message for the suite counts.
