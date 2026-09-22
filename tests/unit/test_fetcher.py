"""HTTP behaviour, especially the distinction that keeps closing safe.

    HTTP 200 with zero jobs  -> SUCCESS, the board is empty
    timeout / 5xx / garbage  -> FAILURE, we learned nothing

Everything here runs against httpx's MockTransport: no network, deterministic,
and sleeps are stubbed so retry tests are instant.
"""

from pathlib import Path

import httpx
import pytest

from career_agent.net.fetcher import (
    FetchError,
    FetchErrorCategory,
    FetchStats,
    HttpFetcher,
    ResponseCache,
)


def _fetcher(handler, **kwargs) -> HttpFetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    kwargs.setdefault("request_delay_seconds", 0.0)
    kwargs.setdefault("backoff_seconds", 0.0)
    return HttpFetcher(client=client, sleep=lambda _s: None, **kwargs)


URL = "https://boards-api.greenhouse.io/v1/boards/example/jobs"


# --- success ---------------------------------------------------------------


def test_successful_json_is_returned() -> None:
    with _fetcher(lambda _r: httpx.Response(200, json={"jobs": [{"id": 1}]})) as fetcher:
        assert fetcher.get_json(URL) == {"jobs": [{"id": 1}]}
        assert fetcher.stats.requests == 1
        assert fetcher.stats.retries == 0


def test_empty_board_is_a_success_not_a_failure() -> None:
    """The single most important behaviour in this module. If an empty board
    raised, a company that closed all its roles would look like an outage - and
    conversely, treating a failure as empty would close every job it has."""
    with _fetcher(lambda _r: httpx.Response(200, json={"jobs": []})) as fetcher:
        assert fetcher.get_json(URL) == {"jobs": []}
        assert fetcher.stats.failures == []


# --- failures --------------------------------------------------------------


def test_timeout_is_retried_then_reported_as_exhausted() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ReadTimeout("timed out", request=request)

    with _fetcher(handler, max_attempts=3) as fetcher, pytest.raises(FetchError) as exc:
        fetcher.get_json(URL)

    assert exc.value.category is FetchErrorCategory.RETRY_EXHAUSTED
    assert attempts["n"] == 3
    assert fetcher.stats.retries == 2
    assert fetcher.stats.retries_exhausted == 1


def test_transient_5xx_is_retried_and_can_succeed() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"jobs": []})

    with _fetcher(handler, max_attempts=3) as fetcher:
        assert fetcher.get_json(URL) == {"jobs": []}
    assert calls["n"] == 3
    assert fetcher.stats.retries == 2


def test_429_is_retried() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429) if calls["n"] == 1 else httpx.Response(200, json={"ok": True})

    with _fetcher(handler, max_attempts=3) as fetcher:
        assert fetcher.get_json(URL) == {"ok": True}
    assert calls["n"] == 2


def test_404_is_not_retried() -> None:
    """Retrying a correct answer wastes time and is impolite."""
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    with _fetcher(handler, max_attempts=3) as fetcher, pytest.raises(FetchError) as exc:
        fetcher.get_json(URL)

    assert exc.value.category is FetchErrorCategory.NOT_FOUND
    assert calls["n"] == 1, "404 must not be retried"


def test_unexpected_authentication_is_its_own_category() -> None:
    with _fetcher(lambda _r: httpx.Response(401)) as fetcher, pytest.raises(FetchError) as exc:
        fetcher.get_json(URL)
    assert exc.value.category is FetchErrorCategory.UNAUTHORISED


def test_malformed_json_is_a_failure_not_an_empty_board() -> None:
    """A 200 carrying garbage tells us nothing about the board's contents."""
    with (
        _fetcher(lambda _r: httpx.Response(200, text="<html>not json</html>")) as fetcher,
        pytest.raises(FetchError) as exc,
    ):
        fetcher.get_json(URL)
    assert exc.value.category is FetchErrorCategory.MALFORMED


def test_connection_error_is_categorised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    with _fetcher(handler, max_attempts=2) as fetcher, pytest.raises(FetchError) as exc:
        fetcher.get_json(URL)
    assert exc.value.category is FetchErrorCategory.RETRY_EXHAUSTED
    assert "no route to host" in exc.value.message


