"""How many postings each configured signal actually reaches.

WHY A PHRASE EDITOR NEEDS THIS
------------------------------
`/api/preferences` lets the owner edit the phrase groups that decide what
counts as the work she wants. It showed her the phrases and nothing else, so
every edit was blind: add a phrase, remove a phrase, and find out what happened
by rescoring nineteen thousand postings and looking at the list.

Measured on her corpus 2026-09-08, that blindness was hiding real facts:

    documentation_practice   6,506 postings   33.4%
    scripting                5,986            30.7%
    tool_stack               5,778            29.7%
    ...
    crm_architecture            81             0.42%
    revops_infrastructure       46             0.24%
    selling_hubspot              0             0%

`selling_hubspot` matches nothing in nineteen thousand postings. That is worth
knowing before deciding whether to reword it or remove it -- and it is not a
verdict, because "no posting says this" and "this work does not exist" are
different statements and only she can tell them apart.

WHERE THE NUMBER COMES FROM
---------------------------
`job_match.membership` holds `|fired:<signal>|` for every signal that matched,
written when the posting was scored. It contains only identifiers this system
generated, never posting text -- which is the property that lets a filter run
against it safely, and the same property makes it safe to count here.

Counted against the SERVED population, so the number describes the list she is
actually looking at rather than a revision nobody is being shown.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass

#: How `membership` encodes a signal that matched. A pipe-delimited token, so
#: `fired:data_sync` cannot be mistaken for a prefix of `fired:data_sync_v2`.
FIRED = "fired:"


def signal_reach(conn: sqlite3.Connection, config_id: str, config_version: int) -> dict[str, int]:
    """Postings in this scored population that each signal matched.

    One pass over `membership`. On the owner's corpus that is 19,469 short
    strings and about a second; a per-signal `LIKE` would be forty-nine scans
    of the same column, which is the shape `facets()` was already rescued from
    once.
    """
    return signal_reach_details(conn, config_id, config_version).lexical


@dataclass(frozen=True)
class SignalReach:
    lexical: dict[str, int]
    positive_body: dict[str, int] | None
    population: int


def signal_reach_details(
    conn: sqlite3.Connection,
    config_id: str,
    config_version: int,
) -> SignalReach:
    """One bounded-column scan; never parse explanation JSON in a UI request.

    Old score versions lack body-positive tokens. Report that as unmeasured,
    not zero, until every row in this population carries the new marker.
    """
    rows = conn.execute(
        "SELECT m.membership FROM job_match m JOIN job j ON j.id=m.job_id "
        "WHERE m.config_id=? AND m.config_version=? "
        "AND j.closed_at IS NULL AND j.content_hash=m.content_hash",
        (config_id, config_version),
    ).fetchall()

    counted: Counter[str] = Counter()
    body: Counter[str] = Counter()
    body_measured = 0
    for row in rows:
        membership = row[0] or ""
        body_measured += "|body_scoring_measured:v1|" in membership
        for token in membership.split("|"):
            if token.startswith(FIRED):
                counted[token[len(FIRED) :]] += 1
            elif token.startswith("body_positive:"):
                body[token[len("body_positive:") :]] += 1
    return SignalReach(
        dict(counted), dict(body) if rows and body_measured == len(rows) else None, len(rows)
    )


#: The categories `membership` actually records. A lexicon signal that matched
#: writes `|fired:<id>|` when the posting is scored; a HARD EXCLUSION does not.
#: An exclusion is a gate outcome, decided by `match/gates.py` against the
#: posting text, and it leaves no token in this column.
MEASURED_CATEGORIES = frozenset({"desired", "negative"})


def reach_summary(
    reach: dict[str, int],
    declared: list[tuple[str, str]],
    population: int,
    *,
    positive_body: dict[str, int] | None = None,
) -> list[dict]:
    """Every declared signal with its reach, or an honest None.

    Two different facts, kept apart:

    * a MEASURED signal missing from `reach` fired zero times, and zero is the
      number most worth showing -- `selling_hubspot` matching nothing across
      19,469 postings is the finding, not a gap;
    * a HARD EXCLUSION is not measured here at all, and reporting it as zero
      would say "this phrase matches nothing" about a phrase this count has
      never looked at. The first version did exactly that for all five of the
      owner's blockers, including the one carrying `we do not sponsor`, which
      demonstrably does refuse postings.

    So an unmeasured signal reports None, and the screen has to say "not
    measured" rather than draw a bar at zero.
    """
    out = []
    for name, category in declared:
        measured = category in MEASURED_CATEGORIES
        found = reach.get(name, 0) if measured else None
        out.append(
            {
                "signal": name,
                "category": category,
                "measured": measured,
                "postings": found,
                "positive_body_postings": (
                    positive_body.get(name, 0)
                    if positive_body is not None and category == "desired"
                    else None
                ),
                # A share only where there is a real denominator AND a real
                # numerator. A percentage over an unscored corpus, or over a
                # count nobody took, would be a number with no meaning printed
                # beside ones that have some.
                "share": (
                    round(100 * found / population, 2)
                    if measured and population and found is not None
                    else None
                ),
            }
        )
    return out
