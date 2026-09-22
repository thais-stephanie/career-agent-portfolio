# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Build the configured provider from environment variables.

    LLM_PROVIDER = auto | anthropic | openai | ollama | none
    LLM_MODEL    = model id (per-provider default if empty)
    API_KEY      = generic key; ANTHROPIC_API_KEY / OPENAI_API_KEY also honoured

``auto`` picks the simplest working path: a hosted key if one is present,
otherwise a reachable local Ollama, otherwise deterministic mode.
"""

from __future__ import annotations

import os
from pathlib import Path

from resume_tailor.providers.llm.base import LLMProvider
from resume_tailor.providers.llm.vendors import (
    AnthropicProvider,
    NoneProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4.1",
    "ollama": "qwen3:4b",
}


def build_provider(env: dict[str, str] | None = None, cache_dir: Path | None = None) -> LLMProvider:
    e = env if env is not None else os.environ
    provider = (e.get("LLM_PROVIDER") or "none").strip().lower()
    if provider not in {"none", "auto", "anthropic", "openai", "ollama"}:
        raise ValueError("Unknown LLM_PROVIDER. Choose none, auto, anthropic, openai or ollama.")
    model = (e.get("LLM_MODEL") or "").strip()
    generic_key = e.get("API_KEY", "").strip()
    timeout = float(e.get("LLM_TIMEOUT_S") or 180)
    local_timeout = float(e.get("LLM_TIMEOUT_S") or 900)  # CPU inference is slow rather than broken
    use_cache = (e.get("LLM_CACHE") or "1") not in ("0", "false", "no")
    cache = cache_dir if use_cache else None

    anthropic_key = e.get("ANTHROPIC_API_KEY", "").strip() or (
        generic_key if provider == "anthropic" else ""
    )
    openai_key = e.get("OPENAI_API_KEY", "").strip() or (
        generic_key if provider == "openai" else ""
    )
    ollama_url = e.get("OLLAMA_BASE_URL", "").strip() or "http://localhost:11434"

    def anthropic() -> LLMProvider:
        return AnthropicProvider(
            model or DEFAULT_MODELS["anthropic"],
            anthropic_key,
            e.get("ANTHROPIC_BASE_URL") or None,
            timeout=timeout,
            cache_dir=cache,
        )

    def openai() -> LLMProvider:
        return OpenAICompatibleProvider(
            model or DEFAULT_MODELS["openai"],
            openai_key,
            e.get("OPENAI_BASE_URL") or None,
            timeout=timeout,
            cache_dir=cache,
        )

    def ollama() -> OllamaProvider:
        return OllamaProvider(
            model or DEFAULT_MODELS["ollama"], ollama_url, timeout=local_timeout, cache_dir=cache
        )

    if provider == "none":
        return NoneProvider()
    if provider == "anthropic":
        if not anthropic_key:
            raise ValueError("LLM_PROVIDER=anthropic but no ANTHROPIC_API_KEY / API_KEY")
        return anthropic()
    if provider == "openai":
        if not openai_key:
            raise ValueError("LLM_PROVIDER=openai but no OPENAI_API_KEY / API_KEY")
        return openai()
    if provider == "ollama":
        return ollama()
    # auto
    if anthropic_key:
        return anthropic()
    if openai_key:
        return openai()
    o = ollama()
    if o.reachable():
        return o
    return NoneProvider()