# --- failure reporting -----------------------------------------------------


def test_failures_are_recorded_with_diagnostics_and_no_secrets() -> None:
    with _fetcher(lambda _r: httpx.Response(404)) as fetcher, pytest.raises(FetchError):
        fetcher.get_json(URL)

    assert len(fetcher.stats.failures) == 1
    record = fetcher.stats.failures[0]
    assert record["category"] == "NOT_FOUND"
    assert record["url"] == URL
    assert record["attempts"] == 1
    assert record["occurred_at"].endswith("Z")
    assert "Authorization" not in str(record)


def test_stats_are_reportable() -> None:
    with _fetcher(lambda _r: httpx.Response(200, json={})) as fetcher:
        fetcher.get_json(URL)
        summary = fetcher.stats.as_dict()
    assert summary["requests"] == 1
    assert set(summary) >= {
        "requests",
        "retries",
        "retries_exhausted",
        "cache_hits",
        "cache_misses",
    }


# --- cache -----------------------------------------------------------------


def test_cache_hit_avoids_a_second_request(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"jobs": [{"id": 7}]})

    cache = ResponseCache(tmp_path / "http", ttl_hours=12)
    with _fetcher(handler, cache=cache, stats=FetchStats()) as fetcher:
        first = fetcher.get_json(URL)
        second = fetcher.get_json(URL)

    assert first == second
    assert calls["n"] == 1
    assert fetcher.stats.cache_misses == 1
    assert fetcher.stats.cache_hits == 1


def test_expired_cache_entry_is_refetched(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "http", ttl_hours=0)
    cache.put(URL, {"jobs": []}, clock=lambda: 0.0)
    assert cache.get(URL, clock=lambda: 10_000.0) is None


def test_corrupt_cache_entry_is_treated_as_a_miss(tmp_path: Path) -> None:
    """The cache is an optimisation; it must never be able to break a run."""
    cache = ResponseCache(tmp_path / "http")
    path = cache._path(URL)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert cache.get(URL) is None


def test_no_cache_bypasses_both_read_and_write(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"n": calls["n"]})

    cache = ResponseCache(tmp_path / "http")
    with _fetcher(handler, cache=cache) as fetcher:
        fetcher.get_json(URL, use_cache=False)
        fetcher.get_json(URL, use_cache=False)
    assert calls["n"] == 2


# --- politeness ------------------------------------------------------------


def test_user_agent_identifies_the_tool_honestly() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("User-Agent", "")
        return httpx.Response(200, json={})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": "career-agent/0.1.0 (personal job-search tool)"},
    )
    with HttpFetcher(client=client, request_delay_seconds=0.0, sleep=lambda _s: None) as fetcher:
        fetcher.get_json(URL)

    assert "career-agent" in seen["ua"]


def test_rate_limiter_spaces_requests_to_the_same_host() -> None:
    slept: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={})))
    # Reads: first wait takes 0.0; second wait takes 0.2 (so 0.8s still owed),
    # then reads again after sleeping to record the new "last request" time.
    clock = iter([0.0, 0.2, 1.0])

    with HttpFetcher(
        client=client, request_delay_seconds=1.0, sleep=slept.append, backoff_seconds=0.0
    ) as fetcher:
        first_wait = fetcher._limiter.wait(
            "example.com", sleep=slept.append, clock=lambda: next(clock)
        )
        second_wait = fetcher._limiter.wait(
            "example.com", sleep=slept.append, clock=lambda: next(clock)
        )

    assert first_wait == 0.0, "the first request to a host waits for nothing"
    assert second_wait == pytest.approx(0.8, abs=0.01)
    assert slept == [pytest.approx(0.8, abs=0.01)]


def test_rate_limiter_does_not_delay_a_different_host() -> None:
    """Politeness is per host: one slow board must not stall the others."""
    client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={})))
    with HttpFetcher(client=client, request_delay_seconds=1.0, sleep=lambda _s: None) as fetcher:
        assert fetcher._limiter.wait("a.example.com", sleep=lambda _s: None) == 0.0
        assert fetcher._limiter.wait("b.example.com", sleep=lambda _s: None) == 0.0
