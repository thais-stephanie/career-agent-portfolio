"""The ledger under real concurrent processes.

These are the tests the sequential suite could not be: every one of them starts
actual OS processes, because the failure they exist to prevent is invisible
inside a single interpreter. Before the lock, and proven against that code:

* two processes read the same count and both wrote it back, producing duplicate
  sequence numbers and a file no reader would load again;
* two processes racing for the last authorised request both got it, spending one
  more than was authorised.

Nothing here writes to the programme ledger. Every case runs in `tmp_path`,
except the last, which only reads.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from career_agent.llm.budget import (
    PROGRAM_AUTHORIZATION,
    Authorization,
    BudgetLedger,
    LedgerEvent,
    verify_ledger,
)
from career_agent.llm.client import Family, ModelConfig

ARM = ModelConfig(vendor="google", identifier="gemma-4-31b-it", reasoning="high")

#: Each worker spins on a start file rather than sleeping, so every process is
#: already inside the interpreter and holding its imports before any of them
#: races. A sleep would make the race a coin toss on machine speed; this makes
#: contention as close to simultaneous as a test can arrange.
WORKER = """
import json, sys, time
from pathlib import Path
from career_agent.llm.budget import (
    Authorization, BudgetLedger, LedgerEvent, ProgramBudgetExhausted,
)
from career_agent.llm.client import Family, ModelConfig

directory, ceiling, label, gate, mode = (
    Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), sys.argv[5]
)
# A sixth argument narrows the authorisation to one phase with its own cap, so
# the phase ceiling can be raced by real processes exactly as the global one is.
phase_cap = int(sys.argv[6]) if len(sys.argv) > 6 else None
auth = Authorization(
    "concurrency", ceiling, "google", "gemma-4-31b-it",
    active_phase="L" if phase_cap is not None else None,
    phase_max_requests=phase_cap,
)
arm = ModelConfig(vendor="google", identifier="gemma-4-31b-it", reasoning="high")

ledger = BudgetLedger.for_authorization(auth, directory=directory)
while not gate.exists():
    time.sleep(0.001)

try:
    reservation = ledger.reserve(
        config=arm, family=Family.DESCRIPTION, cache_key="sha256:" + label,
        execution_id="exec-" + label, phase="L", job_id="job-" + label,
    )
except ProgramBudgetExhausted:
    print(json.dumps({"label": label, "outcome": "REFUSED", "reached_transport": False}))
    sys.exit(0)

if mode == "settle":
    ledger.settle(reservation, LedgerEvent.SENT)
