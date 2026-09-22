"""Shared HTTP client: retries, politeness, caching, and typed failures.

One distinction shapes this whole module:

    HTTP 200 carrying zero jobs   -> SUCCESS. The board is genuinely empty.
    timeout / 5xx / malformed     -> FAILURE. We learned nothing about it.

Collapsing those two would let a momentary network problem look like "every job
at this company disappeared", and the closing logic would act on it. So a
failure is always raised as a typed `FetchError` and never returned as an empty
result, and every failure is counted rather than skipped.

Retries are the other half of that promise. Over a few hundred boards some
requests will fail for boring reasons; without retries a single blip means one
company silently missing from today's corpus, which is precisely the
recall-first principle being violated by accident.
"""

import contextlib
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from career_agent import __version__
from career_agent.clock import now_utc
from career_agent.net.deadline import Deadline

#: What `get_text` asks for. XML and HTML first, because those are the bodies
#: this method exists to read, and a trailing `*/*` so a server that types its
#: feed as something else still answers rather than refusing.
TEXT_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5"

USER_AGENT = (
    f"career-agent/{__version__} (personal job-search tool; "
    "reads public ATS job boards; contact via repository)"
)

CONNECT_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 1.0
DEFAULT_REQUEST_DELAY_SECONDS = 1.0
DEFAULT_CACHE_HOURS = 12

#: Statuses worth trying again. 429 and 5xx are transient by definition.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class FetchErrorCategory(StrEnum):
    """Why a request failed, kept distinct so closing safety can reason about it."""

    TIMEOUT = "TIMEOUT"
    CONNECTION = "CONNECTION"
    HTTP_STATUS = "HTTP_STATUS"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORISED = "UNAUTHORISED"
    MALFORMED = "MALFORMED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"


class FetchError(Exception):
    """A request failed. Carries enough detail to diagnose, and no secrets."""

    def __init__(
        self,
        category: FetchErrorCategory,
        url: str,
        message: str,
        attempts: int = 1,
        status_code: int | None = None,
    ) -> None:
        super().__init__(f"{category.value}: {message}")
        self.category = category
        self.url = url
        self.message = message
        self.attempts = attempts
        self.status_code = status_code
        self.occurred_at = now_utc()

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "url": self.url,
            "message": self.message[:500],
            "attempts": self.attempts,
            "status_code": self.status_code,
            "occurred_at": self.occurred_at,
        }


@dataclass
class FetchStats:
    """Counted, not logged and forgotten. Reported at the end of every run."""

    requests: int = 0
    retries: int = 0
    retries_exhausted: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "retries": self.retries,
            "retries_exhausted": self.retries_exhausted,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "failure_count": len(self.failures),
        }


class _RateLimiter:
    """At most one request per host per `delay` seconds.

    Politeness, not throttling: these are public endpoints that cost the vendor
    money to serve, and we are a single-user tool with no deadline.
    """

    def __init__(self, delay_seconds: float) -> None:
        self.delay = delay_seconds
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str, sleep=time.sleep, clock=time.monotonic) -> float:
        with self._lock:
            previous = self._last.get(host)
            now = clock()
            waited = 0.0
            if previous is not None:
                remaining = self.delay - (now - previous)
                if remaining > 0:
                    sleep(remaining)
                    waited = remaining
                    now = clock()
            self._last[host] = now
            return waited


class ResponseCache:
    """On-disk cache of successful JSON responses.

    Re-running collection an hour later should not re-hit every board. The key
    is a hash of the URL; the payload is stored alongside the time it was
    fetched so expiry is a plain string comparison.
    """

    def __init__(self, directory: Path, ttl_hours: int = DEFAULT_CACHE_HOURS) -> None:
        self.directory = directory
        self.ttl_seconds = ttl_hours * 3600

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.directory / digest[:2] / f"{digest}.json"

    def get(self, url: str, clock=time.time) -> Any | None:
        path = self._path(url)
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if clock() - float(envelope["fetched_at_epoch"]) > self.ttl_seconds:
                return None
            return envelope["body"]
        except (OSError, ValueError, KeyError):
            # A corrupt cache entry is a cache miss, never an error: the cache
            # is an optimisation and must not be able to break a run.
            return None

    def put(self, url: str, body: Any, clock=time.time) -> None:
        path = self._path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {"url": url, "fetched_at": now_utc(), "fetched_at_epoch": clock(), "body": body}
        # Best-effort: a cache write that fails must never break a run.
        with contextlib.suppress(OSError):
            path.write_text(json.dumps(envelope), encoding="utf-8")


