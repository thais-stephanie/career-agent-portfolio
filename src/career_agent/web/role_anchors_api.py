"""`/api/role-anchors`: "Do you have specific roles in mind?"

Optional, and never a readiness requirement. The answer steers query-based
sources (the targeted lane) and nothing else: it moves no score, hides no
posting and needs no recalculation.

Suggestions come from the Career Profile's own job titles and are OFFERED,
never stored: background is what someone has done, not what they want next.
A suggestion becomes an anchor only when the person sends it back, and then it
is kept as `confirmed_suggestion`, so the two provenances stay apart.
"""

from __future__ import annotations

import re
import threading
from typing import TYPE_CHECKING, Any

from career_agent.discovery.aliases import plan_aliases
from career_agent.discovery.anchors import (
    MAX_ANCHORS,
    MAX_TEXT,
    Anchor,
    AnchorsError,
    RoleAnchors,
    clean,
    folded,
    load_anchors,
    save_anchors,
)
from career_agent.storage.workspace_repo import candidate_id_of
from career_agent.web.server import ApiError, closing

if TYPE_CHECKING:
    import sqlite3

    from career_agent.web.api import JobsApi

MAX_SUGGESTIONS = 5


def profile_titles(conn: sqlite3.Connection) -> list[str]:
    """Distinct job titles in the Career Profile, most recent first."""
    candidate = candidate_id_of(conn)
    if candidate is None:
        return []
    try:
        rows = conn.execute(
            "SELECT title FROM career_experience"
            " WHERE candidate_id = ? AND archived = 0 AND title IS NOT NULL AND title != ''"
            " ORDER BY current_role DESC, COALESCE(period_end, '9999') DESC,"
            " COALESCE(period_start, '') DESC, display_order",
            (candidate,),
        ).fetchall()
    except Exception:  # noqa: BLE001 - a workspace without a profile has no suggestions
        return []
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        text = clean(row[0])[:MAX_TEXT]
        if text and folded(text) not in seen:
            seen.add(folded(text))
            out.append(text)
    return out


#: "Role | what it involved": the role comes first, and what follows a bar is
#: context. A spaced dash (hyphen, en or em dash) splits only after a role of
#: two words or more: "Nurse Manager - Night shift" is a role and its context,
#: while "Manager - Customer Success" is one title whose role follows the dash.
_BAR = re.compile(r"\s*\|\s*")
_DASH = re.compile(r"\s+[-\u2013\u2014]\s+")


def split_title(title: str) -> tuple[str, str]:
    """(the role title, any context after it) for a Career Profile title.

    Presentation only: the stored experience is never changed. A title with
    no separator is its own role, with no context.
    """
    text = clean(title)
    parts = _BAR.split(text, maxsplit=1)
    if len(parts) == 1:
        dashed = _DASH.split(text, maxsplit=1)
        if len(dashed) == 2 and len(dashed[0].split()) >= 2:
            parts = dashed
    role = clean(parts[0])
    context = clean(parts[1]) if len(parts) > 1 else ""
    return (role, context) if role else (text, "")


def offered_roles(titles: list[str]) -> set[str]:
    """Folded texts a confirmed suggestion may carry: a title or its role."""
    return {folded(t) for t in titles} | {folded(split_title(t)[0]) for t in titles}


def _payload(anchors: RoleAnchors, titles: list[str]) -> dict[str, Any]:
    from career_agent.discovery.aliases import effective

    anchors = effective(anchors)
    held = {folded(a.text) for a in anchors.anchors}
    suggestions: list[dict[str, str]] = []
    seen: set[str] = set(held)
    for title in titles:
        role, context = split_title(title)
        # Longer than a role may be: not offered, rather than offered cut.
        if len(role) > MAX_TEXT or folded(role) in seen:
            continue
        seen.add(folded(role))
        suggestions.append({"text": role, "context": context, "from": "career_profile"})
    return {
        "anchors": [a.model_dump() for a in anchors.anchors],
        "aliases": [a.model_dump() for a in anchors.aliases],
        "suggestions": suggestions[:MAX_SUGGESTIONS],
        "limits": {"anchors": MAX_ANCHORS, "text": MAX_TEXT},
        # Said by the server so no screen can drift from it.
        "affects_scores": False,
        "required": False,
    }


