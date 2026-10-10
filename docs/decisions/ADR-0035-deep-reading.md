# ADR-0035: The deep reading sits beside the score, and the code decides its level

Status: accepted (owner, 2026-10-10: architecture approved with adjustments;
evidence sharing authorised for the owner's profile, Claude Code subscription
only). Extends ADR-0001 to a second semantic contract. Leaves the semantic
findings contract (`semantic-3`) and the local score unchanged.

## Context

The semantic findings path (`semantic/runner.py`, contract `semantic-3`)
could only RAISE a posting: in `match/score.py` each signal takes the larger
of the literal phrase and the model's finding, and a "none" verdict was
stored and never read. It answered one question (do I want this work?) and,
by design, never saw the person's history. Its candidates were chosen by a
full-text proximity filter, so a posting describing the work in other words
never reached the model, and nothing measured what that filter missed.
Selecting a provider in Settings only wrote a file; runs started from a
disclosure in Settings that the Find Jobs page never mentioned.

## Decision

A second, separate contract: the DEEP READING (`semantic/deep.py`,
`deep-1`). Three questions are kept apart:

1. Do I want this work? The model reports the primary activity, secondary
   duties and tool importance, and how the primary activity relates to the
   person's work items (`central`, `secondary`, `neighbour`, `unrelated`,
   `unclear`), with quotes.
2. Does my history support it? Each stated requirement is `mandatory`,
   `desirable` or `unclear`, and is set against the CONFIRMED evidence that
   was sent, by local id: `supported`, `partial`, `not_shown`, `unclear`.
   `not_shown` is the absence of proof in what was sent, never proof of
   absence.
3. Can I take it on the stated conditions? Not decided here. The gates own
   eligibility; the model may only quote a condition, shown as "to confirm".

The model observes; the code decides (ADR-0001):

* `verify` keeps a quote only when the posting contains it (the same
  `semantic.gate.locate` the findings path uses), an intent id only when it
  was sent, an evidence id only when it was in THIS posting's packet. A
  positive claim whose support did not survive becomes `unclear`, never a
  weaker positive and never a negative. Free-text summaries are shown as
  "AI reading", never as evidence.
* `priority` maps what survived to an ABSOLUTE level with reason codes:
  HIGH, MEDIUM, TO_CONFIRM, LOW, OTHER_WORK. The rules are in the function's
  docstring and in the interface. A level is never a probability of being
  hired and never a comparison with the local score.
* The level is stored nowhere and blended into nothing: it is recomputed
  from the verified observations on read, so a rule change needs no
  migration. `job_match` is never written by a deep run.

The view: results are grouped by level and ordered by local score inside a
level; each row says how it compares with the local band (`raises`,
`lowers`, `agrees`, `open`). Postings without a reading stay in the main
list in the local order and are COUNTED in the panel, never ranked below
read ones. The owner rejected "promoted, then kept, then demoted": a
promotion is a change relative to the local score, not a level.

Selection (`semantic/deep_run.py`): only the main list (eligible where the
person lives; to-confirm locations only when asked), one copy per reposted
role elected as the list elects it, readable text only. Three strata:
`top_local` (40%), `lexical` (25%), `control` (the rest, by a hash of the
job id). What the reading raises in `control` is what the local ranking
misses.

Privacy and billing:

* Evidence leaves only when the profile names the provider in
  `share_evidence_with` (`semantic.local.yaml`), which accepts only
  `claude_code` and `codex`. Only confirmed claims: every skill and tool
  (at most 80), up to 10 experience lines sharing words with the posting,
  and a summary of up to 10 others (120 characters each, most recent first),
  so work described in other words still reaches the reader; local ids
  (E1...), no employer field, no dates (a duration in years; years in free
  text become `[year]`), contacts, links and the display name redacted.
  `sent_json` keeps the alias map (keys and revisions, never text) so a
  cited id is resolved later in THIS profile's ledger; one that changed is
  shown as changed.
* Only a SUBSCRIPTION provider runs a deep reading, chosen BY NAME (never
  Auto); a metered provider is refused before anything is sent. A plan
  limit pauses the run at once; nothing moves to another provider.
* A run starts only from `POST /api/deep/run` with `confirm: true`, after a
  preview that computes, locally, exactly what would be sent.
* Claude Code's child keeps the existing allowlisted environment, so
  `ANTHROPIC_API_KEY` (which `-p` would otherwise always prefer) never
  reaches it, and the sign-in must be `claude.ai`. Whether the account's
  "extra usage" is on cannot be read from the CLI; the interface says so.

## Consequences

* Migration 0051 adds `deep_review` (profile scope); runs reuse
  `semantic_run` with mode `deep`.
* A cancel now ends the CLI call in flight (`local_cli.terminate_running`),
  for both contracts; a limit pauses the findings runner at once too.
* The findings path (`semantic-3`) is untouched and still only raises. The
  owner's profile has never run it (0 evaluations on 2026-10-10).
* A review is out of date when the posting text, the instructions or the
  profile it was asked against changed; it then shows the level it had
  "when read", never a current priority.
* Not built: a deep reading through a metered API; a list filter for "read /
  not read"; re-reading stale reviews from the interface (`reread_stale`
  exists in `select`, unexposed).
