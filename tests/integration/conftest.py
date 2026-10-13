"""Shared integration fixtures, resolved by pytest rather than by import.

`test_live_runner.py` defines the corpus fixtures every runner test needs. A
second module importing them by name works, and makes ruff read each test
signature as a redefinition -- thirty-one warnings about a pattern that is
correct. Re-exporting here lets pytest find them by name instead, which is what
a conftest is for.
"""

import pytest  # noqa: E402
from tests.integration.test_live_runner import (  # noqa: F401
    conn,
    db_path,
    job_ids,
)


@pytest.fixture
def no_live_workable(monkeypatch):
    """A refresh plan that includes Workable must not read Workable's feed.

    `source_refresh._step` builds Workable's work through
    `auto_update.workable_work`, a different door from `feed_work`,
    `_collect_work` and `employer_board_work`, which the plan tests replace. A
    test that stubbed those three and forgot this one opened a real connection
    to the vendor and waited for its answer. A test that wants its own Workable
    step patches this name again, as `test_find_jobs` does.

    Opt in with `pytestmark = pytest.mark.usefixtures("no_live_workable")`;
    the tests of Workable's own work drive it through a fake feed and must not
    have it replaced.
    """
    monkeypatch.setattr(
        "career_agent.web.auto_update.workable_work",
        lambda app: lambda state, cancel: None,
    )
