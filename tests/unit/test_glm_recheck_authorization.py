"""Narrowing an authorisation without resetting the budget it already spent.

Three requests have been made under `m2-completion-gemma-v8-free-2026-09-04`,
all against google/gemma-4-31b-it, and every Gemma arm is now frozen. The next
request is a single OpenRouter free-GLM recheck.

The easy shape would have been a second 400-call ledger for GLM. It would also
have been wrong: two budgets that each believe they are the ceiling are not a
ceiling. So the same authorisation is NARROWED -- same id, same 400, same three
consumed, same events -- and only what may be reserved next changes.

No inference. Every ledger below is a temporary file except where a test reads
the real one.
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
    verify_ledger,
)
from career_agent.llm.client import Family, ModelConfig
from career_agent.llm.routing import pinned_endpoint

PHASE = "GLM_FINAL_CANARY"
SPENT_PHASE = "GLM_RECHECK"
GLM = ModelConfig(vendor="openrouter", identifier="z-ai/glm-5.2:free", reasoning="high")
GEMMA = ModelConfig(vendor="google", identifier="gemma-4-31b-it", reasoning="high")
GLM_PAID = ModelConfig(vendor="openrouter", identifier="z-ai/glm-5.2", reasoning="high")
OTHER_FREE = ModelConfig(vendor="openrouter", identifier="minimax/minimax-m3:free")


def ledger(tmp_path: Path, *, phase_cap: int | None = 2, total: int = 400) -> BudgetLedger:
    """A ledger under a copy of the real authorisation, in a temporary file."""
    authorization = Authorization(
        authorization_id="narrowing-probe",
        max_requests=total,
        vendor="openrouter",
        model="z-ai/glm-5.2:free",
        active_phase=PHASE,
        phase_max_requests=phase_cap,
    )
    return BudgetLedger.for_authorization(authorization, directory=tmp_path / "budget")


def reserve(led: BudgetLedger, config: ModelConfig = GLM, phase: str = PHASE, key: str = "k"):
    return led.reserve(
        config=config,
        family=Family.DESCRIPTION,
        cache_key=f"sha256:{key}",
        execution_id="e",
        phase=phase,
        job_id="j",
    )


# =========================================================================
# 1-2. THE HISTORY IS INTACT AND STILL COUNTED
# =========================================================================


def test_the_real_ledger_still_loads_every_historical_event() -> None:
    """Narrowing the authorisation must not orphan the events it already holds.

    The three spent requests were made against a vendor and model this
    authorisation no longer permits. They are history: `_seed` counts events by
    id and sequence, never by route, so a narrowed authorisation reads its own
    past without objection -- and without forgiving it.
    """
    led = BudgetLedger.for_authorization(PROGRAM_AUTHORIZATION)
    if not led.path.exists():
        pytest.skip("no programme ledger in this working tree")

    events = led.events()
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert verify_ledger(led).ok

    reservations = [e for e in events if e["event"] == LedgerEvent.RESERVED]
    assert [e["phase"] for e in reservations] == ["B", "B-FC", "B-FC2", "GLM_RECHECK"]
    assert {e["provider"] for e in reservations} == {"google", "openrouter"}


def test_consumption_was_not_reset_by_the_narrowing() -> None:
    """The one number a second ledger would have quietly destroyed."""
    led = BudgetLedger.for_authorization(PROGRAM_AUTHORIZATION)
    if not led.path.exists():
        pytest.skip("no programme ledger in this working tree")

    assert led.consumed == 4
    assert led.remaining == 396
    assert led.consumed + led.remaining == 400
    assert led.spent_in(PHASE) == 0, "the final canary has not spent anything yet"
    assert led.spent_in(SPENT_PHASE) == 1, (
        "GLM_RECHECK spent its one request on an upstream 429 and is not refunded"
    )


def test_the_global_ceiling_is_still_four_hundred() -> None:
    assert PROGRAM_AUTHORIZATION.max_requests == 400
    assert PROGRAM_AUTHORIZATION.authorization_id == "m2-completion-gemma-v8-free-2026-09-04"


# =========================================================================
# 3-7. ONE ROUTE, ONE PHASE, AND NO WILDCARD
# =========================================================================


def test_the_free_slug_is_allowed_only_in_its_own_phase(tmp_path: Path) -> None:
    led = ledger(tmp_path)
    assert led.authorization.refusal_for(GLM, PHASE) is None

    for phase in (None, "B", "B-FC2", "SCREEN", SPENT_PHASE, "glm_final_canary"):
        refusal = led.authorization.refusal_for(GLM, phase)
        assert refusal is not None, f"phase {phase!r} was accepted"
        assert "narrowed to phase" in refusal

    with pytest.raises(AuthorizationViolation):
        reserve(led, phase="B")
    assert led.events() == [], "a refused phase writes nothing"


def test_gemma_is_refused_for_a_new_request(tmp_path: Path) -> None:
    """Three requests were spent there. That is history, not permission."""
    led = ledger(tmp_path)
    with pytest.raises(AuthorizationViolation) as raised:
        reserve(led, GEMMA)
    assert "provider fallback is disabled" in str(raised.value)
    assert led.consumed == 0
    assert not led.path.exists()


def test_the_paid_glm_slug_is_refused(tmp_path: Path) -> None:
    """`z-ai/glm-5.2` and `z-ai/glm-5.2:free` are different model ids, and one bills."""
    led = ledger(tmp_path)
    with pytest.raises(AuthorizationViolation) as raised:
        reserve(led, GLM_PAID)
    assert "model fallback is disabled" in str(raised.value)
    assert led.consumed == 0


def test_another_openrouter_free_model_is_refused(tmp_path: Path) -> None:
    """Free is not the permission. This exact slug is."""
    led = ledger(tmp_path)
    with pytest.raises(AuthorizationViolation):
        reserve(led, OTHER_FREE)
    assert led.consumed == 0


def test_the_authorisation_cannot_become_a_wildcard() -> None:
    """One vendor, one model, both plain strings. Nothing here accepts a list."""
    assert isinstance(PROGRAM_AUTHORIZATION.vendor, str)
    assert isinstance(PROGRAM_AUTHORIZATION.model, str)
    assert PROGRAM_AUTHORIZATION.provider_fallback is False
    assert PROGRAM_AUTHORIZATION.model_fallback is False
    assert PROGRAM_AUTHORIZATION.sdk_retries == 0
    assert PROGRAM_AUTHORIZATION.require_zero_cost is True

    for wildcard in ("*", "", "openrouter/*", "z-ai/*"):
        candidate = ModelConfig(vendor="openrouter", identifier=wildcard)
        assert PROGRAM_AUTHORIZATION.refusal_for(candidate, PHASE) is not None


def test_a_fallback_model_list_is_refused(tmp_path: Path) -> None:
    """A comma-joined identifier is a routing list wearing a model's clothes."""
    led = ledger(tmp_path)
    for identifier in (
        "z-ai/glm-5.2:free,z-ai/glm-5.2",
        "z-ai/glm-5.2:free, minimax/minimax-m3:free",
        "[z-ai/glm-5.2:free]",
    ):
        with pytest.raises(AuthorizationViolation):
            reserve(led, ModelConfig(vendor="openrouter", identifier=identifier))
    assert led.consumed == 0


