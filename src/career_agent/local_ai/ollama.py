"""The Ollama adapter, and the one address it is allowed to speak to.

WHY NOT `net.fetcher.HttpFetcher`
---------------------------------
`HttpFetcher` is the right client for its job and the wrong one for this. It is
GET-only; it retries three times with exponential backoff; it rate-limits one
request per host per second; it caches responses on disk keyed by URL; and it
sends a public-facing User-Agent identifying us to a stranger's ATS.

Every one of those is wrong here. This is a POST. A retry of a five-minute local
generation is a ten-minute stall, and the failure was almost certainly "the
model is not loaded", which retrying cannot fix. Politeness delay is meaningless
against your own GPU. The response cache would be keyed on a URL that never
varies, while the thing that actually identifies an answer is the prompt bytes
-- which is `cache.py`'s job. And there is no stranger to introduce ourselves to.

So this uses `httpx` directly, in about thirty lines, and matches `FetchError`'s
style instead of its implementation: typed exceptions, no sentinels, and a
failure is always raised rather than returned as an empty result.

`httpx` is imported INSIDE the request function, not at module scope, so
importing this module reaches no network stack at all -- a property a test
asserts by making `httpx.Client` explode on construction.

LOOPBACK IS THE SECURITY BOUNDARY
---------------------------------
`assert_loopback` is what makes "local inference" a fact rather than an
intention. A misconfigured `OLLAMA_BASE_URL` -- typo, stale export, a value
picked up from somewhere else -- would otherwise ship a candidate profile and a
full job description to a third party, quietly, with no credential involved and
therefore no vendor to notice.

It refuses without resolving DNS. Looking up an attacker-supplied hostname is
itself an outbound request that leaks the query, so a non-numeric host is
rejected on sight; only the literal `localhost` and hosts that parse as loopback
IP addresses are permitted. It runs in the constructor AND again immediately
before every request, because a dataclass being frozen protects the field, not
the client object someone reached into.

NOTHING FROM THE ENVIRONMENT APPEARS IN AN EXCEPTION
----------------------------------------------------
Exception messages here are written from constants only. Configured values reach
a caller as attributes on the exception (`OllamaRefused.host`), so a human
debugging can print them deliberately, and a traceback pasted into a chat window
or captured by a crash reporter cannot carry them by accident.
"""

import ipaddress
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from career_agent.local_ai.contract import LocalEnrichment

#: `(method, url, json_body, timeout_seconds) -> (status_code, body)`.
#: Injected so every test runs offline against a fake. There is no default
#: instance; the default is a *function*, constructed per call.
Transport = Callable[[str, str, "dict[str, Any] | None", float], "tuple[int, dict[str, Any]]"]

ENV_BASE_URL = "OLLAMA_BASE_URL"
ENV_MODEL = "OLLAMA_MODEL"
ENV_TIMEOUT_MS = "OLLAMA_TIMEOUT_MS"

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:4b"

#: Five minutes. A 4B model on CPU is slow, not broken, and a timeout that fires
#: mid-generation looks exactly like a crash while wasting the whole run.
DEFAULT_TIMEOUT_MS = 300_000


class OllamaUnavailable(RuntimeError):
    """The endpoint could not be reached, or did not answer with a usable status.

    This is the recoverable one. A caller catches it and continues without
    enrichment; nothing about the run's correctness depends on a local model
    being up.
    """


class OllamaRefused(RuntimeError):
    """We refused, or the model refused. A decision, never a malfunction.

    Two causes, deliberately sharing a type because both mean "no answer, and
    that is the correct outcome": a base URL that is not loopback, and a model
    declining to answer. Neither should be retried.
    """

    def __init__(self, message: str, host: str | None = None) -> None:
        super().__init__(message)
        #: The offending host, carried as data rather than interpolated into
        #: the message, so a traceback never contains a configured value.
        self.host = host


class OllamaInvalidOutput(RuntimeError):
    """The endpoint answered, and the answer was not a valid enrichment."""


