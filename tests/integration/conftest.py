"""Shared integration fixtures, resolved by pytest rather than by import.

`test_live_runner.py` defines the corpus fixtures every runner test needs. A
second module importing them by name works, and makes ruff read each test
signature as a redefinition -- thirty-one warnings about a pattern that is
correct. Re-exporting here lets pytest find them by name instead, which is what
a conftest is for.
"""

import socket  # noqa: E402

import pytest  # noqa: E402
from tests.integration.test_live_runner import (  # noqa: F401
    conn,
    db_path,
    job_ids,
)

_LOOPBACK = ("127.", "::1", "localhost")


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


@pytest.fixture(autouse=True)
def _no_outgoing_connection(monkeypatch):
    """A test never reaches a host beyond this machine; it fails and says which.

    Without it a source nobody stubbed makes the test depend on the vendor's
    answer and speed, which is how two plan tests came to hang for ten seconds
    only on some runs.
    """
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def check(address: object) -> None:
        host = str(address[0]) if isinstance(address, tuple) else str(address)
        if not host.startswith(_LOOPBACK):
            raise AssertionError(f"a test tried to reach {host}: stub the source it belongs to")

    def connect(self, address):  # type: ignore[no-untyped-def]
        check(address)
        return real_connect(self, address)

    def connect_ex(self, address):  # type: ignore[no-untyped-def]
        check(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
