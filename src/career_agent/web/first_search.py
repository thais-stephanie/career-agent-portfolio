"""The existing neutral setup wizard's missing browser entry point."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from career_agent.config.setup import Answers, SetupError, run_setup
from career_agent.web.server import ApiError

if TYPE_CHECKING:
    from career_agent.web.api import JobsApi


def register_first_search(app: JobsApi) -> None:
    lock = threading.Lock()

    def first_search(*, query: dict, body: dict) -> dict:
        if query or set(body) != {"role_examples", "skills"}:
            raise ApiError(400, "Provide work phrases and optional skills.", for_reader=True)
        for values in body.values():
            if (
                not isinstance(values, list)
                or len(values) > 20
                or any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in values)
            ):
                raise ApiError(
                    400,
                    "Use up to 20 short phrases per box, at most 100 characters each.",
                    for_reader=True,
                )
        if not body["role_examples"]:
            raise ApiError(400, "Describe at least one kind of work you want.", for_reader=True)
        with lock:
            if app.search_config().lexicon:
                raise ApiError(
                    409, "Your search already has phrases. Edit them in Settings.", for_reader=True
                )
            try:
                run_setup(
                    app.config.config_dir,
                    Answers(
                        role_examples=tuple(body["role_examples"]), skills=tuple(body["skills"])
                    ),
                )
            except SetupError as exc:
                raise ApiError(400, str(exc), for_reader=True) from exc
            app._search_config = None
        return {"saved": True, "recalculation_required": True}

    app.register("POST", r"/api/first-search", first_search)