def _is_personal(app: JobsApi) -> bool:
    from career_agent.runtime import RuntimeMode, read_identity

    with closing(app.connect()) as conn:
        identity = read_identity(conn)
    return identity is not None and identity.kind is RuntimeMode.PERSONAL


def register_role_anchors(app: JobsApi) -> None:
    #: One writer at a time: the server is threaded, and two saves racing
    #: could leave the older list on disk.
    lock = threading.Lock()

    def read(*, query: dict, body: dict) -> dict:
        if query or body:
            raise ApiError(400, "Reading role anchors takes no parameters.")
        with closing(app.connect()) as conn:
            titles = profile_titles(conn)
        return _payload(load_anchors(app.config.config_dir), titles)

    def write(*, query: dict, body: dict) -> dict:
        if query or set(body) != {"anchors"} or not isinstance(body["anchors"], list):
            raise ApiError(400, "Send the list of roles as `anchors`.")
        if len(body["anchors"]) > MAX_ANCHORS * 4:
            raise ApiError(400, f"Name up to {MAX_ANCHORS} roles.", for_reader=True)
        if not _is_personal(app):
            # The demo shares the configuration folder; what somebody tries
            # while exploring it must not steer their own searches later.
            raise ApiError(
                409, "The demo does not save roles. Start Career Agent normally.", for_reader=True
            )
        with closing(app.connect()) as conn:
            titles = profile_titles(conn)
        offered = offered_roles(titles)
        with lock:
            held = {
                folded(a.text)
                for a in load_anchors(app.config.config_dir).anchors
                if a.source == "confirmed_suggestion"
            }
            anchors: list[Anchor] = []
            for item in body["anchors"]:
                if not isinstance(item, dict) or set(item) - {"text", "source"}:
                    raise ApiError(400, "Each role is an object with `text` and `source`.")
                text, source = item.get("text"), item.get("source", "user")
                if not isinstance(text, str) or not clean(text):
                    raise ApiError(400, "Each role needs some words.", for_reader=True)
                if not isinstance(source, str) or source not in {"user", "confirmed_suggestion"}:
                    raise ApiError(400, "A role is either typed or a confirmed suggestion.")
                text = clean(text)
                if len(text) > MAX_TEXT:
                    raise ApiError(
                        400, f"Keep each role to {MAX_TEXT} characters or fewer.", for_reader=True
                    )
                if source == "confirmed_suggestion" and folded(text) not in offered | held:
                    # Provenance is a claim about where words came from. A role
                    # the profile never held, and that was never confirmed from
                    # it, cannot have been a suggestion from it.
                    source = "user"
                anchors.append(Anchor(text=text, source=source))
            return _save(anchors, titles)

    def _save(anchors: list[Anchor], titles: list[str]) -> dict:
        if len({folded(a.text) for a in anchors}) > MAX_ANCHORS:
            raise ApiError(
                400,
                f"Name up to {MAX_ANCHORS} roles. Fewer, clearer ones work better.",
                for_reader=True,
            )
        try:
            unique = RoleAnchors(anchors=tuple(anchors))
            saved = save_anchors(
                app.config.config_dir,
                RoleAnchors(anchors=unique.anchors, aliases=plan_aliases(unique.anchors)),
            )
        except (AnchorsError, ValueError) as exc:
            raise ApiError(400, "Those roles could not be saved.", for_reader=True) from exc
        return {**_payload(saved, titles), "saved": True, "recalculation_required": False}

    app.register("GET", r"/api/role-anchors", read)
    app.register("PATCH", r"/api/role-anchors", write)
