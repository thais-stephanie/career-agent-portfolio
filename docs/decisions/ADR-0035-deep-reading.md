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
* `deep_assess.assess` turns what survived into three separate answers and
  one recommendation (addendum 1 below; the first rule, `priority` with
  HIGH/MEDIUM/LOW, is replaced). A recommendation is never a probability of
  being hired and never a comparison with the local score.
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

## Addendum 1 (2026-10-10): three answers, not one level

The first pilot showed the single level over-read the stored observations:
`partial` counted as support (MEDIUM with nothing supported), a listed skill
("Claude Code") counted as "deep experience", a list of four technologies
counted as shown because two were, and dispositions and English sat in the
technical ratio. `semantic/deep_assess.py` (`assess-1`) re-reads the SAME
stored reading, with no new call, and answers:

1. Work: the reading's `work_relation` (central, secondary, neighbour,
   unrelated, unclear), unchanged.
2. History: each MANDATORY technical requirement or credential gets a
   status from the reading's verdict, the KIND of evidence it cited and what
   the requirement asks:
   * a listed SKILL or TOOL states familiarity; it meets "familiarity with"
     and nothing stronger (`declared`); it never shows experience, a level,
     years, authorship or production use;
   * a described experience (employment, project) shows practice; a level
     word (deep, strong, expert, advanced) or a number of years is never
     confirmed by a line (`partial`, with the note);
   * a conjunctive list ("A, B, and C"; alternatives with "or", "such as",
     "equivalent" are one requirement) is split into parts, and each part is
     marked as named or not by the cited evidence's words; the unnamed ones
     stay unknown;
   * where the stored reading cannot be checked (a heading whose list was not
     quoted, a list none of whose parts the cited evidence names), the
     requirement is `review`, never reinterpreted;
   * `not_shown` stays "not in what was sent", never absent; nothing is
     compared when evidence was not sent.
   The summary (well / partly / little shown) weighs shown 1, partly,
   declared and review 0.5, not in what was sent 0, at 0.75 and 0.4, and is
   always printed beside the counts it came from.
3. To confirm: conditions verbatim, languages (from conditions and from
   requirements naming a language) and dispositions turned into questions,
   none of them in the technical ratio.

A CENTRAL requirement is a mandatory credential, or a mandatory requirement
naming a tool the reading marked `core`. One not in what was sent is shown
first and moves the recommendation: STRONG, PREPARE, CHECK_FIRST,
NOT_COMPARED, TO_CONFIRM, LOW, OTHER_WORK, derived from work first, then
central gaps, then history. Neighbouring work is LOW whatever the history.

The contract (`deep-1`) did not change, so no stored reading went stale. A
reading that separates parts itself would be a contract change (`deep-2`)
and needs a pilot of its own.

## Addendum 2 (2026-10-11): DeepSeek through its API, with a budget the person sets

The owner corrected the first reading of this ADR: preferring a subscription
was a cost preference, not a ban on paid APIs. What replaces "subscription
only":

* The panel offers the providers measured on this contract (Claude Code,
  Codex, DeepSeek), each with its model, its connection (subscription or API
  key), whether this computer is ready for it (a local check) and what it
  costs. The choice is saved per profile (`deep_provider` in
  `semantic.local.yaml`) and kept until changed. Nothing ever falls back to
  another provider or to another model of the same one: an answer reporting
  another model is not published and pauses the job; a resume on another
  model is refused.
* Consent stays per profile AND per provider (`share_evidence_with` now
  accepts `deepseek`); consent for Claude is not consent for DeepSeek, and a
  configured key is not consent.
* A metered provider runs only at a recorded price for its exact model
  (`llm/pricing.py`: deepseek-flash, re-read 2026-10-10 at
  api-docs.deepseek.com/quick_start/pricing: 0.30 / 0.006 cached / 1.20 USD
  per million tokens at peak, half off-peak) and inside a budget the person
  approves in the preview. The server enforces it: before each call the worst
  case is reserved (every UTF-8 byte a token, no cache, the whole 8,000-token
  answer ceiling, peak rate); after it, the cost is accounted from the usage
  DeepSeek reports (cache hits at the cache rate, still peak). The job pauses
  when the next worst case does not fit, and when a call's usage cannot be
  accounted for (no usage reported, or a timeout that may have been billed),
  counting the whole reservation.
* No total cap: the person chooses how many postings, up to the readable
  postings of the main list not yet read by that provider. A job (migration
  0052: `deep_job`, `deep_attempt`) holds the approved plan, read in batches
  of 25 without asking again; pause, cancel and resume continue the same
  plan; a posting already read, or answered and rejected, is never asked
  again. The plan takes the three strata in turn, so a job stopped halfway
  is still diverse.
