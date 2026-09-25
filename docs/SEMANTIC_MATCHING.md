# Semantic matching and Search Fit v5

Measured and decided on 2026-09-25. This is the record of why Search Fit reads postings the way it now does.

**AI INTERPRETS. DETERMINISTIC CODE VALIDATES AND SCORES.** A provider never produces a number that becomes Search Fit.

## The problem

A read-only study replayed the 74,000 stored readings of one real search exactly.
- **Scores:** every posting was WEAK: mean 11.7, median 9, maximum 31.
- **Vocabulary coverage (the dominant cause):**
  - 98.3% of postings matched none of the person's 20 work phrases, and 87.9% matched none of their tools.
  - Each phrase was one exact pattern, so a posting that described the work in other words earned nothing.
  - 8 of the 20 work phrases never appeared in any posting.
- **The arithmetic made it worse:**
  - An unconfigured component stayed in the denominator.
  - Each phrase was worth one twentieth of its component, so a near-ideal posting scored 33.
  - Seniority ignored the preferred levels.
  - A soft penalty written as `-3` added points instead of subtracting them.

Formula changes alone only redistribute evidence that was found. Semantic interpretation addresses coverage; the deterministic changes make the arithmetic honest.

## Search Fit v5 (deterministic, always available)

`match/score.py`, `MATCH_SCHEMA_VERSION` 10:

- **Unconfigured is not unmatched.**
  - A phrase component with no configured phrase leaves the denominator (`configured: false`, max 0).
  - A configured component that found nothing stays at 0 of its max.
- **The strongest few pay.**
  - Work pays its 4 strongest distinct signals, tools and methods their 3 strongest, other desired signals their 3 strongest. At full strength, N central matches fill the component.
  - N is fixed by meaning, not fitted to a corpus.
  - N is never more than the number of phrases the person configured: an intent of one phrase is filled by that phrase. More matches add nothing, so verbosity and keyword stuffing buy nothing.
- **One sentence fills at most half a component, and only one of that through AI.**
  - Through literal phrases, one sentence pays for at most 2 of the 4 work slots or 2 of the 3 tool slots, strongest first.
  - Through a semantic finding, one sentence pays for one intent item.
  - A single stuffed line can never fill a component. Matches that were found but not paid stay visible as "already counted".
  - The study proposed "one sentence pays once". Implemented literally, it scored the demo's canonical fit posting MODERATE: its bullets are compound ("Build workflow automation and REST API integrations"), and each states two activities.
  - Relaxing the rule for semantic findings as well had a measured cost: an AWS DevOps posting labelled a non-match rose to GOOD under both DeepSeek and Claude Code. Providers cite one sentence for several items more loosely than postings state them.
  - So the relaxation applies to the person's literal phrases only.
- **The tools guard.** When the person stated the work they want and none of it was found, tools earn at most half their component.
- **Seniority follows the preferred levels.**

  | Level in the posting | Share of the component |
  |---|---|
  | Preferred | Full |
  | One step away on the ladder | Half |
  | Any other stated level | A fifth |
  | Excluded | Nothing, and hidden from Discover (a visibility choice, never an eligibility failure) |
  | Unstated | Nothing |

- **Soft penalties are subtracted magnitudes.** A legacy negative weight is read as its magnitude, and setup now writes `3`.
- **Thresholds are unchanged:** STRONG 75, GOOD 55, MODERATE 35. The drawer's wording now follows the band instead of a second set of cut-offs (70/50/30).

**Effect on the same 74,000 stored readings**, replayed in memory without writing:

| | Mean | Median | P90 | P95 | Max | WEAK | MODERATE | GOOD | STRONG |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Before (v4) | 11.7 | 9 | 20 | 22 | 31 | 74,000 | 0 | 0 | 0 |
| After (v5, deterministic) | 14.0 | 9 | 27 | 31 | 72 | 71,619 | 2,325 | 56 | 0 |

## The semantic path

```
Search Intent ──┐
                ├─> provider (text) ─> parse ─> publication gate ─> stored evaluation
posting text ───┘                                          │
                                  rescore/replay <─────────┘ (validated findings only)
```

