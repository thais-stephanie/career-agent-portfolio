"""Candidate career organization routes. Preview, then one atomic application."""

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

    app.register("GET", r"/api/career", overview)
    app.register("GET", r"/api/career/evidence", evidence)
    app.register("GET", r"/api/career/history", history)
    app.register("POST", r"/api/career/preview", preview)
    app.register("POST", r"/api/career/changes", apply)