* DeepSeek's JSON mode does not hold an answer to a schema, so the answer's
  JSON Schema is written into its instructions (same schema the contract
  identity hashes). The message is sent without spaces after separators
  (about 4% fewer characters) with what every posting shares first, so the
  provider's prefix cache can serve it. What is cut or left out of a packet
  is counted in the preview.
* The estimate is labelled as one: input tokens are characters / 3, output
  assumes 3,300 tokens per posting (the Claude Code pilot's mean; DeepSeek's
  own length is not measured yet), the rate depends on the hour of each call.


## Addendum 3 (2026-10-19): a reading is of an advert under a title

A reading was identified by the advert alone (content hash, profile digest,
contract, provider, model). The reader is sent the TITLE and the advert, never
the employer, so the identity left out half of what it was asked. On the
owner's list, 210 postings carried an advert another posting of the same
employer had been read under another title ("Business Process Consultant"
read, "AI Workflow Analyst" skipped): the planner counted them as read, Find
jobs (which looks readings up by posting) showed them unread, and no job would
ever have read them. Reading one would have REPLACED its sibling's reading,
because the store upserts on that key.

Decision: the identity is (content hash, title, profile digest, contract,
provider, model), the title exactly as sent (`job.title`), never normalised:
a normalisation would declare equivalent two questions the reader saw as
different, which is the defect itself. Not `job_id`: a repost of the same
vacancy (same advert, same title, new id) is the same question and keeps
sharing one reading.

* Migration 0058 rebuilds `deep_review` (the one way SQLite changes a UNIQUE
  constraint): every row copied with its id and every value, `title` filled
  from the posting each reading was asked for. The new key is the old key
  plus a column, so the copy cannot collide. `deep_attempt` gains `title`
  additively, so "rejected" and "answered in this job" ask the same pair.
* The planner skips, dedupes a plan and resumes by (advert, title)
  (`deep_run.read_key`). A new reading's id includes the title; stored ids do
  not move.
