"""The durable request budget, and the orderings that make it one.

Two properties carry the whole module, and neither can be checked by reading it:

* `RESERVED` is on disk, fsynced, BEFORE the transport is touched. So a process
  that dies mid-request has already spent the request, and a resumed run counts
  it. The test for this asserts on the file from inside a fake transport.
* only `LLMNotSent` refunds. A timeout, a 429 and a lost attempt row all leave
  the request spent, because the vendor was asked and a quota does not care what
  we did with the answer.

Everything else here is a consequence of those two, plus the rule that no flag
may widen a standing authorisation.

No network, no vendor client, no real clock.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_agent.llm.budget import (
    PROGRAM_AUTHORIZATION,
    Authorization,
    AuthorizationViolation,
    BudgetLedger,
    LedgerEvent,
    ProgramBudgetExhausted,
    outcome_for,
    verify_ledger,
)
from career_agent.llm.client import (
    Family,
    LLMError,
    LLMNotSent,
    LLMRequest,
    LLMResponse,
    ModelConfig,
    Runner,
)
from career_agent.llm.pacing import RateLimiter
from career_agent.llm.pricing import observed_price
from career_agent.pipeline.live import LiveCallBudgetExhausted, PacedLiveClient

ARM = ModelConfig(vendor="google", identifier="gemma-4-31b-it", reasoning="high")
OTHER_MODEL = ModelConfig(vendor="google", identifier="gemini-3.1-flash-lite")
OTHER_VENDOR = ModelConfig(vendor="openrouter", identifier="gemma-4-31b-it")


def authorization(max_requests: int = 400) -> Authorization:
    return Authorization(
        authorization_id="test-auth",
        max_requests=max_requests,
        vendor="google",
        model="gemma-4-31b-it",
    )


def ledger(tmp_path: Path, max_requests: int = 400) -> BudgetLedger:
    return BudgetLedger.for_authorization(
        authorization(max_requests), directory=tmp_path / "budget"
    )


def request(key: str = "sha256:key") -> LLMRequest:
    return LLMRequest(
        family=Family.DESCRIPTION,
        system="s",
        user="u",
        schema={},
        schema_name="n",
        cache_key=key,
    )


class NoCache:
    """A call repository that never has an answer. The cache is not under test."""

    def validated_answer(self, *args: object, **kwargs: object) -> None:
        return None


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def limiter() -> RateLimiter:
    clock = Clock()
    return RateLimiter(
        requests_per_minute=1_000,
        tokens_per_minute=10_000_000,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


#: The dated free-tier observation for the authorised arm. Passed everywhere,
#: because the money guard refuses an arm nobody priced -- correctly, and it is
#: not the guard under test here.
FREE = observed_price("google", "gemma-4-31b-it")


def paced(led: BudgetLedger, inner: object, *, max_live_calls: int = 100) -> PacedLiveClient:
    return PacedLiveClient(
        inner=inner,  # type: ignore[arg-type]
        calls=NoCache(),  # type: ignore[arg-type]
        limiter=limiter(),
        max_live_calls=max_live_calls,
        price=FREE,
        ledger=led,
        execution_id="exec-1",
        phase="B-0",
        job_id="job-1",
    )


class Answering:
    vendor = "google"
    runner = Runner.PRODUCTION_API

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        self.calls += 1
        return LLMResponse(raw_text='{"a": 1}', model=config.arm)


class Raising:
    vendor = "google"
    runner = Runner.PRODUCTION_API

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    def complete(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        self.calls += 1
        raise self.exc


# =========================================================================
# 1. RESERVED IS DURABLE BEFORE THE TRANSPORT IS TOUCHED
# =========================================================================


def test_the_reservation_is_on_disk_before_the_transport_is_called(tmp_path: Path) -> None:
    """Asserted from inside the transport, which is the only honest place.

    A test that checked the file afterwards would pass just as well if the
    reservation were written after the response came back -- and that ordering
    is the one failure this module exists to prevent.
    """
    led = ledger(tmp_path)
    seen: list[dict[str, object]] = []

    class Inspecting:
        vendor = "google"
        runner = Runner.PRODUCTION_API

        def complete(self, req: LLMRequest, config: ModelConfig) -> LLMResponse:
            seen.extend(json.loads(line) for line in led.path.read_text().splitlines())
            return LLMResponse(raw_text='{"a": 1}', model=config.arm)

    paced(led, Inspecting()).complete(request(), ARM)

    assert len(seen) == 1, "the transport ran with no reservation on disk"
    assert seen[0]["event"] == "RESERVED"
    assert seen[0]["authorization_id"] == "test-auth"
    assert seen[0]["seq"] == 1
    assert seen[0]["phase"] == "B-0"
    assert seen[0]["job_id"] == "job-1"
    assert seen[0]["execution_id"] == "exec-1"
    assert seen[0]["family"] == "description"
    assert seen[0]["provider"] == "google"
    assert seen[0]["model"] == "gemma-4-31b-it@high"
    assert seen[0]["cache_key"] == "sha256:key"


def test_a_crash_between_the_reservation_and_the_outcome_still_counts(tmp_path: Path) -> None:
    """The pessimistic direction, on purpose.

    `BaseException` is not an `LLMError`, so nothing settles the reservation --
    exactly what a `SIGKILL` looks like from the ledger's point of view. A fresh
    reader then finds a reservation with no outcome, and counts it.
    """
    led = ledger(tmp_path)

    class Dying:
        vendor = "google"
        runner = Runner.PRODUCTION_API

        def complete(self, req: LLMRequest, config: ModelConfig) -> LLMResponse:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        paced(led, Dying()).complete(request(), ARM)

    resumed = ledger(tmp_path)
    assert resumed.consumed == 1, "an unsettled reservation must not be free"
    assert resumed.remaining == 399
    assert [event["event"] for event in resumed.events()] == ["RESERVED"]


# =========================================================================
# 2. WHAT REFUNDS, AND WHAT DOES NOT
# =========================================================================


def test_a_request_that_never_reached_http_is_refunded(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    client = paced(led, Raising(LLMNotSent("SDK rejected an unknown keyword")))

    with pytest.raises(LLMNotSent):
        client.complete(request(), ARM)

    assert led.consumed == 0, "nothing was asked, so nothing was spent"
    assert client.live_calls == 0, "the two counters agree by construction"
    assert client.local_failures == 1
    assert [event["event"] for event in led.events()] == ["RESERVED", "NOT_SENT"]
    assert ledger(tmp_path).consumed == 0, "and the refund survives a restart"


def test_a_timeout_is_not_refunded(tmp_path: Path) -> None:
    """The vendor was asked. It may even have served it. The quota moved."""
    led = ledger(tmp_path)
    client = paced(led, Raising(LLMError("504 request timed out after 60s")))

    with pytest.raises(LLMError):
        client.complete(request(), ARM)

    assert led.consumed == 1
    assert client.live_calls == 1
    assert client.local_failures == 0
    assert [event["event"] for event in led.events()] == ["RESERVED", "TIMEOUT"]
    assert ledger(tmp_path).consumed == 1


def test_an_error_response_is_not_refunded(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    with pytest.raises(LLMError):
        paced(led, Raising(LLMError("429 rate limit exceeded"))).complete(request(), ARM)

    assert led.consumed == 1
    assert [event["event"] for event in led.events()] == ["RESERVED", "FAILED"]


def test_a_persist_failure_consumes_and_says_so(tmp_path: Path) -> None:
    """It is a note, not a refund: the request happened, and its record did not."""
    led = ledger(tmp_path)
    client = paced(led, Answering())
    client.complete(request(), ARM)
    assert client.last_reservation is not None
    led.settle(client.last_reservation, LedgerEvent.PERSIST_FAILED, detail="disk is full")

    assert led.consumed == 1
    assert [event["event"] for event in led.events()] == ["RESERVED", "SENT", "PERSIST_FAILED"]
    assert ledger(tmp_path).consumed == 1


def test_the_outcome_classifier_separates_a_timeout_from_everything_else() -> None:
    assert outcome_for("504 Gateway Timeout") is LedgerEvent.TIMEOUT
    assert outcome_for("the request timed out") is LedgerEvent.TIMEOUT
    assert outcome_for("429 rate limit") is LedgerEvent.FAILED
    assert outcome_for("500 internal error") is LedgerEvent.FAILED


# =========================================================================
# 3. A FRESH PROCESS IS NOT A FRESH BUDGET
# =========================================================================


def test_a_new_process_seeds_from_the_file_and_continues_the_count(tmp_path: Path) -> None:
    first = ledger(tmp_path)
    inner = Answering()
    for index in range(3):
        paced(first, inner).complete(request(f"sha256:key-{index}"), ARM)
    assert first.consumed == 3

    # A new object, reading only the file. This is what a new terminal is.
    second = ledger(tmp_path)
    assert second.consumed == 3
    assert second.remaining == 397

    paced(second, inner).complete(request("sha256:key-4"), ARM)
    assert second.consumed == 4
    assert ledger(tmp_path).consumed == 4
    assert [event["seq"] for event in ledger(tmp_path).events()] == [1, 2, 3, 4, 5, 6, 7, 8]


def test_the_budget_does_not_reset_between_phases(tmp_path: Path) -> None:
    """Two phases, one authorisation. The second starts where the first stopped."""
    phase_b = ledger(tmp_path)
    paced(phase_b, Answering()).complete(request("sha256:b"), ARM)

    phase_c = ledger(tmp_path)
    client = PacedLiveClient(
        inner=Answering(),  # type: ignore[arg-type]
        calls=NoCache(),  # type: ignore[arg-type]
        limiter=limiter(),
        max_live_calls=100,
        price=FREE,
        ledger=phase_c,
        execution_id="exec-2",
        phase="C",
    )
    client.complete(request("sha256:c"), ARM)

    events = ledger(tmp_path).events()
    assert [event["phase"] for event in events if event["event"] == "RESERVED"] == ["B-0", "C"]
    assert ledger(tmp_path).consumed == 2


# =========================================================================
# 4. THE CEILING STOPS BEFORE THE REQUEST
# =========================================================================


def test_the_request_after_the_last_authorised_one_is_refused_before_the_transport(
    tmp_path: Path,
) -> None:
    """A three-request authorisation, and the fourth never reaches a client."""
    led = ledger(tmp_path, max_requests=3)
    inner = Answering()
    for index in range(3):
        paced(led, inner).complete(request(f"sha256:key-{index}"), ARM)

    assert led.consumed == 3
    assert led.remaining == 0
    with pytest.raises(ProgramBudgetExhausted):
        paced(led, inner).complete(request("sha256:one-too-many"), ARM)

    assert inner.calls == 3, "the transport was reached three times, not four"
    assert led.consumed == 3, "a refusal writes nothing"
    assert len(led.events()) == 6, "three reservations, three outcomes, no fourth line"


def test_exhaustion_survives_a_restart(tmp_path: Path) -> None:
    led = ledger(tmp_path, max_requests=1)
    inner = Answering()
    paced(led, inner).complete(request(), ARM)

    with pytest.raises(ProgramBudgetExhausted):
        paced(ledger(tmp_path, max_requests=1), inner).complete(request("sha256:next"), ARM)
    assert inner.calls == 1


# =========================================================================
# 5. A FLAG MAY NARROW THE BUDGET AND NEVER WIDEN IT
# =========================================================================


def test_a_lower_per_invocation_cap_wins(tmp_path: Path) -> None:
    led = ledger(tmp_path, max_requests=400)
    inner = Answering()
    client = paced(led, inner, max_live_calls=1)
    client.complete(request(), ARM)

    assert client.remaining_budget == 0
    with pytest.raises(LiveCallBudgetExhausted) as raised:
        client.complete(request("sha256:next"), ARM)
    assert not isinstance(raised.value, ProgramBudgetExhausted), (
        "the per-invocation cap stopped this, and the message should say so"
    )
    assert inner.calls == 1
    assert led.remaining == 399, "the programme budget was not touched by the flag"


def test_a_higher_per_invocation_cap_does_not_raise_the_programme_budget(tmp_path: Path) -> None:
    """The flag a person types cannot exceed the authorisation nobody may edit."""
    led = ledger(tmp_path, max_requests=2)
    inner = Answering()
    client = paced(led, inner, max_live_calls=1_000_000)

    client.complete(request("sha256:a"), ARM)
    client.complete(request("sha256:b"), ARM)
    assert client.remaining_budget == 0, "bounded by the ledger, not by the flag"

    with pytest.raises(ProgramBudgetExhausted):
        client.complete(request("sha256:c"), ARM)
    assert inner.calls == 2


# =========================================================================
# 6. AN UNAUTHORISED ARM IS REFUSED BEFORE A RESERVATION EXISTS
# =========================================================================


def test_another_model_is_refused_before_any_reservation(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    inner = Answering()
    with pytest.raises(AuthorizationViolation) as raised:
        paced(led, inner).complete(request(), OTHER_MODEL)

    assert "model fallback is disabled" in str(raised.value)
    assert inner.calls == 0
    assert led.events() == [], "a refused arm leaves no trace of a reservation"
    assert not led.path.exists(), "and does not even create the file"


def test_another_vendor_is_refused_before_any_reservation(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    inner = Answering()
    with pytest.raises(AuthorizationViolation) as raised:
        paced(led, inner).complete(request(), OTHER_VENDOR)

    assert "provider fallback is disabled" in str(raised.value)
    assert inner.calls == 0
    assert led.consumed == 0


def test_the_same_model_at_another_reasoning_setting_is_covered(tmp_path: Path) -> None:
    """The authorisation names a model, not an arm.

    A reasoning setting is a setting. Pinning the arm would refuse
    `gemma-4-31b-it@low` as though it were a different model, which would be a
    refusal nobody wrote and a confusing one to debug.
    """
    led = ledger(tmp_path)
    low = ModelConfig(vendor="google", identifier="gemma-4-31b-it", reasoning="low")
    paced(led, Answering()).complete(request(), low)
    assert led.consumed == 1
    assert led.events()[0]["model"] == "gemma-4-31b-it@low"


# =========================================================================
# 7. THE FILE IS APPEND-ONLY
# =========================================================================


def test_every_write_appends_and_nothing_is_ever_rewritten(tmp_path: Path) -> None:
    """Asserted on the bytes: every earlier line survives every later write."""
    led = ledger(tmp_path)
    inner = Answering()
    snapshots: list[str] = []
    for index in range(3):
        paced(led, inner).complete(request(f"sha256:key-{index}"), ARM)
        snapshots.append(led.path.read_text(encoding="utf-8"))

    for earlier, later in zip(snapshots, snapshots[1:], strict=False):
        assert later.startswith(earlier), "a later write changed an earlier line"
    assert len(snapshots[-1].splitlines()) == 6


def test_the_ledger_opens_the_file_only_to_append_or_to_read() -> None:
    """Every `open` in the module, by mode, read out of the syntax tree.

    A prose check would collide with the docstring that explains the rule, and a
    check for a forbidden method NAME would pass the day somebody reached the
    same result another way. So this enumerates the modes actually passed to
    `open` and allows exactly two: append, and read.
    """
    import ast
    import inspect

    from career_agent.llm import budget

    tree = ast.parse(inspect.getsource(budget))
    modes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        )
        if name != "open":
            continue
        first = node.args[0] if node.args else None
        modes.append(first.value if isinstance(first, ast.Constant) else "<computed>")

    assert modes, "the module opens no file at all, which cannot be right"
    # "a"    -- the ledger itself, append only.
    # "r"    -- reading it back.
    # "a+b"  -- the LOCK SIDECAR, which holds no data at all; it needs a
    #           readable handle for msvcrt to lock a byte of, and "a" never
    #           truncates, so the no-rewrite guarantee is unaffected.
    assert set(modes) <= {"a", "r", "a+b"}, f"the ledger opens a file in mode(s) {set(modes)}"
    assert "a" in modes, "nothing appends"
    assert not any(m.startswith(("w", "r+", "x")) for m in modes), (
        "a truncating or seeking handle would put the append-only claim in doubt"
    )


def test_a_gap_in_the_sequence_is_refused_rather_than_counted(tmp_path: Path) -> None:
    """A file somebody edited is a file nobody can count."""
    led = ledger(tmp_path)
    inner = Answering()
    for index in range(3):
        paced(led, inner).complete(request(f"sha256:key-{index}"), ARM)

    lines = led.path.read_text(encoding="utf-8").splitlines()
    led.path.write_text("\n".join(lines[:2] + lines[3:]) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="sequence"):
        ledger(tmp_path)


def test_a_ledger_holding_another_authorisation_is_refused(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    paced(led, Answering()).complete(request(), ARM)
    other = Authorization(
        authorization_id="test-auth", max_requests=400, vendor="google", model="gemma-4-31b-it"
    )
    # Same path, different id inside: what a copied file looks like.
    path = led.path
    path.write_text(
        path.read_text(encoding="utf-8").replace('"test-auth"', '"someone-elses"'), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="authorisation"):
        BudgetLedger(path, other)


# =========================================================================
# 8. NO PRODUCTION PATH CAN SEND WITHOUT A RESERVATION
# =========================================================================


def test_a_paced_client_cannot_be_built_without_a_ledger() -> None:
    """The structural half of the guarantee.

    `PacedLiveClient` is the only thing `extract_job` is ever handed, and it has
    no ledger default. So "a live request with no reservation" is not a mistake
    somebody could make by forgetting an argument -- it does not construct.
    """
    with pytest.raises(TypeError):
        PacedLiveClient(  # type: ignore[call-arg]
            inner=Answering(),  # type: ignore[arg-type]
            calls=NoCache(),  # type: ignore[arg-type]
            limiter=limiter(),
            max_live_calls=1,
        )


def test_a_cache_hit_reaches_neither_the_transport_nor_the_ledger(tmp_path: Path) -> None:
    """Replay is not inference. It consumes no quota and must consume no budget."""

    class Cached:
        def validated_answer(self, *args: object, **kwargs: object) -> object:
            class Answer:
                raw_output = '{"a": 1}'
                model = "gemma-4-31b-it@high"
                input_tokens = 10
                output_tokens = 20

            return Answer()

    led = ledger(tmp_path)
    inner = Answering()
    client = PacedLiveClient(
        inner=inner,  # type: ignore[arg-type]
        calls=Cached(),  # type: ignore[arg-type]
        limiter=limiter(),
        max_live_calls=1,
        price=FREE,
        ledger=led,
    )
    client.complete(request(), ARM)

    assert client.cache_hits == 1
    assert inner.calls == 0
    assert led.consumed == 0
    assert not led.path.exists()


# =========================================================================
# 9. THE AUTHORISATION THIS PROGRAMME RUNS UNDER
# =========================================================================


def test_the_programme_authorisation_is_what_was_authorised() -> None:
    """Explicit expected values, not a restatement of the constant.

    Every field here was a sentence in the authorisation. A test reading them
    back off the object it is checking would pass after any edit, which is the
    one thing this must not do.

    NARROWED, not replaced. The id and the 400-request ceiling are the same ones
    three Gemma requests were spent under; what moved is which route may reserve
    the NEXT one, and it may do so once, in one phase.
    """
    assert PROGRAM_AUTHORIZATION.authorization_id == "m2-completion-gemma-v8-free-2026-09-04"
    assert PROGRAM_AUTHORIZATION.max_requests == 400
    assert PROGRAM_AUTHORIZATION.vendor == "openrouter"
    assert PROGRAM_AUTHORIZATION.model == "z-ai/glm-5.2:free"
    assert PROGRAM_AUTHORIZATION.active_phase == "GLM_FINAL_CANARY"
    assert PROGRAM_AUTHORIZATION.phase_max_requests == 2
    assert PROGRAM_AUTHORIZATION.sdk_retries == 0
    assert PROGRAM_AUTHORIZATION.require_zero_cost is True
    assert PROGRAM_AUTHORIZATION.provider_fallback is False
    assert PROGRAM_AUTHORIZATION.model_fallback is False


def test_the_programme_authorisation_refuses_every_arm_but_one() -> None:
    """One route, one phase. Gemma included in what is refused now."""
    glm = ModelConfig(vendor="openrouter", identifier="z-ai/glm-5.2:free", reasoning="high")
    assert PROGRAM_AUTHORIZATION.refusal_for(glm, "GLM_FINAL_CANARY") is None

    # The route three requests were already spent on is history, not a licence.
    assert PROGRAM_AUTHORIZATION.refusal_for(ARM, "GLM_FINAL_CANARY") is not None
    for other in (OTHER_MODEL, OTHER_VENDOR):
        assert PROGRAM_AUTHORIZATION.refusal_for(other, "GLM_FINAL_CANARY") is not None

    # And the right arm in a spent or wrong phase is still refused.
    for phase in (None, "B", "B-FC2", "SCREEN", "GLM_RECHECK"):
        assert PROGRAM_AUTHORIZATION.refusal_for(glm, phase) is not None


# =========================================================================
# 10. THE INTEGRITY PASS PHASE H WILL RUN
# =========================================================================


def test_a_healthy_ledger_passes_the_integrity_pass(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    inner = Answering()
    for index in range(3):
        paced(led, inner).complete(request(f"sha256:key-{index}"), ARM)

    audit = verify_ledger(led)
    assert audit.ok, audit.problems
    assert (audit.events, audit.reserved, audit.not_sent, audit.consumed) == (6, 3, 0, 3)
    assert audit.recorded_calls is None, "no database was supplied"


def test_the_integrity_pass_catches_an_outcome_with_no_reservation(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    paced(led, Answering()).complete(request(), ARM)
    with led.path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "format": 1,
                    "authorization_id": "test-auth",
                    "seq": 3,
                    "at": "2026-09-04T00:00:00Z",
                    "event": "SENT",
                    "reserves": 99,
                }
            )
            + "\n"
        )

    audit = verify_ledger(BudgetLedger(led.path, authorization()))
    assert not audit.ok
    assert any("does not exist" in problem for problem in audit.problems)


def test_the_integrity_pass_catches_more_recorded_calls_than_reservations(tmp_path: Path) -> None:
    """The asymmetric check, and the only one that can prove a bypass.

    Fewer rows than reservations is ordinary. More rows means a request was made
    that no reservation authorised, which is the failure the whole module exists
    to make impossible -- so it must also be detectable after the fact.
    """
    import sqlite3

    led = ledger(tmp_path)
    paced(led, Answering()).complete(request(), ARM)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE llm_call (execution_id TEXT)")
    conn.executemany("INSERT INTO llm_call VALUES (?)", [("exec-1",), ("exec-1",)])

    audit = verify_ledger(led, conn)
    assert not audit.ok
    assert audit.recorded_calls == 2
    assert any("without one" in problem for problem in audit.problems)


def test_the_integrity_pass_accepts_fewer_rows_than_reservations(tmp_path: Path) -> None:
    import sqlite3

    led = ledger(tmp_path)
    inner = Answering()
    for index in range(2):
        paced(led, inner).complete(request(f"sha256:key-{index}"), ARM)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE llm_call (execution_id TEXT)")
    conn.execute("INSERT INTO llm_call VALUES ('exec-1')")

    audit = verify_ledger(led, conn)
    assert audit.ok, audit.problems
    assert (audit.recorded_calls, audit.reserved) == (1, 2)
