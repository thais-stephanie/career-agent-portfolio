"""Career Agent: local job discovery, eligibility and Search Fit."""

import hashlib
from pathlib import Path

__version__ = "0.2.0b3"


def install_id(root: Path | None = None) -> str:
    """This installation folder, named without revealing its path.

    The desktop launcher compares it with what a server on the port answers,
    so it never opens another installation's Career Agent, or anything else
    that happens to listen there, as its own.
    """
    folder = root if root is not None else Path(__file__).resolve().parents[2]
    return hashlib.sha256(str(folder.resolve()).casefold().encode()).hexdigest()[:16]
