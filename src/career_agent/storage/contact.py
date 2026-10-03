"""The person's own identity and contact details, as a resume shows them.

BACKGROUND, NOT A SEARCH ANSWER. Nothing that scores, gates or filters a
posting reads this record (asserted in tests/unit/test_contact.py), and it
lives in the profile's own database, in `candidate_state`, so it is private to
one local profile and travels with that profile's backup.

The place here is where the person wants a RESUME to say they live. Where they
can WORK is a separate, existing answer (`eligibility.candidate_country` in
the search settings) and this record never changes it.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from career_agent.storage.workspace_repo import CandidateStateRepo, ensure_candidate

#: One key in `candidate_state`; versioned so a later shape can be told apart.
STATE_KEY = "contact.v1"

FIELDS = (
    "full_name",
    "email",
    "phone",
    "city",
    "region",
    "country",
    "linkedin_url",
    "portfolio_url",
    "github_url",
)
#: What a resume cannot go out without.
REQUIRED = ("full_name", "email")
URL_FIELDS = ("linkedin_url", "portfolio_url", "github_url")
MAX_LENGTH = 200

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL = re.compile(r"^(https?://)?[^\s/]+\.[^\s/]+(/\S*)?$", re.IGNORECASE)


class ContactError(ValueError):
    """A detail that cannot be saved, with a sentence a person can read."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def empty() -> dict[str, str]:
    return dict.fromkeys(FIELDS, "")


def read_contact(conn: sqlite3.Connection) -> dict[str, str]:
    """The saved details, every field present ("" when not given)."""
    row = conn.execute("SELECT id FROM candidate LIMIT 1").fetchone()
    if row is None:
        return empty()
    raw = CandidateStateRepo(conn).get(str(row["id"]), STATE_KEY)
    stored = json.loads(raw) if raw else {}
    return {field: str(stored.get(field) or "") for field in FIELDS}


def clean(values: dict[str, Any]) -> dict[str, str]:
    """Trim, check gently, and refuse anything that is not one of the fields.

    Phone is free text. A web address without a scheme gets `https://`.
    Nothing optional is ever required.
    """
    unknown = set(values) - set(FIELDS)
    if unknown:
        raise ContactError(sorted(unknown)[0], "That is not one of your details.")
    out = empty()
    for field in FIELDS:
        value = values.get(field, "")
        if not isinstance(value, str):
            raise ContactError(field, "Write this as text.")
        value = " ".join(value.split())
        if len(value) > MAX_LENGTH:
            raise ContactError(field, "This is too long.")
        if value and field == "email" and not _EMAIL.match(value):
            raise ContactError(field, "This does not look like an email address.")
        if value and field in URL_FIELDS:
            if not _URL.match(value):
                raise ContactError(field, "This does not look like a web address.")
            if not value.lower().startswith(("http://", "https://")):
                value = f"https://{value}"
        out[field] = value
    return out


def write_contact(conn: sqlite3.Connection, values: dict[str, Any]) -> dict[str, str]:
    """Save `values` as the details (every field; a blank one is cleared)."""
    cleaned = clean(values)
    candidate_id = ensure_candidate(conn)
    CandidateStateRepo(conn).set(candidate_id, STATE_KEY, json.dumps(cleaned, ensure_ascii=False))
    return cleaned


def missing_required(contact: dict[str, str]) -> list[str]:
    return [field for field in REQUIRED if not contact.get(field)]


def display_location(contact: dict[str, str]) -> str:
    """City, state and country, from whatever parts were given."""
    parts = (contact.get(key, "") for key in ("city", "region", "country"))
    return ", ".join(part for part in parts if part)
