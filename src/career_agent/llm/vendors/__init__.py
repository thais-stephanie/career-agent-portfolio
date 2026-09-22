"""One adapter per vendor, and the registry that names them.

Adding a fourth vendor should cost one new file and one entry here -- the same
test M1B applied to the ATS provider abstraction, applied to the LLM port.

`build_payload` and `parse_response` are exported alongside the clients because
they are the half that can be tested offline, and at M2 Phase A that is the
only half anyone is allowed to run.
"""

import os
from collections.abc import Callable
from typing import Any

from career_agent.llm.client import LLMClient
from career_agent.llm.vendors import anthropic, google, openai, openai_compatible

#: vendor name -> (client factory, build_payload, parse_response)
#: The factory is `Callable[..., LLMClient]` rather than `Callable[[], ...]`
#: because every adapter accepts an optional explicit key -- the tests pass
#: one, `get_client` never does.
ADAPTERS: dict[str, tuple[Callable[..., LLMClient], Callable[..., Any], Callable[..., Any]]] = {
    anthropic.VENDOR: (
        anthropic.AnthropicClient,
        anthropic.build_payload,
        anthropic.parse_response,
    ),
    openai.VENDOR: (openai.OpenAIClient, openai.build_payload, openai.parse_response),
    google.VENDOR: (google.GoogleClient, google.build_payload, google.parse_response),
    # Two routes, one dialect. Cerebras and OpenRouter both serve OpenAI's
    # chat-completions API, which is a different protocol from the Responses
    # API `openai.py` speaks -- so they share a module and register separately.
    openai_compatible.CEREBRAS: (
        openai_compatible.CerebrasClient,
        openai_compatible.build_payload,
        openai_compatible.parse_response,
    ),
    openai_compatible.OPENROUTER: (
        openai_compatible.OpenRouterClient,
        openai_compatible.build_payload,
        openai_compatible.parse_response,
    ),
}


#: Which environment variables hold each vendor's credential, in the order the
#: vendor's own SDK prefers them.
#:
#: PRESENCE ONLY. Nothing in this package reads, prints, logs or persists a
#: value; the adapters hand the variable name to the SDK and let it do the
#: reading. The map lives here rather than in the CLI because two callers now
#: need it -- `doctor` reports what is configured, and the live runner refuses
#: to start without it -- and two copies of a security-relevant list is one copy
#: too many.
CREDENTIAL_VARIABLES: dict[str, tuple[str, ...]] = {
    anthropic.VENDOR: ("ANTHROPIC_API_KEY",),
    openai.VENDOR: ("OPENAI_API_KEY",),
    # The Google SDK accepts either name and prefers GOOGLE_API_KEY.
    google.VENDOR: ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    openai_compatible.CEREBRAS: ("CEREBRAS_API_KEY",),
    openai_compatible.OPENROUTER: ("OPENROUTER_API_KEY",),
}


def credential_variables_present(vendor: str) -> list[str]:
    """Which of a vendor's credential variables are set. Never their values."""
    return [
        name for name in CREDENTIAL_VARIABLES.get(vendor, ()) if os.environ.get(name, "").strip()
    ]


class UnknownVendorError(KeyError):
    """A model config names a vendor that has no adapter."""


def available_vendors() -> list[str]:
    return sorted(ADAPTERS)


def get_client(vendor: str) -> LLMClient:
    """Construct the adapter for one vendor. Constructing costs nothing."""
    try:
        factory, _, _ = ADAPTERS[vendor]
    except KeyError as exc:
        raise UnknownVendorError(
            f"no adapter for vendor {vendor!r}; available: {', '.join(available_vendors())}"
        ) from exc
    return factory()