print(json.dumps({
    "label": label, "outcome": "RESERVED", "seq": reservation.seq,
    "reached_transport": True,
}))
"""


def run_workers(
    tmp_path: Path,
    count: int,
    ceiling: int,
    mode: str = "settle",
    phase_cap: int | None = None,
) -> list[dict]:
    """Start `count` processes, release them at once, collect their verdicts."""
    script = tmp_path / "worker.py"
    script.write_text(WORKER, encoding="utf-8")
    directory = tmp_path / "budget"
    gate = tmp_path / "GO"

    running = [
        subprocess.Popen(
            [sys.executable, str(script), str(directory), str(ceiling), f"w{i}", str(gate), mode]
            + ([str(phase_cap)] if phase_cap is not None else []),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        for i in range(count)
    ]
    # Every worker is now blocked on the gate. Release them together.
    gate.write_text("go", encoding="utf-8")

    verdicts = []
    for process in running:
        out, err = process.communicate(timeout=120)
        assert process.returncode == 0, f"worker failed: {err[-800:]}"
        verdicts.append(json.loads(out.strip().splitlines()[-1]))
    return verdicts


def events_of(tmp_path: Path, ceiling: int) -> list[dict]:
    path = tmp_path / "budget" / "concurrency.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# =========================================================================
# 1. EXACTLY ONE WINNER FOR THE LAST REQUEST
# =========================================================================


def test_two_processes_and_one_authorised_request_produce_exactly_one_reservation(
    tmp_path: Path,
) -> None:
    """The race that used to overspend. Now it has a loser, and the loser stops."""
    verdicts = run_workers(tmp_path, count=2, ceiling=1)

    reserved = [v for v in verdicts if v["outcome"] == "RESERVED"]
    refused = [v for v in verdicts if v["outcome"] == "REFUSED"]

    assert len(reserved) == 1, f"expected one winner, got {verdicts}"
    assert len(refused) == 1, f"expected one loser, got {verdicts}"
    assert refused[0]["reached_transport"] is False, "the loser must stop before the transport"

    events = events_of(tmp_path, 1)
    assert sum(1 for e in events if e["event"] == "RESERVED") == 1, "one line, one request"
    assert (
        BudgetLedger(
            tmp_path / "budget" / "concurrency.jsonl",
            Authorization("concurrency", 1, "google", "gemma-4-31b-it"),
        ).consumed
        == 1
    )


def test_ten_processes_and_three_authorised_requests_spend_exactly_three(tmp_path: Path) -> None:
    """No overspend, and the ceiling is the ceiling however many ask at once."""
    verdicts = run_workers(tmp_path, count=10, ceiling=3)

    reserved = [v for v in verdicts if v["outcome"] == "RESERVED"]
    assert len(reserved) == 3, f"{len(reserved)} reservations against a ceiling of 3"
    assert all(v["reached_transport"] is False for v in verdicts if v["outcome"] == "REFUSED")

    events = events_of(tmp_path, 3)
    assert sum(1 for e in events if e["event"] == "RESERVED") == 3


# =========================================================================
# 2. SEQUENCE ASSIGNMENT IS ATOMIC
# =========================================================================


def test_twenty_concurrent_appends_produce_one_contiguous_valid_ledger(tmp_path: Path) -> None:
    """Forty lines from twenty processes, and every sequence exactly once."""
    verdicts = run_workers(tmp_path, count=20, ceiling=100)
    assert all(v["outcome"] == "RESERVED" for v in verdicts)

    events = events_of(tmp_path, 100)
    sequences = [e["seq"] for e in events]

    assert len(events) == 40, "twenty reservations and twenty outcomes"
    assert sequences == list(range(1, 41)), "the sequence is contiguous and in file order"
    assert len(set(sequences)) == len(sequences), "no duplicate sequence"

    # And the file a later process reads is loadable and counts correctly.
    auth = Authorization("concurrency", 100, "google", "gemma-4-31b-it")
    ledger = BudgetLedger(tmp_path / "budget" / "concurrency.jsonl", auth)
    assert ledger.consumed == 20
    assert verify_ledger(ledger).ok


def test_concurrent_outcome_events_receive_unique_sequences(tmp_path: Path) -> None:
    """Outcomes are locked too, or two settles could collide the way two
    reservations used to."""
    verdicts = run_workers(tmp_path, count=8, ceiling=100, mode="settle")
    assert all(v["outcome"] == "RESERVED" for v in verdicts)

    events = events_of(tmp_path, 100)
    outcomes = [e for e in events if e["event"] != "RESERVED"]
    assert len(outcomes) == 8
    assert len({e["seq"] for e in outcomes}) == 8, "every outcome has its own sequence"
    assert sorted(e["reserves"] for e in outcomes) == sorted(
        e["seq"] for e in events if e["event"] == "RESERVED"
    ), "each outcome settles exactly one distinct reservation"


def test_an_unsettled_reservation_is_not_refunded(tmp_path: Path) -> None:
    """Workers that reserve and are killed before settling. Still spent.

    `mode="abandon"` writes no outcome at all, which is what a crash between the
    reservation and the response looks like from the file.
    """
    verdicts = run_workers(tmp_path, count=5, ceiling=100, mode="abandon")
    assert all(v["outcome"] == "RESERVED" for v in verdicts)

    events = events_of(tmp_path, 100)
    assert [e["event"] for e in events] == ["RESERVED"] * 5, "no outcome lines at all"

    auth = Authorization("concurrency", 100, "google", "gemma-4-31b-it")
    ledger = BudgetLedger(tmp_path / "budget" / "concurrency.jsonl", auth)
    assert ledger.consumed == 5, "five uncertain requests, five spent"
    assert ledger.remaining == 95


# =========================================================================
# 3. NO MANUAL CLEANUP, AND THE LOCK IS THE OPERATING SYSTEM'S
# =========================================================================


def test_contention_leaves_nothing_to_clean_up(tmp_path: Path) -> None:
    """A lock file survives, and a later ledger takes it without complaint.

    The point of an OS-held lock rather than a create-and-delete one: a worker
    killed mid-run releases it by exiting, so no run ever has to decide whether
    a leftover file means anything.
    """
    run_workers(tmp_path, count=4, ceiling=100)
    directory = tmp_path / "budget"
    auth = Authorization("concurrency", 100, "google", "gemma-4-31b-it")

    ledger = BudgetLedger.for_authorization(auth, directory=directory)
    assert ledger.lock_path.exists(), "the sidecar stays; it is not cleanup"
    assert ledger.lock_path.stat().st_size == 0, "and it holds no data"

    reservation = ledger.reserve(
        config=ARM,
        family=Family.DESCRIPTION,
        cache_key="sha256:after",
        execution_id="after",
        phase="L",
        job_id="job",
    )
    ledger.settle(reservation, LedgerEvent.SENT)
    assert ledger.consumed == 5


def test_the_lock_sidecar_is_never_the_ledger(tmp_path: Path) -> None:
    auth = Authorization("concurrency", 10, "google", "gemma-4-31b-it")
    ledger = BudgetLedger.for_authorization(auth, directory=tmp_path / "budget")
    assert ledger.lock_path != ledger.path
    assert ledger.lock_path.name.endswith(".lock")

    ledger.settle(
        ledger.reserve(
            config=ARM,
            family=Family.DESCRIPTION,
            cache_key="sha256:k",
            execution_id="e",
            phase="L",
            job_id="j",
        ),
        LedgerEvent.SENT,
    )
    assert ledger.lock_path.stat().st_size == 0, "no event ever reaches the sidecar"
    assert len(ledger.events()) == 2


# =========================================================================
# 4. THE PROGRAMME LEDGER IS UNTOUCHED
# =========================================================================


def test_the_programme_ledger_is_internally_consistent() -> None:
    """Read-only, and deliberately not pinned to a count.

    An earlier version of this asserted `consumed == 1`, which was true on the
    day the lock was written and false the moment the next authorised request
    was made. A test that has to be edited after every legitimate run teaches
    people to edit it, and then it catches nothing.

    So it asserts what stays true however many requests have been spent: the
    sequence is contiguous, every outcome settles a reservation that exists, the
    count never exceeds the authorisation, and -- the one that could actually
    catch a bypass -- there are never MORE `llm_call` rows for this ledger's
    executions than there are reservations.

    Skipped rather than failed where the file does not exist, so the suite still
    runs on a checkout that has never made a request.
    """
    ledger = BudgetLedger.for_authorization(PROGRAM_AUTHORIZATION)
    if not ledger.path.exists():
        pytest.skip("no programme ledger in this working tree")

    events = ledger.events()
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert ledger.consumed <= PROGRAM_AUTHORIZATION.max_requests
    assert ledger.consumed + ledger.remaining == PROGRAM_AUTHORIZATION.max_requests
    assert PROGRAM_AUTHORIZATION.max_requests == 400

    corpus = Path(__file__).resolve().parents[2] / "data" / "m1d2" / "career.db"
    connection = None
    if corpus.exists():
        # `mode=ro`, AND THE DOCSTRING ABOVE ALREADY SAID READ-ONLY. A plain
        # `sqlite3.connect` opens for WRITING even when nothing writes, and on a
        # WAL database that is not inert: closing the connection CHECKPOINTS it,
        # which folds the write-ahead log back into the main file and changes its
        # size and its mtime. On 2026-09-10 a rescore had been interrupted and
        # left a 21 MB log behind, so this test silently rewrote 4.4 GB of the
        # owner's corpus and `tests/conftest.py` reported it as the session's one
        # violation -- correctly, and pointing at the only test that had ever
        # opened the real thing.
        #
        # Nothing was lost: a checkpoint is what the next writer would have done
        # anyway. But a read-only test must not be able to do it at all, and the
        # URI form is how SQLite is told so.
        connection = sqlite3.connect(f"file:{corpus.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
    try:
        audit = verify_ledger(ledger, connection)
    finally:
        if connection is not None:
            connection.close()

    assert audit.ok, audit.problems
    assert audit.reserved - audit.not_sent == ledger.consumed
    if audit.recorded_calls is not None:
        assert audit.recorded_calls <= audit.reserved, (
            "more recorded calls than reservations means a request was made without one"
        )
