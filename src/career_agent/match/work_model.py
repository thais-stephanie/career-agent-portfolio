"""The way of working a posting states: REMOTE, HYBRID or ONSITE, or nothing.

One reading, used by the facts a card shows and by the Search Fit component
that prices a candidate's work-model preference, so the two can never
disagree about what a posting said.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - the engine imports this module
    from career_agent.match.engine import JobFacts


def read_work_model(job: JobFacts) -> str | None:
    """REMOTE / HYBRID / ONSITE, read from what the board itself said.

    **The structured field first.** A board that asks the employer to pick one
    has an answer, and reading three words out of a free-text location when a
    form field already holds the answer is inference standing in for evidence.

    The text is the fallback, and only the board's own words -- never the
    description, because "remote" in a body paragraph is as likely to describe
    the team as the role. Nothing is inferred beyond that: a location that says
    none of these returns None, and the interface prints "not stated".

    This is emphatically NOT an eligibility answer. Remote does not mean
    worldwide; that question belongs to the geography gate and stays there.
    """
    if job.workplace_type:
        return job.workplace_type
    if not job.location_raw:
        return None
    folded = job.location_raw.casefold()
    if "hybrid" in folded or "hibrido" in folded or "híbrido" in folded:
        return "HYBRID"
    if "remote" in folded or "remoto" in folded:
        return "REMOTE"
    if "onsite" in folded or "on-site" in folded or "presencial" in folded:
        return "ONSITE"
    return None