* Find jobs shows, for a posting, the newest reading of its current advert
  under its current title, whichever posting it was asked for (a repost shows
  its twin's reading, as the planner skips it); else its own older reading,
  out of date. A changed title is a changed posting.

## Addendum 4 (2026-10-19): the plan is read highest band first

The three strata stay exactly as decided above: which postings a plan holds,
and in which stratum, did not change (40% top local, 25% lexical, the rest a
control sample of everything else by a hash of the job id). What changed is
the ORDER a job reads its plan in, which is the order its calls are spent in.

Until now the plan took one posting of each stratum in turn (1 : 1 : 1). That
kept a job stopped halfway diverse, and it also spent a third of every call on
the control sample, mostly the lowest local scores: on the owner's list a
1,000-posting plan read postings at 89%, 14% and 94% in a row, and the first
300 calls averaged a local score of 54 with 96 of them in the weak band.

Decision: the diversity between strata is kept; the operational order now
optimises cost against value.

* Bands of the local fit first, highest first (the person's own band edges:
  strong, good, moderate, weak).
* Inside a band, the strata in turns of 8 : 5 : 7 (the shares above, in
  twentieths); a stratum with nothing left in the band gives its turn to the
  others.
* Inside a stratum, highest local score first, then the job id.

The price is stated rather than hidden: the control sample is still chosen
from the whole rest, but its low-scoring part is read last, so what it shows
about what the local score misses is complete only when a job is read to the
end. On the same 1,000-posting plan (measured 2026-10-19, no model call), the
first 300 calls average 81 and are all in the strong band; the 1,000 postings
and their strata are the same. The Addendum 2 sentence "the plan
takes the three strata in turn" is superseded by this one. A job already
approved keeps the plan it stored; the order applies to plans made from now
on (`deep_run.consumption_order`).

## Addendum 5 (2026-10-19): the work fit outlives an evidence change

On 2026-10-19 every one of the owner's 823 readings went out of date at once.
Not because of her search: salary, remote or hybrid and the other conditions
are neither in the profile digest nor sent to the reader. Because 51 evidence
revisions (33 new claims, 18 edits) were confirmed after the readings, and the
profile digest hashed the intent, the levels and the evidence together. The
digests of all 823 were reproduced from today's intent and levels with the
evidence rebuilt as it stood at each reading, so that cause is proven.

**The pilot that decided it.** Does the evidence in the prompt move the
work-relation judgement? Pre-registered rule, then 30 of the owner's Claude
readings stratified by relation (central 7, secondary 7, neighbour 6,
unrelated 6, unclear 4), read again with the same contract, model, intent,
levels, title and posting (`out/experiments/anchoring-pilot-2026-10-19`,
nothing written to the database):

* without evidence: 23/30 agree with the original, 5 cross between
  central/secondary and neighbour/unrelated/unclear;
* noise control, with the evidence originally sent (the packet rebuilt and
  checked identical to the stored alias map, 30 of 30): 25/30, 3 crossings.

The rule (a difference of at most 2 in both) reads this as predominantly
noise, at the edge of the tolerance. The changes sit on the secondary <->
neighbour edge in both arms, and only one posting changed in both. The finding
that holds either way: one rereading with the SAME input moves about one
relation in six, so rereading is not a more certain answer than reusing.

**Decision.** Partial reuse, conservative:

* Migration 0059 adds `intent_digest` (intent and levels) and
  `evidence_digest` (sharing, claims by key and revision) beside
  `profile_digest`, which stays the identity. Additive and nullable.
* Rows written before it are filled by `career-agent deep-backfill-digests`
  ONLY when the evidence rebuilt as of the reading reproduces the stored
  profile digest; any other row stays NULL and is fully out of date whenever
  its profile digest moved.
* A reading is `current`, `evidence` (only the evidence changed: the work fit
  stands) or `stale` (`deep_run.freshness`, one rule for the list, the panel,
  the drawer and the planner).
* In the `evidence` state the recommendation is current only when it cannot
  move (`deep_assess.holds_without_evidence_change`): the work does not align
  (LOW, OTHER_WORK, TO_CONFIRM are decided before history), or every counted
  requirement was supported by cited items still confirmed at the revision
  sent and evidence is shared now. Otherwise the level is out of date.
* The planner skips a reading whose recommendation is current in that sense
  and reads the rest again in full. The level filters and the deep sort use
  only current recommendations; Find jobs labels the three states.

Not built: re-asking only the evidence half of a reading (a separate contract
with its own pilot), and splitting the reading in two calls.

## Addendum 6 (2026-10-20): calls in flight at once, answers handled in order

**Context.** The overnight job of 2026-10-20 read 1,500 postings in 6.13 h. The
gap between calls equalled the provider's own latency (14 s median), so
preparation, verification and storage cost nothing measurable: throughput could
only move with more than one call in flight. A Claude Code call is a separate,
stateless `claude -p` process.

**Measured.** The same 8 postings, the exact requests of that job, nothing
stored: 1 at a time 214 readings an hour, 2 -> 343, 3 -> 553, 4 -> 677; mean
latency 16 to 20 s at every level, 0 rejected, 0 failed, 0 limits. The readings
differed between rounds on 3 of the 8 postings, the same model noise the
anchoring control measured, and not in a pattern that follows the level.

**Decision.** For a SUBSCRIPTION provider, `deep_run` keeps up to
`deep_concurrency` calls in flight (default 2, `CAREER_AGENT_DEEP_CONCURRENCY`,
1 to 4). Only the call runs on worker threads; every answer is verified, stored,
counted and checkpointed on the job's own thread, in plan order, so the plan,
the stored rows and their order do not depend on the number. A metered provider
stays at one call at a time, because each call reserves its worst case against
the budget before it is sent. A pause or a stop leaves at most n - 1 answers
unread; they were never stored, so a resume asks them again.

Not decided here: a default above 2. The 8-call sample cannot show the
subscription's limit over hours; raise it after a longer run reports no limits.

## Addendum 7 (2026-10-20): the relation asks for the activity, not the stack

**Context.** An audit of the 1,500 overnight readings found the reader moving
the person's own work to "neighbour" when it ran on another platform or with
more code: a Salesforce engineer or admin "rather than HubSpot", a Zendesk
admin, an Oracle integration consultant. Neighbour reads LOW whatever the
evidence says.

**Decision.** The instructions now say to judge the main ACTIVITY, never
whether the tools match: the same activity on another CRM, automation,
integration, ERP or helpdesk platform, or done with more code, stays central
or secondary, and a different tool is a requirement to report. Neighbour is
kept for another job: software product engineering, data engineering,
security, infrastructure, marketing campaigns, quota-carrying sales, product
management or people management.

**Measured, 18 calls, nothing stored.** Nine platform cases read twice: with
the old instructions 9 of 9 stayed neighbour (as stored); with the new ones 4
moved to secondary (both Salesforce roles, the Oracle consultant, the Zendesk
admin) and 5 stayed neighbour (Python engineering, D365 ERP, a developer
platform's forward-deployed engineer, a support-team manager, design
operations). On 36 boundary cases the new instructions moved 4 relations,
the same as a rerun with the old ones (model noise): security, software and
sales stayed neighbour.

**Consequence.** The contract identity moves, so every stored reading reads
out of date until it is read again. Merging this is the owner's decision.