- **Search Intent** (`semantic/intent.py`):
  - The positive phrases of the work, tools and other-signals components, with short ids (`W1`, `T3`, `O2`).
  - Nothing from the Career Profile, evidence, CVs or applications.
  - Its digest is part of every cache key.
- **The contract** (`semantic/contract.py`):
  - For each of work, tools and other signals the provider returns a verdict (`strong`, `partial`, `none` or `unresolved`) and matches.
  - Each match names an intent id and carries at least one verbatim quote.
  - `role_core` and `provider_confidence` are metadata and never become points.
  - The contract identity is the label plus a digest of the instructions and schema, so editing the prompt changes every cache key.
- **The publication gate** (`semantic/gate.py`) treats every provider as untrusted.
  - It publishes a finding only when:
    - the answer parses into the schema;
    - the intent id is one that was sent, in the right list;
    - at least one quote is a verbatim substring of the posting text that was sent.
  - Line-break folding is the only tolerance, and the stored quote is always the posting's own span.
  - A positive verdict with nothing checkable behind it becomes `unresolved`, meaning no evidence: never "no fit" and never fit.
  - A provider failure stores nothing.
- **Scoring** (`match/score.py`):
  - A published finding for an intent item is a contribution at PRIMARY (strong) or SECONDARY (partial) prominence, with its quote.
  - A lexical hit and a finding for the same item never both pay; the stronger reading wins.
  - The same top-N, one-sentence and guard rules then apply.
  - The result records its semantic provenance: provider, model, contract, and requested provider when a fallback happened.
- **Storage:** migration 0041 is additive and adds `semantic_evaluation` (unique on content hash, intent digest, contract, provider and model) and `semantic_run`. No credential is ever stored.

### Replay, not a full re-read

v5 changes arithmetic and provenance, never a reading. So a schema 9 row replays; `REPLAY_MIN_SCHEMA` is 9, and a target-version row written by an older schema is accepted as a replay source. `READER_IDENTITY` and `config_version` do not move. The first targeted rescore after upgrading rewrites every row from its stored readings without reading any posting again.

Semantic findings reach a score only through a rescore. After a semantic run, the evaluated postings are marked and rescored from their stored readings. Turning semantic matching on or off marks every posting that has an evaluation.

## Candidate selection

