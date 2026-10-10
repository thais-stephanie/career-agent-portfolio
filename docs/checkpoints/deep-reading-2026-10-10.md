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
their groups), 15 per stratum: top local
score, closest wording, local score 35 to 54, under 35. Written to
`data/evaluation/deep-reading-v1/` in the live install (gitignored): the
labelling page `rotular.html` (no score, no stratum, order by hash), the
postings, and `key.private.json` with each posting's stratum and local
score. The agent did NOT pre-label: the pilot's reader is the same model
family, so its labels would not be independent.

## 5. Review before the first pilot (second commit)

* **Evidence in other words.** Requiring two shared word roots kept an
  experience described differently out of the packet. The packet now holds
  every confirmed skill and tool (ceiling 80; the owner has 74, the old
  ceiling of 40 dropped 34 silently), up to 10 experience lines that share
  words with the posting, in full, and a SUMMARY of up to 10 other
  experience lines, most recent first, cut to 120 characters. Tested with a
  line sharing no word with the posting. Years inside free text are replaced
  by `[year]`. The instructions say an unsent experience is never evidence
  of a missing one.
* **Coverage, the main list only.** On the copy: 3,386 roles in the main
  list, 1,605 with enough text to read, 1,781 too short (mostly
  metadata-only leads). The panel and the preview say so; nothing calls this
  a survey of the catalogue.
* **Out of date, never current.** A review is shown as out of date, with its
  reason, when the posting's text, the reading instructions, or the profile
  it was asked against (search phrases, preferred levels and, if evidence was
  sent, the confirmed evidence) changed. Its level then is shown as "level
  when read (not current)", in its own group, never as a priority; reviews
  are read per posting, so a review of an earlier text no longer passes as
  the current one. Tested for each reason.

## 6. First pilot: 10 postings, prepared and NOT run

`scripts/deep_pilot.py` previews by default; `--execute` additionally needs
`--max-calls` covering the set, the profile's `mode: claude_code`, consent
in `share_evidence_with`, and a `claude.ai` sign-in. Postings (from the
copy; `data/evaluation/deep-pilot-1/pilot.json`, gitignored):

| Group | Local | Posting |
|---|---:|---|
| top of the local ranking | 83 | Marketing Automation Specialist (Pavago) |
| | 81 | AI Solutions Architect (HireHawk) |
| | 79 | Remote AI Engineer, LLM Systems & Automation (HireLatam) |
| likely relevant, ranked low (agent's provisional judgement) | 49 | GTM Engineer, Clay & Claude (Hire Overseas) |
| | 39 | Head of AI Native Operations (Supabase) |
| | 37 | Partner Operations & Systems Lead (Supabase) |
| other work sharing tools | 62 | AI Product Engineer, Finance Systems (Koin) |
| | 54 | WordPress Developer, UX & Automation (Uptalent.io) |
| | 44 | Senior Software Engineer (Sur) |
| control | 69 | Full Stack Engineer (Pavago) |

None is in the 60-posting labelling set. The exact messages are in
`preview.json` and a readable summary in `preview.md`: 115 distinct
evidence items across the 10 messages, about 204,000 characters in all
(roughly 68,000 input tokens at three characters a token, a guess); no
e-mail, link or year in what would leave. Known limit: company or school
names written INSIDE an evidence line are not removed.

Billing: the subscription is not a promise of zero cost. Claude's "extra
usage" lets use past the plan limit be charged; the CLI cannot report it.
The owner checks claude.ai, Settings, Usage, before any run.

## 7. Integration

1. Close the app; back up as before (`pre-deep-matching-*`).
2. Fast-forward main to this branch (it contains `claude/brazil-relevance`,
   so the full recalculation that branch needs is still required; the deep
   reading itself needs none: migration 0051 is additive).
3. Open through the shortcut; Find Jobs, Deep reading.

Verification on the branch: see the commit message for the suite counts.

## 8. Supply and scoring diagnosis (read-only, on the copy)

* Unique roles (company and title) by local score. Main list: 3,386 roles,
  0 at 90+, 3 at 80+, 5 at 70+, 15 at 60+, top 83. Every open scored role:
  97,849, 0 / 10 / 25 / 160, top 85.
* Why 90+ is near unreachable, by design and not by defect: responsibilities
  pay at most 4 matched work items (6.25 points each of 25) and tools 3 (of
  20), with the tools guard; an unstated level earns 9 of 10. A role centrally
  doing three of the seven listed kinds of work tops out near 83. No weight
  was changed.
* Recognition limits seen in strong candidates: an "AI agents" duty not read
  as AI implementation; a bold duty heading read as incidental; a company
  name ("AI at Zapier") read as the tool; business-systems integration read
  as secondary.
* Target work described in other words (four regex families over the text):
  188 main-list roles carry at least two families, 130 of them scoring under
  40; among location-to-confirm roles, 3,317 do, 120 at 60+.
* Short texts are coverage, not extraction: every one of the 2,242 eligible
  postings under 200 characters is a Jobgether lead whose stored payload is
  short too (233 have target-like titles); of 4,460 mid-length postings with
  a long payload, one (Dynamite Jobs) lost text after markup was removed.
* First pilot step: both calls refused before any answer with "Failed to
  refresh OAuth token: another Claude Code process is refreshing it"; the
  run now pauses on the first sign-in problem (79c74d0).
