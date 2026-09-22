"""The Ollama adapter: loopback, no credentials, no network at import.

Every test here runs offline against an injected fake transport. Nothing in this
file may reach a socket, and `test_importing_the_module_touches_no_network_stack`
is the assertion that keeps it that way even if someone later moves the `httpx`
import to module scope.

The security property under test is `assert_loopback`. It is not a validation
nicety: it is what makes "local inference" a fact. A stale `OLLAMA_BASE_URL`
would otherwise ship a candidate capability line and a full job posting to a
third party with no credential involved, so nothing downstream would notice.
"""

import importlib
import json
from typing import Any

import pytest

from career_agent.local_ai import ollama as ollama_module
from career_agent.local_ai.contract import LocalEnrichment, json_schema
from career_agent.local_ai.ollama import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_MS,
    OllamaClient,
    OllamaInvalidOutput,
    OllamaRefused,
    OllamaSettings,
    OllamaUnavailable,
    assert_loopback,
)

# A value that must never appear in a request, a setting or an exception.
SECRET_KEY = "sk-local-should-never-be-read-9f3a"


class FakeTransport:
    """Records every call and replays a scripted answer.

    Its signature is the whole contract: `(method, url, json_body, timeout)`.
    There is no headers parameter, which is how "this client cannot send an
    Authorization header" is enforced structurally rather than by inspection.
    """

    def __init__(
        self, *answers: tuple[int, dict[str, Any]], raises: Exception | None = None
    ) -> None:
        self.answers = list(answers)
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        json_body: dict[str, Any] | None,
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append(
            {"method": method, "url": url, "json_body": json_body, "timeout": timeout}
        )
        if self.raises is not None:
            raise self.raises
        return self.answers.pop(0)


def _chat_body(payload: dict[str, Any] | str) -> dict[str, Any]:
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return {
        "model": DEFAULT_MODEL,
        "message": {"role": "assistant", "content": content},
        "done_reason": "stop",
        "total_duration": 1234,
        "eval_count": 42,
        "prompt_eval_count": 7,
    }


VALID_ANSWER = {
    "summary": "A systems role.",
    "technologies": [{"text": "Python", "quote": "integrations in Python"}],
    "strengths": [],
    "gaps": [],
    "risk_flags": [],
    "recommended_action": "READ_IN_FULL",
    "confidence": "MEDIUM",
}


# --- the loopback boundary ------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://127.0.0.2:11434",
        "http://[::1]:11434",
        "https://localhost:11434",
    ],
)
def test_loopback_urls_are_accepted(url: str) -> None:
    assert assert_loopback(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://ollama.example.com",
        "https://api.openai.com",
        "http://169.254.169.254",
        "http://127.0.0.1.evil.com",
        "http://10.0.0.7:11434",
        "http://user:hunter2@evil.example.com:11434",
        "http://user:hunter2@localhost:11434",
        "ftp://localhost:11434",
        "file:///etc/passwd",
        "not-a-url",
    ],
)
def test_non_loopback_urls_are_refused(url: str) -> None:
    with pytest.raises(OllamaRefused):
        assert_loopback(url)


