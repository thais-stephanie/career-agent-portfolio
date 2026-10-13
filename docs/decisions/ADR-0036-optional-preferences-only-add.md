# ADR-0036: An optional preference only adds; a USD period and a contractor engagement have owner defaults

Status: accepted (owner, 2026-10-13: the rules below were given as product
rules, to be implemented without a further confirmation). Revised the same
day after the owner's decisions A1 and B1 (below). Moves
`READER_IDENTITY` to `readers-11` and `MATCH_SCHEMA_VERSION` to 14; every
stored score is read again on the next recalculation.

## Context

"Work you want" and "Tools and methods" are things a person would like. Three
rules in `match/score.py` let them LOWER a posting:

1. Each phrase was worth `max / min(named, COUNTED_SIGNALS)`, so naming a
   third tool the posting did not mention cut the value of the two it did.
2. Each phrase was scaled by its weight over the heaviest weight, so adding a
   phrase weighted above the rest shrank every other one.
3. The tools guard held the tools at half (then in proportion to the work
   found) when the work named was not found as a central duty, so naming work
   a posting did not mention lowered its tools.

The drawer then listed a partly met preference under "What doesn't fit yet:
these lower the %", with "The ad has some of what you asked for here, not all
of it", which is true of almost every posting and said nothing about which.

## Decision

**Optional preferences: paid by evidence, never by how much was asked.**
A component is filled by PIECES OF EVIDENCE: `COUNTED_SIGNALS` of them (4 for
work, 3 for tools, 3 for the other signals) fill it, each worth
`max / COUNTED_SIGNALS` times its strength. Each preference the posting shows
is ONE piece, at the strength of WHERE it shows best: a duty in the role's own
section in full, a requirement (the requirements section) or an incidental
mention at its prominence multiplier. How often it shows does not count, so
repeating a preference, or splitting the same content into more items, adds
nothing, and a concise posting scores as the same content written out in many
bullets. Revised by the owner on 2026-10-13 (decision B1): ONE clear central
match of the work you want (a duty in the role's own section, or a strong
semantic finding) completes "Work you want" on its own (`CENTRAL_PIECES`).
One sentence pays for at most half the preferences through the person's
phrases and one through a semantic finding; a semantic finding never adds a
second piece to a phrase already found. Evidence is taken strongest first.
Hence:

* a preference the posting does not mention takes nothing away;
* once a component has a preference, naming one more never lowers a posting;
* a small selection can complete its component: one central match for work;
* nothing depends on how many phrases were named nor on their weights
  (weights only decide which evidence counts first); the maximum is the
  limit.

**The first preference of a component brings a new criterion in** (owner,
2026-10-13, decision A1). A component with no preference stays out of the
percentage. Naming its first preference brings it in, so that one step can
move a score either way, including the first work preference starting the
tools rule below. The interface says so in "How this score works".

**Tools read where the work is.** Three cases, kept apart. A tool in a
sentence about work the person asked to AVOID (a soft-penalty phrase fires in
that sentence) is evidence of other work and earns nothing. Where the work the
person wants is a duty or a requirement of the posting (a counted work match
above INCIDENTAL), the tools pay in full. Where the person named work and the
posting shows it only in passing or not at all, the tools together earn at
most half their component (`TOOLS_WITHOUT_WORK_SHARE`): a common tool says
little about whose work it serves. A person who named NO work is not "the work
was not found": the tools have no limit until the first work preference.

What stays: gates and hard exclusions (they never were part of the %), soft
penalties ("avoid" phrases), an unwanted contract, a level outside the
preferred ones, pay below the target and an avoided way of working. Those are
the only rows the drawer now lists under "These lower the %".

**Contractor default.** When the employer is evidently American (its curated
headquarters in the registry, or the advert saying so in words; never a
currency or the advert's language) and the posting states a hiring scope in
Latin America, and the advert states no engagement, the EFFECTIVE engagement
is a contractor: "Probably Contractor" / "Provavelmente Contractor". The
stated reading, the effective one and both quotes are stored apart
(`EmploymentReading.effective`, `default_us_evidence`,
`default_latam_evidence`). A stated engagement of any kind wins. Full-time and
part-time are a working pattern and no longer read as an engagement. The
default reaches the contract preference and the Contract filter, never a gate.

**Pay period defaults (USD only).** A figure with no period first takes a
period the advert's own text states. Otherwise, for a posting hiring in Latin
America, every figure below 7,000 is a month's pay and every figure above
30,000 a year's; from one to the other, both included, and a band crossing
either, stays unknown. The period is marked inferred (`period_inferred`) and
used for normalisation and matching; the stated figures are unchanged. Another
currency and an hourly rate get no default. The interface shows one figure
for a band of one, and a yearly figure as its monthly equivalent (divided by
12, rounded only on screen), with the yearly figure in the detail view.

## Consequences

* Measured on the 2026-09-27 benchmark (175 postings, blind double-reviewed
  labels, private; read-only replay with the search configuration of that
  time and the stored semantic findings, no model call), GOOD or above:

  | Rule | Dev false positives | Dev valid | Holdout false positives | Holdout valid |
  |---|---:|---:|---:|---:|
  | 858f22d (guard opening in proportion to work) | 18 | 65 | 9 | 27 |
  | fcf80c2 (no guard, fixed shares) | 31 | 65 | 16 | 31 |
  | 65d23bd (repeated duties as pieces) | 19 | 65 | 10 | 28 |
  | Distinct pieces, central = one piece (B2) | 19 | 64 | 9 | 28 |
  | **This rule (A1 + B1)** | **22** | **65** | **10** | **28** |

  Against B2, three development false positives and one valid posting cross
  GOOD, each through ONE central work match filling the work component.

  **Exploratory, not a validation.** The holdout was consulted while the
  variants of this round were being chosen, so these figures cannot stand as
  an independent final check of the rule; a fresh, unseen set would be needed
  for that.
* Monotonicity on the same sample (every work and tool phrase removed one at
  a time, and each component emptied): 0 of the checks with at least one
  preference left raised a score; every rise came from emptying a component,
  the accepted first-preference transition.
* Old scores keep their old explanation fields; the drawer says a score was
  made with earlier rules instead of explaining it with the new ones.
* The Contract filter reads `job_match.engagement` (migration 0053): the
  engagement stated, or the contractor default. `employment_type` keeps the
  board's job type, and the Hours filter offers only full-time and
  part-time from it. Rows scored before readers-11 have no engagement until
  their next scoring.