A run never sends the whole corpus. The prefilter (`semantic/runner.py`) is deterministic and recall-first.
- **A posting is a candidate when it is:**
  - open, with text;
  - not verified ineligible;
  - not blocked by screening;
  - not at a hidden level;
  - not already evaluated for this intent and contract;
  - a proximity match for at least one intent item (two of the item's words within six words of each other, through the local full-text index).
- **Rejected alternative:** "any intent word appears" matched 73% of the corpus.
- **Measured on the real corpus:**
  - 8,236 of 60,438 open, eligible postings pass (13.6%).
  - Every posting a person labelled a fit passes.
  - 7 of 8 labelled adjacent postings and 8 of 34 labelled non-matches also pass; letting some non-matches through is intended for a recall-first filter.
- Candidates are ranked by full-text relevance, then recency.
- A run takes at most 300 postings on a metered provider, or 25 on a subscription provider, by default.

## Benchmark

The benchmark used a fixed, non-cherry-picked sample of 230 stored postings: the first postings in `sha1(job_id)` order from each of nine strata.

| Stratum | Postings |
|---|---:|
| Lexical work hits | 30 |
| Plausible title, no lexical overlap | 60 |
| Tool-heavy, work-light | 25 |
| Current top decile | 30 |
| Median | 25 |
| Low | 20 |
| Obvious non-match | 20 |
| Verbose | 10 |
| Sparse | 10 |

- **Labels:** 45 of them (five per stratum) were labelled by hand against the person's intent: 3 fit, 8 adjacent, 34 no. The labels and posting texts are private, local and uncommitted.
- **Scoring rule:** a fit is right at GOOD or STRONG, an adjacent posting at MODERATE or GOOD, and a non-match at WEAK.

| Arm (same 45 postings) | Agree | Over-promoted | Missed | Avg latency | Tokens in / out per posting | Marginal cost per posting |
|---|---:|---:|---:|---:|---:|---:|
| v4 as stored | 34 | 0 | 11 | n/a | n/a | n/a |
| v5 deterministic | 36 | 0 | 7 | n/a | n/a | n/a |
| DeepSeek Flash, contract 1 | 38 | 4 | 0 | 1.6 s | 1,820 / 178 | $0.0008 |
| DeepSeek Flash, contract 2 | 39 | 3 | 1 | 1.7 s | 2,043 / 181 | $0.0008 |
| DeepSeek Flash, contract 2, thinking low | 38 of 44 | 2 | 1 | 6.9 s | 2,059 / 1,415 | $0.0023 |
| Claude Code (claude-sonnet-5, subscription) | 39 | 2 | 2 | 11.6 s | 7,560 / 1,043 | your plan limits |
| Codex (gpt-6-sol at low effort, subscription) | 39 | 2 | 2 | 8.8 s | 21,986 / 178 | your plan limits |

- **Quote compliance:** every provider quoted the postings verbatim. With the final gate, which also requires word boundaries, no function-word-only quote and at least three words for a work or other-signal quote, these claimed quotes were published:
  - DeepSeek 114 of 118;
  - Claude Code 74 of 76;
  - Codex 84 of 87;
  - DeepSeek with thinking 115 of 122.

  The refusals are fragments too short to state work (a single tool name offered as evidence of a kind of work), not invented text. Before this hardening, only thinking-low had invented a quote, once. One thinking-low answer was cut off at its output ceiling and produced no finding.
- **Shared over-promotions:** the semantic arms promoted the same adjacent data postings to STRONG. Both are data roles, and the person's stated intent is broad across data work. That is the breadth of the stated intent, not a provider error.
- **Shared miss:** a business-systems architect posting that names none of the person's tools and no level. MODERATE is what the arithmetic says it should be.
- **DeepSeek's only unique false positive:** a manufacturing test-automation role scored GOOD.
- **Stratum results:** across the 227 of 230 postings DeepSeek answered with contract 2, the low, median, sparse, verbose and obvious-non-match strata stayed WEAK. The strata with work evidence rose.

**Cost:**
- DeepSeek's recorded peak rate is $0.30 in and $1.20 out per million tokens; off-peak is half.
- The whole benchmark used 503 answered DeepSeek calls (959,319 input and 147,272 output tokens), about $0.46 at peak. With the probes and the truncated thinking attempts that were billed, it stayed under about $0.52.
- Evaluating all 8,236 candidates would cost about $6.80 at peak, in runs capped by the budget.

## Routing decision (Auto)

Auto uses the first available provider in this order:
1. DeepSeek, when a key is configured;
2. Claude Code, when signed in with a Claude subscription;
3. Codex, when signed in with ChatGPT;
4. otherwise deterministic scoring.

There is no escalation. Claude Code and Codex agreed with the labels exactly as often as DeepSeek (39 of 45 each). Neither fixed DeepSeek's one unique error without adding one of its own. They were 5 to 7 times slower, and they spend the person's plan limits.

Thinking is off: it roughly tripled the cost and quadrupled the latency without moving the agreement. A provider the person selects is respected. When it is unavailable, scoring falls back to deterministic, never to another vendor, and the fallback is recorded.

### Laya

Laya (a local ModernBERT decision model) is detected and never used. It returns typed decisions without text spans, so it cannot quote a posting, and the gate publishes nothing without a quote. As a prefilter it would add PyTorch and a 421M-parameter checkpoint (about 2 GB of memory) to replace a full-text query that takes about 1.5 s and keeps every labelled fit. It was not benchmarked live on this machine for that reason; it has no production path unless a measurement gives it one.

## Hardening after review

An adversarial review of the pull request found no merge blocker, and these were fixed before merging:
- **The subscription CLIs receive an allowlisted environment**, not this process's. Posting text is third-party input, and a child holding every loaded key would be one prompt injection away from quoting one back. This also excludes every API-billing variable, including `CODEX_API_KEY`.
- **Codex runs with its tools disabled:** shell, exec, browser, apps, plugins and web search. Asked by a posting to run a command, it answers that it cannot.
- **The gate requires word boundaries**, refuses quotes made only of function words, and requires three words for work and other-signal quotes.
- **The per-sentence rule compares the sentence a quote sits in,** not the fragment. A phrase that appears again in a later sentence can still pay from there.
- **Each evaluation commits together with the rescore marks** for every job sharing its text, and a run row is always closed.
- **Budget reservations use UTF-8 bytes as the token upper bound**, and a metered provider without a recorded price is never run.
- **The semantic run and a manual rescore refuse to overlap.**
- **The interface translates every error and stop reason by code.**

## First run on the real corpus (2026-09-25)

The first live batches changed three things. Each was measured before it was kept.
- **A malformed finding no longer rejects its whole answer.**
  - 28 of the first 300 answers were rejected whole. They listed items the model had turned down, with strength `none`, or misspelt one key, and so lost every valid finding beside them.
  - Such a match is now dropped on its own and counted (`malformed_match`). A malformed answer is still rejected whole.
  - The next 300 answers had 1 rejection.
- **Contract 3.**
  - An item with a narrowing qualifier (business, revenue, CRM, AI, a domain) is `strong` only when the posting's work carries that qualifier. `strong` is for the centre of the role, and only supporting items are listed.
  - On the labelled set it changed agreement by one case (38 against 39 of 45). It is kept because it removed the malformed answers, which the evaluations had already paid for.
- **A partial finding pays like an incidental mention.**
  - The contract defines `partial` as a secondary duty or a close neighbour. That is an interpretation of adjacency, and it no longer pays like a statement in a secondary section.
  - On a fixed sample of 30 STRONG results from the first live batch (hand-labelled: 21 fits, 9 adjacent roles), STRONG held 19 of 28 fits before and 15 of 19 after. The adjacent roles that stayed STRONG fell from 9 to 4.
  - On the 45-case labelled set, false positives fell from 3 to 1 and misses rose from 2 to 3.
  - Fits that move out of STRONG land in GOOD and stay visible.

An existing installation shows its old scores until Search Fit is recalculated once. Evaluations stored under contract 2 are not reused under contract 3, so the first run after upgrading asks those postings again.

### The live result

After the deterministic rescore of 122,876 postings and 6,100 DeepSeek calls:
- **Evaluated:** 5,798 posting texts under contract 3, in relevance order. They are the top of the 13,948 that pass the prefilter.
- **Answers:** 30 rejected (28 of them before the per-match fix), 0 provider failures.
- **Tokens:** 13.5M input and 2.2M output.
- **Cost:** $6.68 at the peak rate. Off-peak calls cost half.

Visible in Discover (81,212 open postings that are not ruled out and not at a hidden level):

| | WEAK | MODERATE | GOOD | STRONG |
|---|---:|---:|---:|---:|
| Before (v4) | all | 0 | 0 | 0 |
| v5 deterministic | 78,884 | 2,259 | 68 | 1 |
| v5 with semantic findings | 76,643 | 2,909 | 1,250 | 410 |

Hand review of fixed samples of the resulting STRONG band found about seven in ten to be the work the person described. The rest were mostly data, BI or generic full-stack roles that match data and application-development items the person listed themselves. The WEAK samples held no fit.

## Subscriptions are not APIs

| Provider | How it is reached | Who pays |
|---|---|---|
| DeepSeek | `DEEPSEEK_API_KEY` in the root `.env` (addable from the settings panel), through the existing chat-completions adapter | Metered, estimated before a run, hard-stopped at the per-run budget |
| Claude Code | The person's signed-in `claude` executable (never a batch shim): no tools, no MCP servers, no settings, no session persistence, in an empty temporary directory, with an allowlisted environment | Their Claude subscription. No API-billing variable reaches the child, and an API-key-only sign-in is reported instead of used |
| Codex | The person's signed-in `codex exec` (never a batch shim): ephemeral, read-only sandbox, user config ignored, shell, exec, browser, apps, plugins and web search disabled, empty temporary directory, allowlisted environment | Their ChatGPT plan. `CODEX_API_KEY` never reaches the child, and an API-key login is reported instead of used |
| Laya | Detection only | n/a |

## Readiness

`match/readiness.py`:
- **NOT READY:** no work phrase. Discover shows "Not ready" instead of a number.
- **PARTIAL:** work stated; level or way of working not stated.
- **READY:** work, level and way of working all stated.

Tools are not required, because many occupations have none worth stating. Readiness does not depend on the corpus, because a rare phrase is rare intent, not incomplete setup.

## Revalidation after the corpus grew (2026-09-25)

The coverage work (#11, #16) added 31,739 postings after the previous semantic run: Greenhouse 16,072, Lever 7,581, Ashby 6,970, Himalayas 234, LinkedIn 215, and smaller ATS families. Evaluating all of them was never the plan. `career-agent semantic-match` gained two options for a revalidation:

- `--since last-run` (or an ISO time) limits the pool to postings collected after the last semantic run, plus any posting a targeted search returned.
- `--order priority` reads targeted-search postings first, then by deterministic Search Fit, then by full-text relevance. The default stays `relevance` everywhere, including the app.

The cache identity is unchanged: posting text, search-intent digest and semantic contract. A text already evaluated for the same intent is never paid for again.

**One run, DeepSeek, hard budget $2.00:**

- **Pool.** 8,781 of the new texts passed the prefilter. The run chose the top 2,000 in priority order.
- **Estimate.** 7.66M input tokens and $3.14 expected ($8.30 worst case). Earlier runs cost about $0.99 per 1,000 postings, below estimate. The budget stop is the hard limit.
- **Actual.** 1,419 calls, 3.82M input and 0.69M output tokens, **$1.97 spent** (stopped by the budget). 1,418 answers published, 1 refused by the publication gate, 0 failures. 1,830 postings rescored.

**Effect on the 31,739 new postings:**

| band | before | after |
|---|---|---|
| STRONG | 16 | 350 |
| GOOD | 208 | 999 |
| MODERATE | 3,495 | 2,553 |
| WEAK | 28,020 | 27,837 |

Main movements: MODERATE to GOOD 793, MODERATE to STRONG 230, GOOD to STRONG 74, WEAK to GOOD 72, WEAK to STRONG 30.

Share of new postings rated GOOD or STRONG:

| source | share |
|---|---|
| LinkedIn | 48.8% |
| Himalayas | 25.6% |
| Greenhouse | 4.8% |
| Lever | 3.0% |
| Ashby | 2.4% |

| lane | postings | GOOD or STRONG | share |
|---|---|---|---|
| Targeted (a search built from the person's intent returned it) | 409 | 165 | 40.3% |
| Exploratory | 31,330 | 1,184 | 3.8% |

**Suspicious high scores.** Some generic full-stack and product-engineer roles score 94 to 100. A fixed, rule-defined sample was inspected, covering:

- the top STRONG postings;
- full-stack roles at 90 or above;
- product-engineer roles at 90 or above;
- data and BI roles at 75 or above;
- GTM, revenue operations, business systems, MarTech, automation and AI-implementation titles.

What fills those scores:

- In those high scorers, the work component (25) and the tools component (20) are filled by four and three genuine PRIMARY semantic matches.
- The matches are to work items the search intent itself lists: API integration, business application development, data integration, internal tools, AI agents, and tools such as Node.js, SQL and REST APIs.
- A posting that centrally does four of the stated kinds of work is a fit for that intent.
- Two individual matches were looser than the contract intends. Those are single provider judgments, not a pattern.

Across all 747 STRONG postings, the provider's own role core (what the role mainly does) was checked:

- 63% mention integration, automation or business systems;
- 76% mention AI implementation;
- 21% mention GTM, revenue, CRM or marketing;
- only 21 of 710 (3%) are primarily generic product or full-stack engineering with none of those.

**Decision: Search Fit is unchanged.** No scoring component was shown to overpay in general; the breadth comes from the search intent itself. Two changes would narrow what counts, and neither is a scoring change:

- narrower work phrases, since some of the current ones describe generic engineering;
- role anchors, which steer retrieval.

Title-fit points stay out, as the role-alignment benchmark decided. Semantic role-core alignment, reading the whole description, was not promoted to ranking. The owner has named no role anchors, so there is no named role to align to, and the earlier benchmark found only ALIGNED reliable.