def test_a_hostname_is_never_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolving an attacker-supplied name is itself an egress that leaks the
    query, so refusal happens without asking any resolver."""
    import socket

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("DNS resolution attempted while enforcing loopback")

    monkeypatch.setattr(socket, "gethostbyname", explode)
    monkeypatch.setattr(socket, "getaddrinfo", explode)

    with pytest.raises(OllamaRefused):
        assert_loopback("http://ollama.example.com:11434")


def test_credential_in_url_is_never_echoed() -> None:
    with pytest.raises(OllamaRefused) as caught:
        assert_loopback("http://admin:hunter2@localhost:11434")
    assert "hunter2" not in str(caught.value)
    assert "admin" not in str(caught.value)


def test_constructor_refuses_a_non_loopback_url() -> None:
    with pytest.raises(OllamaRefused):
        OllamaClient(
            OllamaSettings(base_url="http://ollama.example.com"), transport=FakeTransport()
        )


def test_loopback_is_rechecked_before_every_request() -> None:
    """Frozen settings protect the field, not the client object."""
    transport = FakeTransport((200, {"models": [{"name": DEFAULT_MODEL}]}))
    client = OllamaClient(OllamaSettings(), transport=transport)
    client.settings = OllamaSettings(base_url="http://evil.example.com")  # reached in past the door
    with pytest.raises(OllamaRefused):
        client.health()
    assert transport.calls == []


# --- the environment ------------------------------------------------------


def test_from_env_reads_exactly_three_variables() -> None:
    settings = OllamaSettings.from_env(
        {
            "OLLAMA_BASE_URL": "http://127.0.0.1:1234/",
            "OLLAMA_MODEL": "llama3.2:3b",
            "OLLAMA_TIMEOUT_MS": "60000",
        }
    )
    assert settings.base_url == "http://127.0.0.1:1234"
    assert settings.model == "llama3.2:3b"
    assert settings.timeout_ms == 60_000


def test_from_env_defaults_when_nothing_is_set() -> None:
    settings = OllamaSettings.from_env({})
    assert settings.base_url == DEFAULT_BASE_URL
    assert settings.model == DEFAULT_MODEL
    assert settings.timeout_ms == DEFAULT_TIMEOUT_MS


def test_ollama_api_key_changes_nothing_about_the_settings() -> None:
    """Loopback needs no authentication. A variable by that name is either left
    over from a hosted provider or an attempt to make this client authenticate
    to something that is not local, and it is read by nothing here."""
    without = OllamaSettings.from_env({"OLLAMA_MODEL": "qwen3:4b"})
    with_key = OllamaSettings.from_env(
        {
            "OLLAMA_MODEL": "qwen3:4b",
            "OLLAMA_API_KEY": SECRET_KEY,
            "OPENAI_API_KEY": SECRET_KEY,
            "GOOGLE_API_KEY": SECRET_KEY,
        }
    )
    assert without == with_key
    assert SECRET_KEY not in json.dumps(with_key.digest_material())


def test_ollama_api_key_changes_nothing_about_the_request() -> None:
    transport = FakeTransport((200, _chat_body(VALID_ANSWER)))
    settings = OllamaSettings.from_env({"OLLAMA_API_KEY": SECRET_KEY})
    client = OllamaClient(settings, transport=transport)
    client.enrich([{"role": "user", "content": "hi"}], schema=json_schema())

    recorded = json.dumps(transport.calls)
    assert SECRET_KEY not in recorded
    assert "authorization" not in recorded.lower()
    assert "api_key" not in recorded.lower()
    # The transport is called with four positional values and no header channel.
    assert set(transport.calls[0]) == {"method", "url", "json_body", "timeout"}


def test_a_bad_timeout_names_the_variable_not_the_value() -> None:
    with pytest.raises(ValueError) as caught:
        OllamaSettings.from_env({"OLLAMA_TIMEOUT_MS": "soon-ish"})
    assert "OLLAMA_TIMEOUT_MS" in str(caught.value)
    assert "soon-ish" not in str(caught.value)


def test_no_environment_value_reaches_an_exception_message() -> None:
    """A traceback pasted into a chat window must not carry configuration."""
    env = {
        "OLLAMA_BASE_URL": "http://ollama.internal.example.com:9999",
        "OLLAMA_MODEL": "secret-model-name-zzz",
        "OLLAMA_TIMEOUT_MS": "4242",
        "OLLAMA_API_KEY": SECRET_KEY,
    }
    settings = OllamaSettings.from_env(env)

    with pytest.raises(OllamaRefused) as refused:
        OllamaClient(settings, transport=FakeTransport())
    for value in env.values():
        assert value not in str(refused.value)
    # The offending host is still available, deliberately, as data.
    assert refused.value.host == "ollama.internal.example.com"

    local = OllamaClient(
        OllamaSettings(model=env["OLLAMA_MODEL"], timeout_ms=4242),
        transport=FakeTransport(raises=ConnectionError("connection refused")),
    )
    with pytest.raises(OllamaUnavailable) as unavailable:
        local.health()
    for value in env.values():
        assert value not in str(unavailable.value)


# --- health ---------------------------------------------------------------


def test_health_lists_model_names() -> None:
    transport = FakeTransport(
        (200, {"models": [{"name": "qwen3:4b"}, {"name": "llama3.2:latest"}]})
    )
    client = OllamaClient(OllamaSettings(), transport=transport)
    assert client.health() == ["qwen3:4b", "llama3.2:latest"]
    assert transport.calls[0]["method"] == "GET"
    assert transport.calls[0]["url"] == "http://localhost:11434/api/tags"


def test_health_raises_unavailable_on_a_connection_error() -> None:
    """And the caller can carry on: enrichment is optional, correctness is not."""
    client = OllamaClient(
        OllamaSettings(),
        transport=FakeTransport(raises=ConnectionError("[Errno 111] Connection refused")),
    )
    try:
        client.health()
    except OllamaUnavailable as exc:
        message = str(exc)
        assert message.strip()
        assert "Ollama" in message
    else:  # pragma: no cover - the call above must raise
        pytest.fail("a connection error must surface as OllamaUnavailable")


def test_health_raises_unavailable_on_a_non_200() -> None:
    client = OllamaClient(OllamaSettings(), transport=FakeTransport((503, {})))
    with pytest.raises(OllamaUnavailable):
        client.health()


@pytest.mark.parametrize(
    ("configured", "served", "expected"),
    [
        ("qwen3:4b", "qwen3:4b", True),
        ("mistral", "mistral:latest", True),
        ("mistral:latest", "mistral", True),
        ("qwen3:4b", "llama3.2:3b", False),
    ],
)
def test_is_model_available_folds_the_latest_tag(
    configured: str, served: str, expected: bool
) -> None:
    client = OllamaClient(
        OllamaSettings(model=configured),
        transport=FakeTransport((200, {"models": [{"name": served}]})),
    )
    assert client.is_model_available() is expected


# --- enrich ---------------------------------------------------------------


def test_enrich_sends_the_deterministic_body() -> None:
    transport = FakeTransport((200, _chat_body(VALID_ANSWER)))
    client = OllamaClient(OllamaSettings(), transport=transport)
    schema = json_schema()

    enrichment, stats = client.enrich([{"role": "user", "content": "hi"}], schema=schema)

    body = transport.calls[0]["json_body"]
    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["url"].endswith("/api/chat")
    assert body["stream"] is False
    assert body["think"] is False
    assert body["options"]["temperature"] == 0
    assert body["options"]["seed"] == 0
    assert body["format"] == schema
    assert body["model"] == DEFAULT_MODEL

    assert isinstance(enrichment, LocalEnrichment)
    assert enrichment.summary == "A systems role."
    assert stats == {
        "total_duration": 1234,
        "eval_count": 42,
        "prompt_eval_count": 7,
        "model": DEFAULT_MODEL,
        "endpoint": "/api/chat",
        "think": False,
    }


def test_enrich_rejects_content_that_is_not_json() -> None:
    client = OllamaClient(
        OllamaSettings(), transport=FakeTransport((200, _chat_body("{not json at all")))
    )
    with pytest.raises(OllamaInvalidOutput):
        client.enrich([], schema=json_schema())


def test_enrich_rejects_json_that_violates_the_contract() -> None:
    bad = dict(VALID_ANSWER, confidence="VERY_SURE")
    client = OllamaClient(OllamaSettings(), transport=FakeTransport((200, _chat_body(bad))))
    with pytest.raises(OllamaInvalidOutput):
        client.enrich([], schema=json_schema())


def test_enrich_rejects_an_extra_field() -> None:
    bad = dict(VALID_ANSWER, match_score=0.87)
    client = OllamaClient(OllamaSettings(), transport=FakeTransport((200, _chat_body(bad))))
    with pytest.raises(OllamaInvalidOutput):
        client.enrich([], schema=json_schema())


def test_enrich_rejects_a_response_with_no_message() -> None:
    client = OllamaClient(OllamaSettings(), transport=FakeTransport((200, {"done": True})))
    with pytest.raises(OllamaInvalidOutput):
        client.enrich([], schema=json_schema())


def test_a_refusal_is_a_decision_not_a_failure() -> None:
    body = _chat_body("")
    body["message"]["refusal"] = "I cannot help with that."
    client = OllamaClient(OllamaSettings(), transport=FakeTransport((200, body)))
    with pytest.raises(OllamaRefused):
        client.enrich([], schema=json_schema())


# --- no network at import -------------------------------------------------


def test_importing_the_module_touches_no_network_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """`httpx` is imported inside the request function on purpose.

    Making `httpx.Client` explode on construction proves both halves: importing
    and reloading the adapter constructs none, and neither does driving it with
    an injected transport.
    """
    import httpx

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an HTTP client was constructed without a request being made")

    monkeypatch.setattr(httpx, "Client", explode)

    reloaded = importlib.reload(ollama_module)
    client = reloaded.OllamaClient(
        reloaded.OllamaSettings(),
        transport=FakeTransport((200, {"models": [{"name": "qwen3:4b"}]})),
    )
    assert client.health() == ["qwen3:4b"]