# =========================================================================
# 8. ONE REQUEST, AND THE SECOND STOPS BEFORE THE TRANSPORT
# =========================================================================


def test_the_third_request_in_this_phase_is_refused(tmp_path: Path) -> None:
    """Two are authorised -- one description, one conditional provider. Not three."""
    led = ledger(tmp_path)
    for key in ("one", "two"):
        led.settle(reserve(led, key=key), LedgerEvent.SENT)
    assert led.spent_in(PHASE) == 2

    with pytest.raises(ProgramBudgetExhausted) as raised:
        reserve(led, key="three")
    assert "allows 2 request(s)" in str(raised.value)
    assert led.spent_in(PHASE) == 2, "the refusal wrote nothing"
    assert len(led.events()) == 4


def test_the_phase_cap_survives_a_restart(tmp_path: Path) -> None:
    """A cap counted in memory would be two requests per terminal."""
    first = ledger(tmp_path)
    for key in ("one", "two"):
        first.settle(reserve(first, key=key), LedgerEvent.SENT)

    with pytest.raises(ProgramBudgetExhausted):
        reserve(ledger(tmp_path), key="three")


def test_an_unsent_request_does_not_return_the_phases_one_try(tmp_path: Path) -> None:
    """The phase cap counts what was ASKED, not what was spent.

    `consumed` refunds a request that never reached the wire, which is right for
    a budget. It is wrong for "you may try this once": a run that reserved,
    failed locally and re-asked would have had two attempts at a one-attempt
    recheck.
    """
    led = ledger(tmp_path, phase_cap=1)
    led.settle(reserve(led, key="one"), LedgerEvent.NOT_SENT)

    assert led.consumed == 0, "the budget was refunded"
    assert led.spent_in(PHASE) == 1, "the phase's one try was not"
    with pytest.raises(ProgramBudgetExhausted):
        reserve(led, key="two")


# =========================================================================
# 9-10. THE GLOBAL CEILING AND CONCURRENCY ARE UNCHANGED
# =========================================================================


def test_the_global_ceiling_still_binds_under_the_narrowing(tmp_path: Path) -> None:
    """A phase cap is a second ceiling, never a way past the first."""
    led = ledger(tmp_path, phase_cap=None, total=2)
    led.settle(reserve(led, key="one"), LedgerEvent.SENT)
    led.settle(reserve(led, key="two"), LedgerEvent.SENT)

    with pytest.raises(ProgramBudgetExhausted) as raised:
        reserve(led, key="three")
    assert "allows 2 inference request(s) in total" in str(raised.value)


def test_the_phase_cap_is_enforced_inside_the_lock(tmp_path: Path) -> None:
    """Two processes, one authorised request. The same guarantee as the global cap.

    Real processes, a generous GLOBAL ceiling and a phase cap of one, so the
    only thing that can stop the losers is the phase cap itself -- read off the
    file inside the same exclusive lock as the global one.
    """
    from tests.unit.test_budget_ledger_concurrency import run_workers

    verdicts = run_workers(tmp_path, count=6, ceiling=100, phase_cap=2)
    reserved = [v for v in verdicts if v["outcome"] == "RESERVED"]
    assert len(reserved) == 2
    assert all(v["reached_transport"] is False for v in verdicts if v["outcome"] == "REFUSED")


