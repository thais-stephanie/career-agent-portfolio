"""Local readings in the background, with a state a page can ask about.

A local reading takes minutes on a laptop CPU. It used to be one blocking
request: the page showed a spinner with no progress and no end, Cancel only
stopped the browser from waiting, and the model kept working. Now a reading is
a background run per posting, with an explicit state:

    NOT_RUN             never asked (the page's own default)
    RUNNING             phase: checking, loading, reading, writing, verifying
    SUCCESS             stored with the posting; shown on every later visit
    CANCELLED           the person stopped it; the connection to Ollama was
                        closed, which is what makes the model stop
    MODEL_MISSING       Ollama answers and the configured model is not installed
    OLLAMA_UNAVAILABLE  nothing answers at the local Ollama address
    TIMEOUT             it did not finish within the total deadline
    ERROR               anything else, in a sentence a person can read

Every state but RUNNING is final. Nothing here ever calls anything but the
local Ollama client (loopback only, see `ollama.assert_loopback`): there is no
fallback to a hosted provider, by construction.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

NOT_RUN = "NOT_RUN"
RUNNING = "RUNNING"
SUCCESS = "SUCCESS"
CANCELLED = "CANCELLED"
MODEL_MISSING = "MODEL_MISSING"
OLLAMA_UNAVAILABLE = "OLLAMA_UNAVAILABLE"
TIMEOUT = "TIMEOUT"
ERROR = "ERROR"

FINAL = frozenset({SUCCESS, CANCELLED, MODEL_MISSING, OLLAMA_UNAVAILABLE, TIMEOUT, ERROR})


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class LocalReading:
    job_id: str
    model: str
    state: str = RUNNING
    phase: str = "checking"
    tokens: int = 0
    message: str = ""
    code: str = ""
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    _started: float = field(default_factory=time.monotonic)
    _ended: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def as_dict(self) -> dict[str, Any]:
        end = self._ended if self._ended is not None else time.monotonic()
        return {
            "job_id": self.job_id,
            "model": self.model,
            "state": self.state,
            "phase": self.phase if self.state == RUNNING else None,
            "tokens": self.tokens,
            "message": self.message,
            "code": self.code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_s": round(end - self._started, 1),
            "cancel_requested": self.cancel.is_set() and self.state == RUNNING,
        }

    def finish(self, state: str, message: str = "", code: str = "") -> None:
        self.state = state
        self.message = message
        self.code = code
        self._ended = time.monotonic()
        self.finished_at = _now()


class LocalReadings:
    """The readings of one app (one profile), one per posting at most."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, LocalReading] = {}

    def start(
        self, job_id: str, model: str, work: Callable[[LocalReading], None]
    ) -> dict[str, Any]:
        """Begin a reading, or return the one already running for this posting."""
        with self._lock:
            current = self._runs.get(job_id)
            if current is not None and current.state == RUNNING:
                return current.as_dict()
            reading = LocalReading(job_id=job_id, model=model)
            self._runs[job_id] = reading

        def run() -> None:
            try:
                work(reading)
            except Exception as exc:  # noqa: BLE001 -- reported, never raised into a thread
                if reading.state == RUNNING:
                    reading.finish(ERROR, f"The local model could not finish: {exc}", "unexpected")
            if reading.state == RUNNING:
                reading.finish(ERROR, "The local model stopped without an answer.", "no_answer")

        threading.Thread(target=run, name=f"local-reading-{job_id}", daemon=True).start()
        return reading.as_dict()

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            reading = self._runs.get(job_id)
        return reading.as_dict() if reading is not None else None

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            reading = self._runs.get(job_id)
        if reading is None:
            return None
        if reading.state == RUNNING:
            reading.cancel.set()
        return reading.as_dict()

    def cancel_all(self) -> None:
        with self._lock:
            readings = list(self._runs.values())
        for reading in readings:
            if reading.state == RUNNING:
                reading.cancel.set()

    @property
    def running(self) -> bool:
        with self._lock:
            return any(r.state == RUNNING for r in self._runs.values())
