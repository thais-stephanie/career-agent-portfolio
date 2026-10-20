"""Session-wide guard: no test may change the owner's operational data.

See `tests/protected_paths.py` for what counts as operational and why the
contract is expressed as patterns rather than as one machine's filenames.

It runs for EVERY suite -- unit, integration and browser -- because the damage
does not care which directory the test lived in. It costs one `stat` and one
small hash per protected file per test, which on this repository is four files
of a few tens of kilobytes.
"""

from __future__ import annotations

import os
import socket

import pytest
from tests.protected_paths import changed, describe, restore, snapshot

#: THE PRODUCT'S OWN SWITCHES ARE THE OWNER'S, NEVER THE SUITE'S.
#:
#: Removed AT IMPORT, before any fixture runs and before any server a fixture
#: starts inherits the environment. The owner's Windows environment sets the
#: two matching switches (Phase C and Phase D); they leaked into seven tests
#: that build their own expectations under the default, and those failed on
#: this machine only (2026-10-18). A test that wants a switch sets it with
#: `monkeypatch.setenv`. Test-harness switches (`CAREER_AGENT_REFRESH_EVIDENCE`)
#: are deliberately not in this list.
LOCAL_PRODUCT_SWITCHES = (
    "CAREER_AGENT_ELIGIBILITY_SOURCE",
    "CAREER_AGENT_WORK_MATCH_SOURCE",
    "CAREER_AGENT_DB",
    "CAREER_AGENT_ALLOW_UPGRADE_WITHOUT_BACKUP",
    "CAREER_AGENT_DEEP_CONCURRENCY",
)
for _name in LOCAL_PRODUCT_SWITCHES:
    os.environ.pop(_name, None)

#: Taken AT IMPORT, read at every teardown.
#:
#: Not in `pytest_sessionstart`: a conftest under `testpaths` is loaded during
#: preparse, and its `pytest_sessionstart` is not guaranteed to be registered
#: in time -- measured here, it was not, and the baseline stayed empty while
#: every assertion about it passed vacuously. Importing a conftest happens
#: before any test in its tree runs, which is exactly the moment wanted, and it
#: has no ordering subtleties at all.
_BASELINE: dict = snapshot()

#: Every test that changed something, in the order they did it. Reported at the
#: end as well as failing at the moment of detection: by then the session may
#: have scrolled a long way past the first one, and the FIRST one is the cause.
_VIOLATIONS: list[tuple[str, str]] = []


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    if not _BASELINE:
        return
    damaged = changed(_BASELINE)
    if not damaged:
        return
    what = describe(damaged)
    _VIOLATIONS.append((item.nodeid, what))
    unrestored = restore(_BASELINE, damaged)
    # Re-snapshot so the next test is judged against reality rather than
    # reporting the same violation once per remaining test in the session.
    _BASELINE.clear()
    _BASELINE.update(snapshot())
    detail = f" COULD NOT BE RESTORED: {describe(unrestored)}." if unrestored else " Restored."
    # **THE OTHER EXPLANATION, NAMED.** This watches FILES, not callers, so it
    # cannot tell a test that wrote one from something else on the machine
    # writing it while the test ran -- a `rescore` or a `collect` in another
    # terminal, both of which legitimately write the corpus for minutes at a
    # time. That happened on 2026-09-08 and the message said only "a test may
    # not do this", which sent the reader looking for a bug in the test.
    #
    # The bias toward alarm is still right: a suite that quietly corrupted a
    # 2 GB corpus is the worst outcome available. But an alarm has to say what
    # else it might be, or it trains people to dismiss it.
    concurrent = (
        "\nIf a `rescore` or `collect` was running against this database at the "
        "same time, that is the likelier explanation and nothing is wrong with "
        "the test -- do not run the suite alongside one."
        if unrestored
        else ""
    )
    pytest.fail(
        f"{item.nodeid} changed protected operational data: {what}.{detail}\n"
        "A test may not read or write the owner's real configuration or corpus. "
        "Use `tests.support.committed_config_dir()` for configuration and a "
        f"`tmp_path` database.{concurrent}",
        pytrace=False,
    )


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    if not _VIOLATIONS:
        return
    terminalreporter.section("protected operational data", red=True)
    for nodeid, what in _VIOLATIONS:
        terminalreporter.line(f"  {nodeid}  ->  {what}")


_LOOPBACK = ("127.", "::1", "localhost")


@pytest.fixture(autouse=True)
def _no_outgoing_connection(monkeypatch):
    """A test never reaches a host beyond this machine; it fails and says which.

    Without it a source nobody stubbed makes the test depend on the vendor's
    answer and speed, which is how two plan tests came to hang for ten seconds
    only on some runs, and how the browser tests of a first run came to
    download Workable's whole feed and wait on it.
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
