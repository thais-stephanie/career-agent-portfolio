"""The Candidate Intake Package: proposed evidence that arrived as a file.

Four modules, and the split is the point.

`models`   the shape a package may take, and every refusal that follows from
           it. Nothing here touches a database.
`parse`    bytes into models, with claim identity derived here rather than
           trusted from the file.
`conflicts` which claims disagree, and which are simply the same claim seen
           twice. It groups; it never resolves.
`store`    the staging tables, the review states, and the one place a
           confirmed intake claim becomes a `VerifiedClaim`.

**Nothing in this package sets `verified=True`.** A confirmed row is handed to
`cv.propose.to_claim`, which is one of the three places in the whole product
allowed to do that, and `tests/unit/test_intake_never_verifies.py` walks the
syntax tree of every module here to keep it so.
"""

from career_agent.intake.models import (
    IntakePackage,
    IntakeParseError,
    ProposedClaim,
    ReviewState,
    SourceKind,
)
from career_agent.intake.parse import parse_package

__all__ = [
    "IntakePackage",
    "IntakeParseError",
    "ProposedClaim",
    "ReviewState",
    "SourceKind",
    "parse_package",
]