# =========================================================================
# THE ENDPOINT THIS RECHECK IS PINNED TO
# =========================================================================


def test_the_pinned_endpoint_is_the_free_one_somebody_looked_at() -> None:
    endpoint = pinned_endpoint("openrouter", "z-ai/glm-5.2:free")
    assert endpoint is not None
    assert endpoint.provider_name == "Decart"
    assert endpoint.tag == "decart/fp4"
    assert endpoint.free
    assert (endpoint.input_price, endpoint.output_price) == (0.0, 0.0)
    assert endpoint.observed_at == "2026-09-04"
    assert "structured_outputs" in endpoint.supported_parameters
    assert "max_tokens" in endpoint.supported_parameters

    assert pinned_endpoint("openrouter", "z-ai/glm-5.2") is None, "the paid slug is not pinned"
    assert pinned_endpoint("google", "gemma-4-31b-it") is None, "a direct vendor needs no pin"


def test_a_cache_hit_creates_no_reservation(tmp_path: Path) -> None:
    """Replay is not inference, under a narrowed authorisation as under any other."""
    led = ledger(tmp_path)
    assert led.spent_in(PHASE) == 0
    assert not led.path.exists()
    assert json.dumps(led.events()) == "[]"


# =========================================================================
# THE FINAL CANARY'S OWN PREFLIGHT
# =========================================================================


def test_a_spent_phase_stays_spent(tmp_path: Path) -> None:
    """Moving the active phase is not a way to refund the previous one.

    GLM_RECHECK authorised one request and used it on an upstream 429. Two
    things keep it spent: the authorisation refuses the old phase outright, and
    the cap is counted from that phase's own events, so it would still read as
    spent even if the phase were reopened.
    """
    led = ledger(tmp_path)
    assert led.authorization.refusal_for(GLM, SPENT_PHASE) is not None
    with pytest.raises(AuthorizationViolation):
        reserve(led, phase=SPENT_PHASE)
    assert led.events() == []

    real = BudgetLedger.for_authorization(PROGRAM_AUTHORIZATION)
    if real.path.exists():
        assert real.spent_in(SPENT_PHASE) == 1
        assert real.spent_in(PHASE) == 0, "the new phase inherits nothing"


def test_the_phase_is_not_part_of_the_semantic_cache_key() -> None:
    """A question does not change because of which phase asked it.

    If the phase reached the key, a resumed run would miss every answer the
    previous phase paid for -- and the whole point of the cache is that a
    question already answered is not asked twice.
    """
    from career_agent.llm.requests import build_description_request

    built = build_description_request("sha256:PIN", "a posting", GLM)
    joined = "".join(built.cache_key.inputs)
    for phase in (PHASE, SPENT_PHASE, "B", "B-FC2"):
        assert phase not in joined
        assert phase not in built.cache_key.key


def test_no_stored_row_is_an_accepted_hit_for_this_arm() -> None:
    """The cache preflight, against the real database.

    The 429 returned no content, so there is nothing to reuse; and nothing from
    another model, provider, transport or disposition may stand in for it.
    """
    import sqlite3

    from career_agent.llm.requests import build_description_request, build_provider_request
    from career_agent.pipeline.extract import load_source
    from career_agent.storage.fingerprint_repo import LLMCallRepo

    corpus = Path(__file__).resolve().parents[2] / "data" / "m1d2" / "career.db"
    if not corpus.exists():
        pytest.skip("no corpus in this working tree")

    conn = sqlite3.connect(corpus)
    conn.row_factory = sqlite3.Row
    try:
        source = load_source(conn, "01M0XZHTCTFNA6BXNKRFRRZ9CZ")
        repo = LLMCallRepo(conn)

        description = build_description_request(source.content_hash, source.description_text, GLM)
        provider = build_provider_request(
            source.provider, source.observations, source.field_map_digest, GLM
        )
        for built in (description, provider):
            assert built is not None
            assert (
                repo.validated_answer(built.cache_key.key, model=GLM.arm, vendor="openrouter")
                is None
            ), "no accepted answer exists for this arm; the 429 returned no content"

        # And the 429 row itself is durable, and not admissible.
        row = conn.execute(
            "SELECT cache_disposition, length(raw_output) n, error FROM llm_call"
            " WHERE provider='openrouter' AND error LIKE '%429%' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row["cache_disposition"] != "ACCEPTED"
        assert row["n"] == 0, "an upstream refusal carries no content to reuse"

        # Nothing Gemma answered can stand in for a GLM question.
        gemma = conn.execute(
            "SELECT COUNT(*) FROM llm_call WHERE model LIKE 'gemma%' AND cache_key IN (?, ?)",
            (description.cache_key.key, provider.cache_key.key),
        ).fetchone()[0]
        assert gemma == 0
    finally:
        conn.close()
