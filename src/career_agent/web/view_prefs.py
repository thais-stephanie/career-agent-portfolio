"""How old a posting may be and still appear in the search. One choice per profile.

Postings older than the limit are HIDDEN from the discovery views, never
deleted, never unscored: the match % does not depend on this choice and no
reading is invalidated by it. The age is the date the SOURCE published the
posting (`job.posted_at`). The date this machine last collected it is not a
publication date and is never used as one: collecting a posting again must not
make it young.

A posting that states no publication date is hidden too, unless the person
turned on "include postings without a date". Saved, applied and otherwise
tracked postings are exempt (they keep their own views), and so are the
resumes made for a job: the age is shown there, access is not blocked.
"""

from __future__ import annotations

from typing import Any

from career_agent.web.server import ApiError

KEY_AGE = "view.max_age_days"
KEY_UNDATED = "view.include_undated"
DEFAULT_AGE = 120
#: The limits offered; None is "no limit".
CHOICES: tuple[int | None, ...] = (120, 150, None)
_NONE = "none"


def read(conn: Any) -> dict[str, Any]:
    """This profile's choice. A profile that never chose has the default.

    The demo corpus is a fixed set of invented postings with fixed dates: an age
    limit would empty it as the months pass, so it does not apply there
    (`applies` is False and the limit reads as none)."""
    from career_agent.runtime.mode import RuntimeMode, read_identity
    from career_agent.storage.workspace_repo import CandidateStateRepo, candidate_id_of

    identity = read_identity(conn)
    if identity is not None and identity.kind is RuntimeMode.DEMO:
        return {
            "max_age_days": None,
            "include_undated": False,
            "choices": list(CHOICES),
            "applies": False,
        }

    repo = CandidateStateRepo(conn)
    # Reading never creates the candidate: a list request writes nothing.
    candidate = candidate_id_of(conn)
    raw = repo.get(candidate, KEY_AGE) if candidate else None
    if raw is None:
        age: int | None = DEFAULT_AGE
    elif raw == _NONE:
        age = None
    else:
        try:
            age = int(raw)
        except ValueError:
            age = DEFAULT_AGE
        if age not in CHOICES:
            age = DEFAULT_AGE
    return {
        "max_age_days": age,
        "include_undated": bool(candidate and repo.get(candidate, KEY_UNDATED) == "1"),
        "choices": list(CHOICES),
        "applies": True,
    }


def write(conn: Any, body: dict[str, Any]) -> dict[str, Any]:
    from career_agent.storage.db import transaction
    from career_agent.storage.workspace_repo import CandidateStateRepo, ensure_candidate

    if not body or set(body) - {"max_age_days", "include_undated"}:
        raise ApiError(400, "Say the age limit and whether to include postings without a date.")
    if "max_age_days" in body:
        age = body["max_age_days"]
        if age is not None and (isinstance(age, bool) or age not in CHOICES):
            raise ApiError(400, "The age limit is 120 days, 150 days or no limit.", for_reader=True)
    if "include_undated" in body and not isinstance(body["include_undated"], bool):
        raise ApiError(400, "include_undated must be true or false.")
    with transaction(conn):
        repo = CandidateStateRepo(conn)
        candidate = ensure_candidate(conn)
        if "max_age_days" in body:
            age = body["max_age_days"]
            repo.set(candidate, KEY_AGE, _NONE if age is None else str(age))
        if "include_undated" in body:
            repo.set(candidate, KEY_UNDATED, "1" if body["include_undated"] else "0")
    return read(conn)
