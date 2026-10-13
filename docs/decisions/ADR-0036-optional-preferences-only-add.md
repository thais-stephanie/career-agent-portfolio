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

**Optional preferences.** Each phrase the posting shows adds a fixed share of
its component, `max / COUNTED_SIGNALS` (4 for work, 3 for tools, 3 for the
other signals), times its prominence (a central duty pays in full, an
incidental mention its multiplier: that is how much of it the posting shows).
The component's maximum is the explicit limit. A phrase the posting does not
mention adds nothing and takes nothing away, and naming one more phrase can
never lower a posting's points. Weights only decide which matches count first.
The tools guard is removed. A component the person never configured stays out
of the denominator, as before; the share of one that is configured no longer
depends on how many phrases were named.

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

* Measured trade-off, recorded rather than hidden: on the 2026-09-27
  development set, 21 of 29 false positives at GOOD or above had only
  non-central work evidence, and the guard was what kept their tools at half
  (docs/SEMANTIC_MATCHING.md, "Evidence quality"). Without it a posting that
  names three of the person's tools and none of their work earns the whole
  tools component. The owner chose the monotonic rule knowingly; a posting of
  that shape is now visible in the drawer as "the ad mentions none" of the
  work.
* A person who names fewer phrases than `COUNTED_SIGNALS` cannot fill that
  component any more (one phrase found is a quarter of the work component).
  Before, one named and found filled it, and naming a second one lowered it.
* Old scores keep their old explanation fields; the drawer says a score was
  made with earlier rules instead of explaining it with the new ones.