class HttpFetcher:
    """Fetch JSON with retry, rate limiting and caching.

    Retries are deliberately hand-rolled rather than delegated to a decorator:
    the retry decision depends on *which* failure occurred, the attempt count
    has to reach the caller for reporting, and 404 must never be retried. That
    logic is clearer written out than configured.
    """

    def __init__(
        self,
        cache: ResponseCache | None = None,
        stats: FetchStats | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        request_delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
        client: httpx.Client | None = None,
        sleep=time.sleep,
        deadline: Deadline | None = None,
    ) -> None:
        self.cache = cache
        self.stats = stats or FetchStats()
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self._limiter = _RateLimiter(request_delay_seconds)
        self.deadline = deadline
        self._base_sleep = sleep
        self._sleep = self._budget_sleep
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def check_budget(self) -> None:
        if self.deadline is not None:
            self.deadline.check()

    def _budget_sleep(self, seconds: float) -> None:
        if self.deadline is None:
            self._base_sleep(seconds)
        else:
            self.deadline.sleep(seconds, self._base_sleep)

    def __enter__(self) -> "HttpFetcher":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def download(self, url: str, destination: Path) -> int:
        """Stream a public feed to disk; a truncated response is never success.

        One bounded attempt. The caller can retry later; gigabyte feeds must
        not be silently downloaded three times or buffered in memory.
        """
        self._limiter.wait(httpx.URL(url).host, sleep=self._sleep)
        self.stats.requests += 1
        size = 0
        try:
            with self._client.stream("GET", url, headers={"Accept": TEXT_ACCEPT}) as response:
                if response.status_code >= 400:
                    raise self._record(
                        FetchError(
                            FetchErrorCategory.HTTP_STATUS,
                            url,
                            f"HTTP {response.status_code}",
                            status_code=response.status_code,
                        )
                    )
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        output.write(chunk)
                        size += len(chunk)
            return size
        except httpx.HTTPError as exc:
            raise self._record(
                FetchError(
                    FetchErrorCategory.CONNECTION,
                    url,
                    f"feed transfer interrupted after {size} bytes ({type(exc).__name__})",
                )
            ) from exc

    def get_json(
        self,
        url: str,
        use_cache: bool = True,
        *,
        safe_url: str | None = None,
        cache_key: str | None = None,
    ) -> Any:
        """Return parsed JSON, or raise FetchError. Never returns a sentinel.

        `safe_url` and `cache_key` exist for a vendor that puts a key in the
        query string (Comeet's Careers API takes `token=`): the real URL is
        requested, the safe one is what an error and the cache envelope
        record, and the cache key is what the on-disk file is named after.
        Both default to the URL itself, which is right for every source that
        carries no key.
        """
        key = cache_key or url
        if use_cache and self.cache is not None:
            cached = self.cache.get(key)
            if cached is not None:
                self.stats.cache_hits += 1
                return cached
            self.stats.cache_misses += 1

        body = self._request_json(url, None, safe_url or url)

        if use_cache and self.cache is not None:
            self.cache.put(key, body)
        return body

    def get_text(self, url: str, use_cache: bool = True) -> str:
        """Return the response body as text, or raise FetchError.

        The same retry, rate limiting and cache machinery as `get_json`, for
        the sources that publish XML. An RSS feed is a documented interface and
        deserves the same politeness as a JSON one; what it does not deserve is
        a second HTTP stack with its own timeouts and its own idea of what a
        transient failure is.

        The cache stores the text under the same key shape, wrapped so the
        on-disk format stays JSON and one cache serves both kinds of body.
        """
        if use_cache and self.cache is not None:
            cached = self.cache.get(url)
            if isinstance(cached, dict) and "text" in cached:
                self.stats.cache_hits += 1
                return str(cached["text"])
            self.stats.cache_misses += 1

        body = self._request_text(url)

        if use_cache and self.cache is not None:
            self.cache.put(url, {"text": body})
        return body

    def post_json(
        self,
        url: str,
        body: dict[str, Any],
        *,
        safe_url: str,
        cache_key: str,
        use_cache: bool = True,
    ) -> Any:
        """POST a JSON body and return parsed JSON, or raise FetchError.

        The same retry, rate-limiting and cache machinery as `get_json`, and
        deliberately in this class rather than in an adapter: a second HTTP
        stack would mean a second set of timeouts, a second backoff policy and
        a second definition of what a transient failure is.

        `safe_url` and `cache_key` are required rather than derived, and the
        reason is a credential. Some vendors put the API key in the request
        PATH -- Jooble's contract is `POST /api/{key}` -- so the real URL is a
        secret, and a URL is exactly the value this class writes into every
        `FetchError`, every log line a caller prints from one, and the name of
        every cache file. Making the caller hand over a redacted URL and an
        explicit key is what stops a credential reaching a traceback by
        accident. Nothing here ever reads `url` except to send it.

        The cache key is separate from the URL for a second reason: a POST's
        identity is the endpoint AND the body, and two searches differing only
        in their page number must not collide.
        """
        if use_cache and self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.stats.cache_hits += 1
                return cached
            self.stats.cache_misses += 1

        parsed = self._request_json(url, body, safe_url)

        if use_cache and self.cache is not None:
            self.cache.put(cache_key, parsed)
        return parsed

    def _request_text(self, url: str) -> str:
        """One request, retried, returning the raw body.

        Shares `_request_json`'s policy by construction: it calls it with a
        marker that says "do not parse", so there is exactly one place that
        decides what a 404 means, which statuses retry, and how long to wait.

        It does NOT share the Accept header, and that is a correction rather
        than an exception. The client is built with `Accept: application/json`
        because almost everything here is JSON, and that header rode on this
        method too -- so a method whose entire purpose is bodies that are not
        JSON was telling every server it would accept nothing else. Most
        ignored it. Programathor answered **406 Not Acceptable**, correctly,
        and a whole source read as unreachable.
        """
        return str(self._request_json(url, None, url, parse_json=False, accept=TEXT_ACCEPT))

    def _request_json(
        self,
        url: str,
        body: dict[str, Any] | None,
        safe_url: str,
        *,
        parse_json: bool = True,
        accept: str | None = None,
    ) -> Any:
        """One request, retried. `safe_url` is the only URL that is ever recorded.

        `accept` overrides the client's default for one request. It exists for
        `_request_text` and defaults to None, so every JSON caller is unchanged.
        """
        host = httpx.URL(url).host
        last_error: FetchError | None = None

        for attempt in range(1, self.max_attempts + 1):
            self.check_budget()
            if attempt > 1:
                self.stats.retries += 1
                # Exponential backoff. No jitter is needed here: this is one
                # sequential client, not a thundering herd.
                self._sleep(self.backoff_seconds * (2 ** (attempt - 2)))

            self._limiter.wait(host, sleep=self._sleep)
            self.check_budget()
            self.stats.requests += 1

            try:
                headers = {"Accept": accept} if accept else None
                response = (
                    self._client.get(url, headers=headers)
                    if body is None
                    else self._client.post(url, json=body, headers=headers)
                )
            except httpx.TimeoutException as exc:
                last_error = FetchError(FetchErrorCategory.TIMEOUT, safe_url, str(exc), attempt)
                continue
            except httpx.HTTPError as exc:
                last_error = FetchError(FetchErrorCategory.CONNECTION, safe_url, str(exc), attempt)
                continue

            # Terminal statuses: retrying a correct answer is pointless and rude.
            if response.status_code == 404:
                raise self._record(
                    FetchError(
                        FetchErrorCategory.NOT_FOUND,
                        safe_url,
                        "board or resource not found",
                        attempt,
                        404,
                    )
                )
            if response.status_code in (401, 403):
                raise self._record(
                    FetchError(
                        FetchErrorCategory.UNAUTHORISED,
                        safe_url,
                        "authentication unexpectedly required",
                        attempt,
                        response.status_code,
                    )
                )

            if response.status_code in RETRYABLE_STATUS:
                last_error = FetchError(
                    FetchErrorCategory.HTTP_STATUS,
                    safe_url,
                    f"transient HTTP {response.status_code}",
                    attempt,
                    response.status_code,
                )
                continue

            if response.status_code >= 400:
                raise self._record(
                    FetchError(
                        FetchErrorCategory.HTTP_STATUS,
                        safe_url,
                        f"HTTP {response.status_code}",
                        attempt,
                        response.status_code,
                    )
                )

            if not parse_json:
                return response.text

            try:
                return response.json()
            except ValueError as exc:
                # A 200 carrying unparseable content is a failure, not an empty
                # board. This is the case that most needs to stay distinct.
                raise self._record(
                    FetchError(
                        FetchErrorCategory.MALFORMED,
                        safe_url,
                        f"response was not valid JSON: {exc}",
                        attempt,
                        response.status_code,
                    )
                ) from exc

        self.stats.retries_exhausted += 1
        assert last_error is not None
        raise self._record(
            FetchError(
                FetchErrorCategory.RETRY_EXHAUSTED,
                safe_url,
                f"gave up after {self.max_attempts} attempts; last failure: {last_error.message}",
                self.max_attempts,
                last_error.status_code,
            )
        )

    def _record(self, error: FetchError) -> FetchError:
        self.stats.failures.append(error.as_dict())
        return error
