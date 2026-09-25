# Role-core alignment: benchmark and decision

`src/career_agent/match/role_core.py` classifies a posting TITLE against the roles a person named (their role anchors and the planner's aliases): ALIGNED, ADJACENT, OUTSIDE or UNRESOLVED. It is a read-only prototype. Nothing that scores, gates or hides a posting imports it, and `tests/unit/test_role_core.py` asserts that.

## Benchmark (synthetic, 2026-09-25)

`personas.yaml`: nine invented people (hair stylist, ICU nurse, math teacher, sales representative, SDR, account executive, customer success manager, accountant, backend engineer) and 90 titles. The labels were written by hand before the classifier ran, and they were never edited to fit it.

| truth \ classified | ALIGNED | ADJACENT | OUTSIDE | UNRESOLVED |
|---|---|---|---|---|
| ALIGNED (30) | 27 | 3 | 0 | 0 |
| ADJACENT (31) | 0 | 16 | 15 | 0 |
| OUTSIDE (26) | 0 | 4 | 22 | 0 |
| UNRESOLVED (3) | 0 | 0 | 0 | 3 |

Accuracy is 68 of 90 (75.6%).

- **ALIGNED is precise:** 27 of 27 titles called ALIGNED really are the named role, and 90% of those roles are found.
- **The boundary between ADJACENT and OUTSIDE is not reliable.** Half of the neighbouring roles were called OUTSIDE: Barber next to Hair Stylist, Bookkeeper next to Accountant, Onboarding Specialist next to Customer Success Manager. Seeing those takes knowledge of occupations that words in a title do not carry.
- **The same-role-noun rule makes the opposite mistake:** Construction Manager comes out next to Customer Success Manager, and Civil Engineer next to Backend Engineer.

## Real workspace (read-only, aggregates only)

The maintainer's workspace names no role yet, only work phrases. Measured against work phrases alone, 94% of postings Search Fit rated STRONG, and 97% rated GOOD, have a title that repeats none of them. Work phrases describe work, not titles, which is exactly why Search Fit reads the whole advert.

So without a named role the classifier now answers UNRESOLVED ("cannot tell") instead of OUTSIDE.

## Decision

**No scoring change.** No title-fit points, no demotion, no filter.

A signal that calls half of the neighbouring roles "outside" would push down exactly the moves between neighbouring roles that people make most often. Without named roles it has nothing to say at all.

ALIGNED is the only reliable output. If it is used later, the use must be one that cannot hide a posting, such as a label or a tie-break, and it must be measured again on real named roles first.

Run it again with `python -m pytest tests/unit/test_role_core.py -q`. The test enforces the floors above, so a later rule change that trades precision for coverage has to say so.
