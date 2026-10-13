# ADR-0036: An optional preference only adds; a USD period and a contractor engagement have owner defaults

Status: accepted (owner, 2026-10-13: the rules below were given as product
rules, to be implemented without a further confirmation). Moves
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
`max / COUNTED_SIGNALS` times its strength (a central duty in full, an
incidental mention its prominence multiplier). The first sentence a preference
shows in is a piece; a further distinct sentence of the SAME preference is
another full piece only when it is a duty in the role's own section. One
sentence pays at most half the pieces through the person's phrases and one
through a semantic finding; a semantic finding never adds a second piece to a
phrase already found. Evidence is taken strongest first, so a weak mention
never takes the place of a stronger duty. Hence:

* a preference the posting does not mention brings no evidence and takes
  nothing away, and naming one more preference never lowers a posting;
* a small selection can fill its component when the posting shows enough of
  it (one wanted activity that is a duty in four bullets fills the work
  component);
* nothing depends on how many phrases were named nor on their weights
  (weights only decide which evidence counts first); the maximum is the
  limit.

**Tools read where the work is.** Three cases, kept apart. A tool in a
sentence about work the person asked to AVOID (a soft-penalty phrase fires in
that sentence) is evidence of other work and earns nothing. Where the work the
person wants is a CENTRAL duty of the posting (a counted work match at PRIMARY
or SECONDARY prominence), the tools pay in full. Where the person named work
and the posting shows none of it as a central duty, the tools together earn at
most half their component (`TOOLS_WITHOUT_WORK_SHARE`): a common tool says
little about whose work it serves. A person who named NO work is not "the work
was not found": nothing was asked, so the tools have no limit. That is the one
place where naming a phrase can lower a posting: the FIRST work preference,
which tells the product what work is wanted, can hold the tools of a posting
whose duties are other work. A second, third or tenth phrase never can.

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
  | **This rule** | **19** | **65** | **10** | **28** |

  Four postings cross the GOOD line against 858f22d: two false positives and
  two valid postings rise, each through a duty repeated in the role section.
* Removing any one phrase from that search raised no posting's score in
  7,000 checks (175 postings, every work and tool phrase).
* Old scores keep their old explanation fields; the drawer says a score was
  made with earlier rules instead of explaining it with the new ones.
* The Contract filter reads `job_match.engagement` (migration 0053): the
  engagement stated, or the contractor default. `employment_type` keeps the
  board's job type, and the Hours filter offers only full-time and
  part-time from it. Rows scored before readers-11 have no engagement until
  their next scoring.
