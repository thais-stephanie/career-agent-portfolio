"""Candidate career organization routes. Preview, then one atomic application."""

from typing import Any

from pydantic import ValidationError

from career_agent.storage.career_repo import CareerError, CareerRepo
from career_agent.storage.db import transaction
from career_agent.storage.workspace_repo import candidate_id_of, ensure_candidate
from career_agent.web.server import ApiError, LocalApp, closing


def register_career_routes(app: LocalApp) -> None:
    def overview(*, query: dict, body: dict) -> dict:
        with closing(app.connect()) as conn:
            return CareerRepo(conn, candidate_id_of(conn)).overview()

    def evidence(*, query: dict, body: dict) -> dict:
        def one(key: str, default: str = "") -> str:
            values = query.get(key, [default])
            return str(values[0])

        if set(query) - {"experience", "q", "state", "category", "offset", "limit"}:
            raise ApiError(400, "Unknown career evidence filter.")
        try:
            offset, limit = int(one("offset", "0")), int(one("limit", "50"))
            if offset < 0 or not 1 <= limit <= 100:
                raise ValueError
        except ValueError as exc:
            raise ApiError(400, "Choose a valid evidence page.") from exc
        with closing(app.connect()) as conn:
            return CareerRepo(conn, candidate_id_of(conn)).page(
                experience=one("experience"),
                query=one("q"),
                state=one("state"),
                category=one("category"),
                offset=offset,
                limit=limit,
            )

    def preview(*, query: dict, body: dict) -> dict:
        try:
            with closing(app.connect()) as conn:
                return CareerRepo(conn, candidate_id_of(conn)).preview(body)
        except (ValueError, ValidationError) as exc:
            raise ApiError(400, str(exc), for_reader=True) from exc

    def history(*, query: dict, body: dict) -> dict:
        if set(query) - {"offset", "limit"}:
            raise ApiError(400, "Unknown history filter.")
        try:
            offset = int(query.get("offset", ["0"])[0])
            limit = int(query.get("limit", ["20"])[0])
            if offset < 0 or not 1 <= limit <= 100:
                raise ValueError
        except ValueError as exc:
            raise ApiError(400, "Choose a valid history page.") from exc
        with closing(app.connect()) as conn:
            return CareerRepo(conn, candidate_id_of(conn)).history(offset, limit)

    def apply(*, query: dict, body: dict) -> dict:
        if set(body) - {"command", "preview_hash", "reviewed"} or not isinstance(
            body.get("command"), dict
        ):
            raise ApiError(400, "Apply a reviewed career preview.")
        try:
            with closing(app.connect()) as conn, transaction(conn):
                repo = CareerRepo(conn, candidate_id_of(conn))
                # Validate before creating the sole candidate on a fresh install.
                if repo.preview(body["command"])["preview_hash"] != body.get("preview_hash"):
                    raise CareerError("The evidence changed. Review a fresh preview.")
                candidate_id = ensure_candidate(conn)
                return CareerRepo(conn, candidate_id).apply(
                    body["command"],
                    body["preview_hash"],
                    reviewed=body.get("reviewed") is True,
                )
        except (ValueError, ValidationError) as exc:
            raise ApiError(409, str(exc), for_reader=True) from exc

    def set_name(*, query: dict, body: dict) -> dict:
        """The person's name as a resume prints it, in the existing candidate
        row. Not a search answer: nothing that scores a posting reads it."""
        from career_agent.storage.workspace_repo import (
            PLACEHOLDER_NAME,
            ensure_candidate,
            person_name,
        )

        name = " ".join(str(body.get("name") or "").split())
        if not name or len(name) > 200 or name.casefold() == PLACEHOLDER_NAME.casefold():
            raise ApiError(400, "Write your name as it should appear on a resume.", for_reader=True)
        with closing(app.connect()) as conn, transaction(conn):
            candidate_id = ensure_candidate(conn)
            conn.execute("UPDATE candidate SET display_name = ? WHERE id = ?", (name, candidate_id))
            return {"name": person_name(conn)}

    def _labels() -> list[str]:
        host = getattr(app, "profile_host", None)
        label = getattr(getattr(host, "active", None), "label", None)
        return [str(label)] if label else []

    def _master_view(conn: Any, master: Any, notes: list[str] | None = None) -> dict:
        from career_agent.resume_doc.master import evidence_changes

        if master is None:
            return {"master": None}
        return {
            "master": {
                "id": master.id,
                "title": master.title,
                "updated_at": master.updated_at,
                "sha256": master.working_sha256,
                "identity": master.working.identity.model_dump(mode="json"),
                "name_finding": master.working.identity.name_finding(),
            },
            "evidence_changes": evidence_changes(conn, master.working),
            **({"notes": notes} if notes is not None else {}),
        }

    def get_master(*, query: dict, body: dict) -> dict:
        """The profile's Master resume and its identity. Reading never creates one."""
        from career_agent.resume_doc.store import ResumeStore

        with closing(app.connect()) as conn:
            return _master_view(conn, ResumeStore(conn).current_master())

    def make_master(*, query: dict, body: dict) -> dict:
        """The Master, made from confirmed Career Profile data the first time."""
        from career_agent.resume_doc.master import get_or_create_master

        with closing(app.connect()) as conn:
            master, notes = get_or_create_master(conn, labels=_labels())
            return _master_view(conn, master, notes)

    def set_identity(*, query: dict, body: dict) -> dict:
        """The resume identity, as a new revision of the Master. It changes the
        resume only: no evidence, search setting or score reads it."""
        from career_agent.resume_doc.master import update_resume_identity
        from career_agent.resume_doc.models import Identity
        from career_agent.resume_doc.store import NotFound, StaleDocument

        if set(body) != {"identity", "expected_sha256"}:
            raise ApiError(400, "Send the identity and the version it edits.")
        try:
            identity = Identity.model_validate(body["identity"])
        except ValidationError as exc:
            raise ApiError(400, "Check the contact details.", for_reader=True) from exc
        with closing(app.connect()) as conn:
            try:
                master = update_resume_identity(
                    conn, identity, expected_sha256=str(body["expected_sha256"])
                )
            except NotFound as exc:
                raise ApiError(404, "There is no Master resume yet.") from exc
            except StaleDocument as exc:
                raise ApiError(
                    409, "Your resume changed in another window. Reload it.", for_reader=True
                ) from exc
            return _master_view(conn, master)

    app.register("PATCH", r"/api/candidate/name", set_name)
    app.register("GET", r"/api/resume/master", get_master)
    app.register("POST", r"/api/resume/master", make_master)
    app.register("PATCH", r"/api/resume/master/identity", set_identity)
    app.register("GET", r"/api/career", overview)
    app.register("GET", r"/api/career/evidence", evidence)
    app.register("GET", r"/api/career/history", history)
    app.register("POST", r"/api/career/preview", preview)
    app.register("POST", r"/api/career/changes", apply)
