# ruff: noqa: F811
"""The deep-reading panel tells its states apart, and counts the list it reads.

A simulated provider and a synthetic corpus: no call is made and no key is read.

* While a recalculation is outstanding, Find jobs serves the PREVIOUS match
  revision. The deep reading reads that same list (it used to count only the
  revision in force and saw zero roles under a list of thousands).
* A coverage count that failed is not a count of zero.
* Nothing to read cannot start a reading, and says why; a number above what is
  available is refused with the real availability; a valid smaller number goes
  through untouched.
* A provider that refuses the key is told apart from a provider that failed
  after a call, and it stops at once.
* Another profile does not see this one's counts.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import pytest
from tests.integration.test_deep_api_provider import api, call  # noqa: F401 - fixture
from tests.integration.test_local_profiles import app as app_of
from tests.integration.test_local_profiles import install  # noqa: F401 - fixture

from career_agent.semantic.providers import (
    Availability,
    Billing,
    Capabilities,
    ProviderAnswer,
    ProviderFailed,
    ProviderStatus,
)
from career_agent.storage.db import connect
from career_agent.web.server import ApiError


def choose(api) -> None:
    call(api, "POST", "/api/deep/provider", {"provider": "deepseek"})


def scores_older(api) -> None:
    """Every stored score now answers a PREVIOUS configuration version."""
    conn = connect(api.config.db_path)
    try:
        conn.execute("UPDATE job_match SET config_version = config_version - 1")
    finally:
        conn.close()


def test_the_panel_counts_the_list_that_is_on_screen_not_only_the_revision_in_force(api) -> None:
    choose(api)
    current = call(api, "GET", "/api/deep")
    assert current["scores"]["state"] == "current" and current["readable"] > 0
    ready = current["available"]
    scores_older(api)
    older = call(api, "GET", "/api/deep")
    assert older["scores"]["state"] == "older"
    assert older["scores"]["serving_version"] == older["scores"]["current_version"] - 1
    assert older["readable"] == current["readable"] and older["available"] == ready
    # And what a preview would read is what the list shows.
    preview = call(api, "POST", "/api/deep/preview", {"limit": 3, "budget_usd": 0.5})
    assert preview["scores"]["state"] == "older" and len(preview["postings"]) == 3


def test_a_failed_coverage_count_is_a_failure_and_never_a_zero(api, monkeypatch) -> None:
    choose(api)

    def broken(*_args, **_kwargs):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr("career_agent.semantic.deep_run.coverage", broken)
    status = call(api, "GET", "/api/deep")
    assert status["coverage_ok"] is False
    for key in ("main_list_roles", "readable", "too_short"):
        assert status[key] is None, "not a zero: nothing was counted"
    assert "available" not in status and status["unavailable"] is None


def test_nothing_to_read_cannot_start_a_reading_and_says_why(api) -> None:
    choose(api)
    conn = connect(api.config.db_path)
    try:
        conn.execute("DELETE FROM job_match")
    finally:
        conn.close()
    status = call(api, "GET", "/api/deep")
    assert status["coverage_ok"] is True and status["available"] == 0
    assert status["scores"]["state"] == "none" and status["unavailable"] == "NO_SCORES"
    for path, body in (
        ("/api/deep/preview", {"limit": 3, "budget_usd": 0.5}),
        ("/api/deep/run", {"limit": 3, "budget_usd": 0.5, "confirm": True}),
    ):
        with pytest.raises(ApiError) as refused:
            call(api, "POST", path, body)
        assert refused.value.status == 409 and refused.value.code in {
            "NO_POSTINGS",
            "PROVIDER_NOT_READY",
        }


def test_availability_follows_the_switch_and_a_stale_number_is_refused_with_the_truth(api) -> None:
    choose(api)
    status = call(api, "GET", "/api/deep")
    available = status["available"]
    assert status["available_unresolved"] >= available > 0
    # A number chosen earlier, above what is available now: refused, with the real figure.
    with pytest.raises(ApiError) as refused:
        call(api, "POST", "/api/deep/preview", {"limit": available + 50, "budget_usd": 0.5})
    assert refused.value.status == 400 and refused.value.code == "LIMIT_OUT_OF_RANGE"
    assert refused.value.data == {"available": available}
    # A valid smaller number is kept as it was chosen.
    kept = call(api, "POST", "/api/deep/preview", {"limit": 5, "budget_usd": 0.5})
    assert kept["requested"] == 5 and len(kept["postings"]) == 5


def test_a_provider_that_is_not_ready_is_refused_before_anything_is_sent(api) -> None:
    choose(api)
    with pytest.raises(ApiError) as refused:
        call(api, "POST", "/api/deep/run", {"limit": 3, "budget_usd": 0.5, "confirm": True})
    assert refused.value.status == 409 and refused.value.code == "PROVIDER_NOT_READY"
    assert refused.value.data == {"state": "KEY_MISSING"}


@dataclass
class RefusesTheKey:
    id: str = "refuses"
    display_name: str = "Refuses"
    model: str = "m"
    calls: list[str] = field(default_factory=list)

    def availability(self) -> ProviderStatus:
        return ProviderStatus(Availability.AVAILABLE)

    def capabilities(self) -> Capabilities:
        return Capabilities(billing=Billing.SUBSCRIPTION, quotes=True, max_concurrency=1, sends="t")

    def complete(self, system: str, user: str, schema: dict) -> ProviderAnswer:
        self.calls.append(user)
        raise ProviderFailed("HTTP 401", state=Availability.KEY_MISSING)


def test_a_refused_key_stops_the_run_at_once_and_is_not_a_generic_failure(api) -> None:
    from career_agent.semantic.deep_run import run_deep

    conn = connect(api.config.db_path)
    try:
        provider = RefusesTheKey()
        stats = run_deep(conn, api.search_config(), provider, share=False, limit=5)  # type: ignore[arg-type]
    finally:
        conn.close()
    assert stats.stop_reason == "AUTH_FAILED"
    assert len(provider.calls) == 1, "one refused request, then nothing more is asked"
    assert stats.failed == 0 and stats.published == 0


def test_another_profile_does_not_see_this_profiles_counts(install) -> None:  # noqa: F811
    host = install
    first = app_of(host)
    created = call(first, "POST", "/api/profiles", {"label": "Other"})["created"]["id"]
    call(first, "POST", "/api/deep/provider", {"provider": "claude_code"})
    mine = call(first, "GET", "/api/deep")
    assert mine["profile"] and mine["main_list_roles"] is not None
    call(first, "POST", "/api/profiles/switch", {"profile_id": created})
    second = app_of(host)
    assert second is not first
    theirs = call(second, "GET", "/api/deep")
    assert theirs["profile"] != mine["profile"]
    assert theirs["provider"] is None, "the choice of provider belongs to the first profile"
    assert theirs["main_list_roles"] in (0, None) or theirs["scores"]["state"] in {
        "none",
        "current",
    }