def assert_loopback(base_url: str) -> str:
    """Return the host if `base_url` is loopback; raise `OllamaRefused` if not.

    Permitted: scheme http or https, no embedded credential, and a host that is
    either the literal string `localhost` or parses as a loopback IP address
    (`127.0.0.0/8`, `::1`, and their bracketed form).

    **No DNS resolution happens.** Resolving `evil.example.com` to check whether
    it points at 127.0.0.1 would send the hostname to a resolver -- an egress,
    performed while enforcing a rule against egress -- and a name that resolves
    to loopback today can resolve elsewhere on the next request anyway.
    """
    parts = urlsplit(base_url)

    if parts.scheme not in ("http", "https"):
        raise OllamaRefused(
            "refusing a non-HTTP Ollama base URL. Only http:// and https:// to a "
            "loopback address are permitted."
        )

    if parts.username or parts.password:
        # A credential in a local URL is either a mistake or an attempt to point
        # us at something that is not Ollama. Neither is worth supporting, and
        # the value must not be echoed anywhere.
        raise OllamaRefused(
            "refusing an Ollama base URL containing an embedded credential. Local "
            "inference needs no credential, so a URL carrying one is not local."
        )

    host = parts.hostname
    if not host:
        raise OllamaRefused("refusing an Ollama base URL with no host.")

    if host == "localhost":
        return host

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Not a literal address, and not the one name we accept without asking
        # anybody. `127.0.0.1.evil.com` lands here, which is the point.
        raise OllamaRefused(
            "refusing a non-loopback Ollama host. Only 'localhost', 127.0.0.0/8 "
            "and ::1 are permitted, and a name is never resolved to find out. "
            "The offending host is on the exception's .host attribute.",
            host=host,
        ) from None

    if not address.is_loopback:
        raise OllamaRefused(
            "refusing a non-loopback Ollama address. Only 127.0.0.0/8 and ::1 are "
            "permitted. The offending host is on the exception's .host attribute.",
            host=host,
        )
    return host


@dataclass(frozen=True)
class OllamaSettings:
    """How to talk to the local model. Frozen, and free of credentials.

    `think` and `stream` are fields rather than call arguments because they
    change what the answer *is*, not merely how it arrives, and both ride in the
    settings digest that forms part of the cache key.
    """

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    think: bool = False
    stream: bool = False
    temperature: float = 0.0
    seed: int = 0
    #: Not in the environment on purpose. Context size is a property of the
    #: prompt we send, not of the machine, and a caller changing it gets a
    #: different cache key rather than a different answer under the same one.
    num_ctx: int = 8192

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "OllamaSettings":
        """Read the three variables that exist, and no others.

        There is deliberately no `OLLAMA_API_KEY`. Loopback needs no
        authentication, and a variable by that name in the environment is
        either left over from a hosted provider or an attempt to make this
        client authenticate to something that is not local. Either way it is
        read by nothing here and changes nothing about the request.
        """
        raw_timeout = env.get(ENV_TIMEOUT_MS)
        if raw_timeout is None or not raw_timeout.strip():
            timeout_ms = DEFAULT_TIMEOUT_MS
        else:
            try:
                timeout_ms = int(raw_timeout)
            except ValueError:
                raise ValueError(
                    f"{ENV_TIMEOUT_MS} must be a whole number of milliseconds."
                ) from None
            if timeout_ms <= 0:
                raise ValueError(f"{ENV_TIMEOUT_MS} must be greater than zero.")

        return cls(
            base_url=(env.get(ENV_BASE_URL) or DEFAULT_BASE_URL).strip().rstrip("/"),
            model=(env.get(ENV_MODEL) or DEFAULT_MODEL).strip(),
            timeout_ms=timeout_ms,
        )

    @property
    def timeout_seconds(self) -> float:
        return self.timeout_ms / 1000.0

    def digest_material(self) -> dict[str, Any]:
        """The generation settings that change the answer, for the cache key.

        `base_url` and `timeout_ms` are absent: where the model runs and how
        long we are willing to wait do not alter what it says. Everything that
        does is here.
        """
        return {
            "model": self.model,
            "think": self.think,
            "stream": self.stream,
            "temperature": self.temperature,
            "seed": self.seed,
            "num_ctx": self.num_ctx,
        }

    def with_model(self, model: str) -> "OllamaSettings":
        return replace(self, model=model)


def _httpx_transport(
    method: str,
    url: str,
    json_body: dict[str, Any] | None,
    timeout: float,
) -> tuple[int, dict[str, Any]]:
    """The real transport. `httpx` is imported here, never at module scope.

    No custom headers: no User-Agent to introduce us to a stranger, and above
    all no Authorization header, because there is nothing to authenticate to.
    """
    import httpx

    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.request(method, url, json=json_body)
    except httpx.HTTPError as exc:
        raise OllamaUnavailable(
            "could not reach the local Ollama endpoint. Is `ollama serve` running?"
        ) from exc

    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


