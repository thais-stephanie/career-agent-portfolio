"""`settle` waits for the run to end and says so when it does not.

It replaced `join(10)`, which returns when its time is up whether or not the
run ended, so a run still working was read as a finished one.
"""

import threading

import pytest
from tests.support import settle

from career_agent.pipeline.retrieval import RetrievalRunner


def test_a_run_that_ends_is_returned_finished() -> None:
    runner = RetrievalRunner()
    runner.start(lambda state, cancel: None, "r1")
    snapshot = settle(runner, within=5)
    assert snapshot is not None and snapshot["status"] == "done"


def test_a_run_that_does_not_end_fails_naming_where_it_is() -> None:
    runner = RetrievalRunner()
    stop = threading.Event()
    runner.start(lambda state, cancel: stop.wait(5), "r2")
    try:
        with pytest.raises(AssertionError, match="did not end within 0.2s"):
            settle(runner, within=0.2)
    finally:
        stop.set()
        runner.join(5)
