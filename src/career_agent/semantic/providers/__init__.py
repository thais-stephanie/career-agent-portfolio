"""The semantic providers Career Agent knows, by id.

Constructing a provider reaches nothing and reads no credential.
"""

from __future__ import annotations

from career_agent.semantic.providers.base import (
    Availability,
    Billing,
    Capabilities,
    ProviderAnswer,
    ProviderFailed,
    ProviderStatus,
    SemanticProvider,
)
from career_agent.semantic.providers.deepseek import DeepSeekProvider
from career_agent.semantic.providers.laya import LayaProvider
from career_agent.semantic.providers.local_cli import ClaudeCodeProvider, CodexProvider

#: Display order in Settings.
PROVIDER_IDS: tuple[str, ...] = ("deepseek", "codex", "claude_code", "laya")


def get_provider(provider_id: str) -> SemanticProvider:
    if provider_id == "deepseek":
        return DeepSeekProvider()
    if provider_id == "codex":
        return CodexProvider()
    if provider_id == "claude_code":
        return ClaudeCodeProvider()
    if provider_id == "laya":
        return LayaProvider()
    raise KeyError(f"unknown semantic provider {provider_id!r}")


__all__ = [
    "PROVIDER_IDS",
    "Availability",
    "Billing",
    "Capabilities",
    "ProviderAnswer",
    "ProviderFailed",
    "ProviderStatus",
    "SemanticProvider",
    "get_provider",
]