class OllamaClient:
    """A minimal client for the two Ollama endpoints this project uses.

    Holds no session and no connection pool: a handful of local requests per run
    does not justify either, and a stateless client is trivially fake-able.
    """

    def __init__(self, settings: OllamaSettings, transport: Transport | None = None) -> None:
        # Refuse at construction so a misconfigured URL fails where the mistake
        # was made, not later inside whatever happened to call enrich().
        assert_loopback(settings.base_url)
        self.settings = settings
        self._transport: Transport = transport or _httpx_transport

    def _url(self, path: str) -> str:
        # Re-checked immediately before every request. Frozen settings protect
        # the dataclass field; they do not stop anyone rebinding `self.settings`.
        assert_loopback(self.settings.base_url)
        return self.settings.base_url.rstrip("/") + path

    def _call(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = self._url(path)
        try:
            return self._transport(method, url, json_body, self.settings.timeout_seconds)
        except (OllamaUnavailable, OllamaRefused, OllamaInvalidOutput):
            raise
        except Exception as exc:
            # Connection refused, timeout, DNS, a transport bug: from here they
            # are one thing -- we learned nothing about the local model. The
            # message carries no configured value; the cause carries the detail.
            raise OllamaUnavailable(
                "the local Ollama endpoint did not answer. It is unreachable, timed "
                "out, or is not running. Enrichment is optional; the run continues."
            ) from exc

    def health(self) -> list[str]:
        """Model names the local server currently has. Raises if it cannot say."""
        status, body = self._call("GET", "/api/tags")
        if status != 200:
            raise OllamaUnavailable(
                "the local Ollama endpoint answered with a non-200 status for "
                "/api/tags, so its model list is unknown."
            )
        models = body.get("models")
        if not isinstance(models, list):
            raise OllamaUnavailable(
                "the local Ollama endpoint answered /api/tags without a model list."
            )
        return [str(entry.get("name", "")) for entry in models if isinstance(entry, dict)]

    def is_model_available(self) -> bool:
        """Whether the configured model is present, `:latest` or not.

        Ollama reports an untagged pull as `name:latest` while users write
        `name`. Treating those as different models would make a correct setup
        look broken, so both spellings are folded to one.
        """
        wanted = _strip_latest(self.settings.model)
        return any(_strip_latest(name) == wanted for name in self.health())

    def enrich(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any],
    ) -> tuple[LocalEnrichment, dict[str, Any]]:
        """One chat completion, schema-enforced, parsed into the contract.

        Returns the UNVERIFIED model alongside a stats dict. Verification is
        `contract.verify`'s job and is deliberately not done here: this class
        knows about HTTP, not about what counts as proof.
        """
        body: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            # Both stated explicitly rather than left to the server's defaults.
            # A streamed answer arrives as fragments this client cannot parse,
            # and a thinking model spends its budget on tokens we discard.
            "stream": self.settings.stream,
            "think": self.settings.think,
            "format": schema,
            "options": {
                "temperature": self.settings.temperature,
                "seed": self.settings.seed,
                "num_ctx": self.settings.num_ctx,
            },
        }
        status, payload = self._call("POST", "/api/chat", body)

        if status != 200:
            raise OllamaUnavailable(
                "the local Ollama endpoint answered /api/chat with a non-200 status."
            )

        message = payload.get("message")
        if not isinstance(message, dict):
            raise OllamaInvalidOutput("the /api/chat response carried no message object.")

        if message.get("refusal") or payload.get("done_reason") == "refusal":
            # A refusal is an answer. Retrying it, caching it as output, or
            # treating it as a transport failure would each be wrong.
            raise OllamaRefused("the local model declined to answer this posting.")

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise OllamaInvalidOutput("the /api/chat response carried no message content.")

        try:
            parsed = json.loads(content)
        except ValueError as exc:
            raise OllamaInvalidOutput(
                f"the model's message content was not valid JSON: {exc}"
            ) from exc

        if not isinstance(parsed, dict):
            raise OllamaInvalidOutput("the model returned JSON that is not an object.")

        try:
            enrichment = LocalEnrichment.model_validate(parsed)
        except Exception as exc:
            raise OllamaInvalidOutput(
                f"the model's JSON does not satisfy the enrichment contract: {exc}"
            ) from exc

        stats = {
            "total_duration": payload.get("total_duration"),
            "eval_count": payload.get("eval_count"),
            "prompt_eval_count": payload.get("prompt_eval_count"),
            "model": self.settings.model,
            "endpoint": "/api/chat",
            "think": self.settings.think,
        }
        return enrichment, stats


def _strip_latest(name: str) -> str:
    return name[: -len(":latest")] if name.endswith(":latest") else name
