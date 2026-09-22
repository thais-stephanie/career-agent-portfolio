"""Reading a value out of an archived provider payload.

Pure logic with two consumers that must agree byte-for-byte: `providers/base.py`
*produces* `source_field`/`source_value` when collecting, and `domain/verify.py`
*proves* them at extraction time. If those two ever disagreed about how a value
serialises, evidence would fail verification for formatting reasons rather than
real ones -- and the failure would look exactly like a hallucination.

Moved here from `providers/base.py` at M2 rather than duplicated. The domain
layer may not import siblings, so the alternative was two implementations of a
correctness-critical function, which is how they drift.

Resolution happens in Python, never in SQL. That is hazard A17 from the M0
architecture: SQLite's JSON functions and Postgres's differ enough that a
verifier written in SQL would quietly change meaning on migration, and
string-matching JSON with LIKE is both unportable and wrong.
"""

import json
from typing import Any


def resolve_path(payload: Any, path: str) -> Any:
    """Walk a dotted path into a payload. Returns None when it does not resolve."""
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def serialise_value(value: Any) -> str | None:
    """The canonical string form of a payload value, or None if there is nothing.

    Scalars stringify as themselves. Objects and arrays serialise with sorted
    keys so that a structure such as `salaryRange` survives whole and compares
    identically on both sides of the contract.

    Missing, None, empty string, empty object and empty list all return None.
    Absence stays absence -- an empty value is not an observation, and recording
    one would manufacture a claim the employer never made.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    # Before the int branch: bool is an int subclass, and False would otherwise
    # serialise as "0" -- which is a value, when what the provider said was no.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, dict | list | tuple):
        if not value:
            return None
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return str(value).strip() or None
