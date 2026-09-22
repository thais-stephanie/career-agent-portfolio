"""Which database this process is allowed to open, and what to call it.

One module, because "personal or demo" is one decision and it must be made in
exactly one place. Threading a boolean through call sites is how a fallback
gets added later "just for this path" and the guarantee quietly stops holding.
"""

from career_agent.runtime.mode import (
    DEMO_DB_PATH,
    DatabaseIdentity,
    RuntimeIndicator,
    RuntimeMode,
    RuntimeModeError,
    database_ref,
    identity_of,
    read_identity,
    record_retrieval,
    resolve_database,
    stamp_identity,
)

__all__ = [
    "DEMO_DB_PATH",
    "DatabaseIdentity",
    "database_ref",
    "RuntimeIndicator",
    "RuntimeMode",
    "RuntimeModeError",
    "identity_of",
    "read_identity",
    "record_retrieval",
    "resolve_database",
    "stamp_identity",
]
