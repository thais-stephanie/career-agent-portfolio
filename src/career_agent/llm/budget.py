"""The durable request budget: what was authorised, and what has been spent.

WHY A FILE AND NOT AN INTEGER
-----------------------------
`PacedLiveClient` has always counted its own live calls, checked the count
before each request rather than after, and refunded a request that provably
never reached the wire. Every one of those decisions is right, and all of them
are in memory. The counter is born at zero in each process, so a budget spread
across eight phases and any number of resumptions was eight separate budgets
that happened to share a number.

A cumulative authorisation cannot live in a process. It has to survive a
crash, a `KeyboardInterrupt`, a new terminal and a week off, because those are
the ordinary events that separate one phase from the next.

WHY EVENTS AND NOT A COUNTER ON DISK
------------------------------------
A counter on disk has to be read, incremented and written back, and a process
that dies between the read and the write loses the increment -- which is the
failure a durable budget exists to prevent. So nothing is ever rewritten. The
ledger is a file of append-only events, and the count is *derived*:

    consumed = RESERVED - NOT_SENT

`RESERVED` is written, and fsynced, BEFORE the POST. That ordering is the whole
guarantee, and it is deliberately pessimistic: a process killed between the
reservation and the request has consumed a request it never made. Over-counting
costs one request out of four hundred. Under-counting is how a free tier gets
exhausted by a run that believed it had budget left, and how a paid one gets a
bill nobody authorised.

`NOT_SENT` is the only refund, and it mirrors `LLMNotSent` exactly: the request
was never constructed, no HTTP transport was reached, no quota moved. Everything
else consumes -- a timeout, a 429, a 500, an answer that cannot be parsed, an
answer whose attempt row failed to persist. The vendor was asked. That is the
event a quota counts.

WHY THIS FILE IS NOT IN THE DATABASE
------------------------------------
The budget is program-wide and the database is not. `data/` holds six corpora,
is gitignored as rebuildable machine state, and is selected per command with
`--db`. A budget that lived there would reset by pointing at a different file,
which is exactly the property it must not have.

So it lives beside `evaluation/free-challenger-screen.yaml`, under version
control, for the same reason that file does: it is the record of what was
authorised before anything ran, and its value comes from being unchangeable
after the fact.

WHAT THIS IS NOT
----------------
Not a spend audit. `llm/pricing.py` answers "what did this cost and to whom",
in three currencies, and it is the authority on money. This answers one
narrower question -- how many HTTP requests has this authorisation spent --
because a free route still has a daily allowance, and a request budget is what
an authorisation is actually written in.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from career_agent.llm.client import Family, ModelConfig
from career_agent.llm.failures import CallBudgetExhausted, RunHalted

#: Durable non-corpus artifacts live under `evaluation/` and are tracked, which
#: is the convention `golden-review.md` and `free-challenger-screen.yaml`
#: already follow. Relative, like both of those, so the path is the same
#: whichever database a command was pointed at.
LEDGER_DIR = Path("evaluation/budget")

#: The schema version of a ledger line. Present from the first line written,
#: because a format that has to be guessed at cannot be audited, and this file
#: is meant to be read years after the run.
LEDGER_FORMAT = 1


class LedgerEvent(StrEnum):
    """What happened to one authorised request.

    `RESERVED` is written before the POST; exactly one outcome is written after.
    Only `NOT_SENT` gives the budget back.
    """

    #: Written, and fsynced, before the request is handed to the transport.
    RESERVED = "RESERVED"
    #: The vendor answered. Whether the answer was any good is a different file.
    SENT = "SENT"
    #: The request never reached HTTP. The only refund there is.
    NOT_SENT = "NOT_SENT"
    #: The vendor was asked and did not answer in time. Consumes.
    TIMEOUT = "TIMEOUT"
    #: Asked, and the answer was an error -- 429, 5xx, refusal. Consumes.
    FAILED = "FAILED"
    #: Asked and answered, and the record of it could not be stored. Consumes,
    #: and says so: an unrecorded request is the one an audit cannot see.
    PERSIST_FAILED = "PERSIST_FAILED"


class AuthorizationViolation(RunHalted):
    """A request the standing authorisation does not cover.

    Raised BEFORE a reservation is written, so a refused route consumes nothing.
    A wrong vendor or a wrong model is not a transient condition and not a
    budget question: it means this run is not the run that was authorised.
    """


class ProgramBudgetExhausted(CallBudgetExhausted):
    """The cumulative authorisation is spent, across every phase and process.

    A subclass of the existing per-run exhaustion so every caller that already
    stops on a spent budget keeps stopping, and only code that needs to tell
    "this invocation's cap" from "the programme's cap" has to know there are
    two.
    """


@dataclass(frozen=True)
class Authorization:
    """One standing permission to make requests, named and bounded.

    Frozen and constructed in code rather than from a flag, because every field
    is something a person decided once. A CLI that could raise `max_requests`
    would make the ceiling a suggestion.
    """

    authorization_id: str
    #: Cumulative maximum actual inference HTTP requests, for the life of the
    #: authorisation. Never reset, never per-phase, never per-process.
    max_requests: int
    #: The only vendor this authorisation covers.
    vendor: str
    #: The only model id it covers. `ModelConfig.identifier`, not `arm`: a
    #: reasoning setting is a setting, and pinning the arm would refuse the same
    #: model at a different one rather than reporting it.
    model: str
    #: Retries the vendor SDK may perform on its own. Zero everywhere: a retry
    #: nobody counted is a request nobody authorised.
    sdk_retries: int = 0
    #: The route must be a recurring-free one. A configured key is not
    #: permission and a promotional credit is not free.
    require_zero_cost: bool = True
    provider_fallback: bool = False
    model_fallback: bool = False
    #: The only phase in which a live request may be reserved, if the
    #: authorisation is narrowed to one.
    #:
    #: An authorisation outlives the route it was written for. This one began as
    #: google/gemma-4-31b-it, spent three requests there, and was then narrowed
    #: to a single OpenRouter recheck -- WITHOUT resetting the budget, because
    #: the ceiling is a property of the programme and not of the model it
    #: happened to be pointed at. The three Gemma events stay valid and
    #: auditable; what changes is what may be reserved NEXT.
    active_phase: str | None = None
    #: How many requests that phase may reserve in total, across every process.
    phase_max_requests: int | None = None

    def refusal_for(self, config: ModelConfig, phase: str | None = None) -> str | None:
        """Why this arm is not covered, or `None` if it is."""
        if self.active_phase is not None and phase != self.active_phase:
            return (
                f"authorisation {self.authorization_id} is narrowed to phase "
                f"{self.active_phase!r} and this request names {phase!r}; earlier phases are "
                "history and cannot be reopened"
            )
        if config.vendor != self.vendor:
            return (
                f"authorisation {self.authorization_id} covers vendor {self.vendor!r} "
                f"and this request names {config.vendor!r}; provider fallback is disabled"
            )
        if config.identifier != self.model:
            return (
                f"authorisation {self.authorization_id} covers model {self.model!r} "
                f"and this request names {config.identifier!r}; model fallback is disabled"
            )
        return None


#: The authorisation this programme runs under, pinned by value.
#:
#: Four hundred requests, one vendor, one model, no fallback, no paid route, and
#: no reset between the eight phases. Written here rather than passed in so that
#: reading the code answers "what is authorised" without reading a command line
#: somebody typed once.
#: NARROWED, not replaced, and not reset. Four requests have been spent under
#: this id -- three against google/gemma-4-31b-it and one against the OpenRouter
#: free GLM slug. They remain in the ledger, remain counted, and remain
#: auditable. Every Gemma arm is frozen; the only route that may reserve a NEW
#: request is `z-ai/glm-5.2:free`.
#:
#: The phase moves from GLM_RECHECK to GLM_FINAL_CANARY, which is how a spent
#: phase stays spent. GLM_RECHECK authorised one request and used it on an
#: upstream 429; its cap is not refunded and its events are not rewritten. The
#: new phase authorises two, because this one may need a conditional
#: ProviderFamily call after a passing DescriptionFamily -- and two is the whole
#: of it.
#:
#: A second 400-call ledger for GLM would have been the easy shape and the wrong
#: one: two budgets that each believe they are the ceiling are not a ceiling.
PROGRAM_AUTHORIZATION = Authorization(
    authorization_id="m2-completion-gemma-v8-free-2026-09-04",
    max_requests=400,
    vendor="openrouter",
    model="z-ai/glm-5.2:free",
    active_phase="GLM_FINAL_CANARY",
    phase_max_requests=2,
)


# =========================================================================
# THE INTER-PROCESS LOCK
# =========================================================================


#: How long a process waits for the ledger before giving up. Generous, because
#: the critical section is a few milliseconds of file IO and anything longer
#: means something is genuinely wrong; bounded, because a run that blocks
#: forever on a lock is indistinguishable from a hung vendor call.
LOCK_TIMEOUT_SECONDS = 30.0

#: How often a waiter retries. Short enough that contention costs nothing real.
LOCK_POLL_SECONDS = 0.01


class LedgerLockUnavailable(RunHalted):
    """The ledger could not be locked, so nothing may be reserved.

    A run that cannot take the lock has not been refused a budget; it has been
    unable to ASK. Both end the same way -- no request -- and the message says
    which, because the fixes are entirely different.
    """


# `sys.platform` rather than `os.name`, because a type checker narrows on the
# first and not the second -- and a branch it cannot narrow is a branch it must
# check against the wrong platform's stubs.
if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
    import msvcrt

    def _take(handle: Any) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _drop(handle: Any) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:  # pragma: no cover - exercised on POSIX only
    import fcntl

    def _take(handle: Any) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _drop(handle: Any) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive(lock_path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Hold an exclusive inter-process lock on a sidecar file.

    THE LOCK IS THE OPERATING SYSTEM'S, NOT OURS
    --------------------------------------------
    `flock` and `msvcrt.locking` are both released by the kernel when the
    holding process exits or its handle closes, however it exits. A lock built
    from "create a file, delete it afterwards" would instead leave a stale file
    behind every time a run was killed, and every later run would need somebody
    to decide whether that file meant anything. This needs no cleanup, ever.

    A sidecar rather than the ledger itself: locking a file you are also
    appending to means the lock byte moves under you, and on Windows the region
    is tied to the file position. The ledger stays a file that is only ever read
    or appended to.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with lock_path.open("a+b") as handle:
        while True:
            try:
                _take(handle)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise LedgerLockUnavailable(
                        f"could not lock {lock_path} within {timeout:.0f}s: another process is "
                        "holding the request budget. Nothing was reserved and nothing was sent."
                    ) from exc
                time.sleep(LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            _drop(handle)


@dataclass(frozen=True)
class Reservation:
    """One request, authorised and recorded, not yet answered."""

    seq: int
    cache_key: str
    family: Family


class BudgetLedger:
    """An append-only JSONL file, and the cumulative count derived from it.

    Opened for appending and for nothing else. There is no update path, no
    truncate, no seek and no upsert -- not as a policy anyone has to remember,
    but because no method here does anything except read the whole file or add
    a line to the end of it.
    """

    def __init__(self, path: Path, authorization: Authorization) -> None:
        self.path = path
        #: A sidecar the ledger is never read from or written to. Its only
        #: content is the lock the operating system holds on it.
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.authorization = authorization
        self._seq = 0
        self._reserved = 0
        self._not_sent = 0
        self._seed()

    # -- reading -----------------------------------------------------------

    @classmethod
    def for_authorization(
        cls, authorization: Authorization, *, directory: Path | None = None
    ) -> BudgetLedger:
        """The ledger for one authorisation, at its conventional path."""
        base = LEDGER_DIR if directory is None else directory
        return cls(base / f"{authorization.authorization_id}.jsonl", authorization)

    def _seed(self) -> None:
        """Re-derive the cumulative state from the file. Idempotent.

        Called in the constructor, so a fresh process is never a fresh budget,
        and again inside the lock before every append, so a count is never
        older than the file it is about. A line belonging to another
        authorisation is a mixed file and is refused rather than skipped:
        silently ignoring it would make the count depend on which lines this
        reader happened to recognise.
        """
        self._seq = 0
        self._reserved = 0
        self._not_sent = 0
        if not self.path.exists():
            return
        for number, line in enumerate(self._lines(), start=1):
            record = json.loads(line)
            if record.get("authorization_id") != self.authorization.authorization_id:
                raise RunHalted(
                    f"{self.path} line {number} belongs to authorisation "
                    f"{record.get('authorization_id')!r}, not "
                    f"{self.authorization.authorization_id!r}. One file per authorisation; a "
                    "mixed ledger cannot be counted."
                )
            seq = int(record["seq"])
            if seq != self._seq + 1:
                raise RunHalted(
                    f"{self.path} line {number} has sequence {seq} where {self._seq + 1} was "
                    "expected. The sequence is contiguous by construction, so a gap means a "
                    "line was removed or the file was written by something else."
                )
            self._seq = seq
            kind = record["event"]
            if kind == LedgerEvent.RESERVED:
                self._reserved += 1
            elif kind == LedgerEvent.NOT_SENT:
                self._not_sent += 1

    def _lines(self) -> Iterator[str]:
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield line

    def events(self) -> list[dict[str, Any]]:
        """Every event, in order. For audits and for the PHASE H checks."""
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self._lines()]

    @property
    def consumed(self) -> int:
        """Actual inference HTTP requests this authorisation has spent.

        `RESERVED - NOT_SENT`, and nothing else. A reservation with no outcome
        at all -- the process died mid-request -- still counts, which is the
        conservative reading and the only safe one.
        """
        return self._reserved - self._not_sent

    @property
    def remaining(self) -> int:
        return max(0, self.authorization.max_requests - self.consumed)

    def spent_in(self, phase: str | None) -> int:
        """Reservations recorded under one phase. Read from the file, not a memory.

        Reservations only: a phase cap bounds what was ASKED, and the refund
        rule that makes `consumed` conservative would let a phase re-ask after a
        request that never reached the wire. That is the right reading for a
        budget and the wrong one for "you may try this once".
        """
        return sum(
            1
            for event in self.events()
            if event.get("event") == LedgerEvent.RESERVED and event.get("phase") == phase
        )

    # -- writing -----------------------------------------------------------

    def _append(
        self, event: LedgerEvent, guard: Callable[[], None] | None = None, **fields: Any
    ) -> int:
        """The whole critical section, under one exclusive inter-process lock.

        Acquire, re-read the file, re-derive consumed and remaining, run the
        caller's guard against the FRESH numbers, take the next sequence,
        append, fsync, release. Every step between the read and the write is
        inside the lock, which is what makes "the next sequence" and "one below
        the ceiling" mean the same thing to two processes as to one.

        Before the lock existed, two processes could read the same count and
        both write it back: duplicate sequences that made the file unloadable,
        and one request more than was authorised. Both are proven in
        `test_budget_ledger_concurrency.py`, against this code.

        `flush` then `fsync`: without the second, a reservation can be lost to a
        power cut while the request it authorised has already been made, and the
        budget would then under-count the one direction it must not.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive(self.lock_path):
            return self._append_locked(event, guard, **fields)

    def _append_locked(
        self, event: LedgerEvent, guard: Callable[[], None] | None = None, **fields: Any
    ) -> int:
        self._seed()
        if guard is not None:
            guard()
        self._seq += 1
        record = {
            "format": LEDGER_FORMAT,
            "authorization_id": self.authorization.authorization_id,
            "seq": self._seq,
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": str(event),
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        # Count the line we just wrote. `_seed` read the file as it was BEFORE
        # this append, so without this the object would under-report its own
        # reservation until something reloaded it -- and `remaining` is printed
        # to a person deciding whether to authorise more.
        if event is LedgerEvent.RESERVED:
            self._reserved += 1
        elif event is LedgerEvent.NOT_SENT:
            self._not_sent += 1
        return self._seq

    def refuse_unauthorized(self, config: ModelConfig, phase: str | None = None) -> None:
        """Check the arm against the authorisation, before anything is written."""
        refusal = self.authorization.refusal_for(config, phase)
        if refusal is not None:
            raise AuthorizationViolation(refusal)

    def reserve(
        self,
        *,
        config: ModelConfig,
        family: Family,
        cache_key: str,
        execution_id: str,
        phase: str,
        job_id: str,
    ) -> Reservation:
        """Authorise one request and record it, before it is made.

        Order matters and is the point of the whole module: the arm is checked,
        then the cumulative budget, then the line is written and fsynced, and
        only then may a caller reach the transport. A refusal at either check
        writes nothing, so a request that was never allowed never appears to
        have been spent.
        """
        self.refuse_unauthorized(config, phase)

        def ceiling() -> None:
            """Checked inside the lock, against the file, not against a memory.

            Two processes racing for the last request both used to read the same
            count and both pass. Now the loser reads the winner's line and stops
            here -- before the transport, and before a line of its own exists.
            """
            if self.consumed + 1 > self.authorization.max_requests:
                raise ProgramBudgetExhausted(
                    f"authorisation {self.authorization.authorization_id} allows "
                    f"{self.authorization.max_requests} inference request(s) in total and has "
                    f"spent {self.consumed}; the {family} request for cache key "
                    f"{cache_key[:24]}... was not sent"
                )
            # The phase cap, counted off the file inside the same lock as the
            # global one. A recheck authorised for one request is one request
            # however many terminals ask for it.
            cap = self.authorization.phase_max_requests
            if cap is not None and self.spent_in(phase) + 1 > cap:
                raise ProgramBudgetExhausted(
                    f"phase {phase!r} of authorisation "
                    f"{self.authorization.authorization_id} allows {cap} request(s) and has "
                    f"spent {self.spent_in(phase)}; the {family} request for cache key "
                    f"{cache_key[:24]}... was not sent"
                )

        seq = self._append(
            LedgerEvent.RESERVED,
            ceiling,
            execution_id=execution_id,
            phase=phase,
            job_id=job_id,
            family=str(family),
            provider=config.vendor,
            model=config.arm,
            cache_key=cache_key,
        )
        # `_seed` inside the lock already counted this line back off the file.
        return Reservation(seq=seq, cache_key=cache_key, family=family)

    def settle(
        self, reservation: Reservation, event: LedgerEvent, *, detail: str | None = None
    ) -> None:
        """Record what became of a reservation.

        `NOT_SENT` refunds; every other outcome leaves the request spent. The
        outcome names the reservation it settles rather than repeating its
        identity, so a line can never disagree with the one it refers to.
        """
        if event is LedgerEvent.RESERVED:
            raise ValueError("RESERVED is not an outcome")
        self._append(
            event,
            None,
            reserves=reservation.seq,
            **({"detail": detail[:200]} if detail else {}),
        )


def outcome_for(message: str) -> LedgerEvent:
    """Which outcome an error after the wire attempt is.

    A timeout is called out because it is the case most easily mistaken for
    "nothing happened": the request went out, the vendor may well have served
    it, and the quota moved. Everything else that answered badly is `FAILED`.
    """
    lowered = message.lower()
    if "timeout" in lowered or "timed out" in lowered:
        return LedgerEvent.TIMEOUT
    return LedgerEvent.FAILED


@dataclass(frozen=True)
class LedgerAudit:
    """What an integrity pass found. Empty `problems` is the only pass.

    Separate from the ledger itself so that reading a ledger and judging one are
    different acts: `BudgetLedger` refuses a file it cannot count, and this
    answers the wider question PHASE H asks -- does the file agree with the
    database, and could a request have been made without being reserved.
    """

    authorization_id: str
    events: int
    reserved: int
    not_sent: int
    consumed: int
    #: `llm_call` rows attributable to this ledger's own executions, or None
    #: when no database was supplied.
    recorded_calls: int | None
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_ledger(ledger: BudgetLedger, conn: Any | None = None) -> LedgerAudit:
    """Check one ledger's internal consistency, and its agreement with the DB.

    Four questions, and the fourth is the one that matters:

    * is the sequence contiguous and monotonic from 1;
    * does every outcome refer to a reservation that exists, exactly once;
    * is the consumed count within the authorisation;
    * and are there MORE recorded requests than reservations.

    The last is the only asymmetric one, deliberately. Fewer `llm_call` rows
    than reservations is explainable and sometimes expected -- a persist failure
    loses a row, and a process killed mid-request never writes one. More rows
    than reservations cannot happen if every request passed through `reserve`,
    so it is the signature of a live call nobody authorised.
    """
    events = ledger.events()
    problems: list[str] = []

    reserved_seqs: set[int] = set()
    settled: dict[int, list[str]] = {}
    for position, record in enumerate(events, start=1):
        if record.get("seq") != position:
            problems.append(f"line {position} carries sequence {record.get('seq')!r}")
        if record.get("authorization_id") != ledger.authorization.authorization_id:
            problems.append(f"line {position} belongs to another authorisation")
        kind = record.get("event")
        if kind == LedgerEvent.RESERVED:
            reserved_seqs.add(int(record["seq"]))
        else:
            target = record.get("reserves")
            if target is None:
                if kind != LedgerEvent.PERSIST_FAILED:
                    problems.append(f"line {position} is an outcome naming no reservation")
            else:
                settled.setdefault(int(target), []).append(str(kind))

    for target, kinds in settled.items():
        if target not in reserved_seqs:
            problems.append(f"outcome(s) {kinds} settle reservation {target}, which does not exist")
        terminal = [k for k in kinds if k != LedgerEvent.PERSIST_FAILED]
        if len(terminal) > 1:
            problems.append(f"reservation {target} was settled more than once: {terminal}")

    if ledger.consumed > ledger.authorization.max_requests:
        problems.append(
            f"consumed {ledger.consumed} exceeds the authorised {ledger.authorization.max_requests}"
        )

    recorded: int | None = None
    if conn is not None:
        executions = {
            str(record["execution_id"])
            for record in events
            if record.get("event") == LedgerEvent.RESERVED and record.get("execution_id")
        }
        if executions:
            placeholders = ",".join("?" * len(executions))
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM llm_call WHERE execution_id IN ({placeholders})",
                tuple(executions),
            ).fetchone()
            recorded = int(row["n"] if hasattr(row, "keys") else row[0])
        else:
            recorded = 0
        if recorded > len(reserved_seqs):
            problems.append(
                f"{recorded} llm_call row(s) for this ledger's executions against "
                f"{len(reserved_seqs)} reservation(s): a request was made without one"
            )

    return LedgerAudit(
        authorization_id=ledger.authorization.authorization_id,
        events=len(events),
        reserved=len(reserved_seqs),
        not_sent=sum(1 for record in events if record.get("event") == LedgerEvent.NOT_SENT),
        consumed=ledger.consumed,
        recorded_calls=recorded,
        problems=tuple(problems),
    )
