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


def _payload(anchors: RoleAnchors, titles: list[str]) -> dict[str, Any]:
    held = {folded(a.text) for a in anchors.anchors}
    return {
        "anchors": [a.model_dump() for a in anchors.anchors],
        "aliases": [a.model_dump() for a in anchors.aliases],
        "suggestions": [
            {"text": t, "from": "career_profile"} for t in titles if folded(t) not in held
        ][:MAX_SUGGESTIONS],
        "limits": {"anchors": MAX_ANCHORS, "text": MAX_TEXT},
        # Said by the server so no screen can drift from it.
        "affects_scores": False,
        "required": False,
    }


def register_role_anchors(app: JobsApi) -> None:
    def read(*, query: dict, body: dict) -> dict:
        if query or body:
            raise ApiError(400, "Reading role anchors takes no parameters.")
        with closing(app.connect()) as conn:
            titles = profile_titles(conn)
        return _payload(load_anchors(app.config.config_dir), titles)

    def write(*, query: dict, body: dict) -> dict:
        if query or set(body) != {"anchors"} or not isinstance(body["anchors"], list):
            raise ApiError(400, "Send the list of roles as `anchors`.")
        with closing(app.connect()) as conn:
            titles = profile_titles(conn)
        offered = {folded(t) for t in titles}
        anchors: list[Anchor] = []
        for item in body["anchors"]:
            if not isinstance(item, dict) or set(item) - {"text", "source"}:
                raise ApiError(400, "Each role is an object with `text` and `source`.")
            text = clean(item.get("text") if isinstance(item.get("text"), str) else "")
            source = item.get("source", "user")
            if not text or len(text) > MAX_TEXT:
                raise ApiError(
                    400, f"Keep each role to {MAX_TEXT} characters or fewer.", for_reader=True
                )
            if source == "confirmed_suggestion" and folded(text) not in offered:
                # Provenance is a claim about where words came from. A role
                # the profile never held cannot have been a suggestion from it.
                source = "user"
            if source not in {"user", "confirmed_suggestion"}:
                raise ApiError(400, "A role is either typed or a confirmed suggestion.")
            anchors.append(Anchor(text=text, source=source))
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
