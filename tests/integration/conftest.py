"""Shared integration fixtures, resolved by pytest rather than by import.

`test_live_runner.py` defines the corpus fixtures every runner test needs. A
second module importing them by name works, and makes ruff read each test
signature as a redefinition -- thirty-one warnings about a pattern that is
correct. Re-exporting here lets pytest find them by name instead, which is what
a conftest is for.
"""

from tests.integration.test_live_runner import (  # noqa: F401
    conn,
    db_path,
    job_ids,
)
