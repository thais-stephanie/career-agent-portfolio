# Added for the Career Agent public edition (2026-09-26). See NOTICE.
"""Errors a person reads, as codes the page can word in their language.

A user-facing error is `{"code", "message", "params"}` under `detail`: the
page looks the code up in its EN/PT catalogue and fills in the params; the
English `message` is the fallback for any other caller. Nothing else about a
failure reaches the page: an unexpected exception becomes the `internal`
code, and its detail goes to the local log only (no stack trace, no path).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

log = logging.getLogger("resume_tailor")


def user_error(status: int, code: str, message: str, **params: Any) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message, "params": params})


def body(code: str, message: str, **params: Any) -> dict[str, Any]:
    """The JSON body of a user-facing error, for responses built directly."""
    return {"detail": {"code": code, "message": message, "params": params}}


INTERNAL_MESSAGE = "Something went wrong in Resume Tailor. Nothing was changed; try again."
INVALID_MESSAGE = "That request was not complete. Reload Resume Tailor and try again."
