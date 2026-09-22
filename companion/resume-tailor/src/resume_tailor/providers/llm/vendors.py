# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Concrete providers: Anthropic, OpenAI-compatible, Ollama, plus ``NoneProvider``
(deterministic mode) and ``FakeProvider`` (tests)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from resume_tailor.providers.llm.base import LLMError, LLMInfo, LLMResponse, extract_json


class _Cache:
    """Disk cache of JSON replies keyed by (provider, model, prompts). Optional."""

    def __init__(self, directory: Path | None):
        self.dir = directory
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    def key(self, provider: str, model: str, system: str, user: str) -> str:
        h = hashlib.sha256(f"{provider}\n{model}\n{system}\n{user}".encode()).hexdigest()
        return h[:32]

    def get(self, k: str) -> str | None:
        if not self.dir:
            return None
        p = self.dir / f"{k}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))["text"]
        return None

    def put(self, k: str, text: str, meta: dict[str, Any]) -> None:
        if not self.dir:
            return
        (self.dir / f"{k}.json").write_text(
            json.dumps({"text": text, **meta}, indent=2), encoding="utf-8"
        )


class _HttpProvider:
    name = "base"

    def __init__(self, model: str, timeout: float = 180.0, cache_dir: Path | None = None):
        self.model = model
        self.timeout = timeout
        self.cache = _Cache(cache_dir)

    def _call(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> tuple[str, dict[str, int]]:
        raise NotImplementedError

    def complete_json(
        self, system: str, user: str, *, max_tokens: int = 4000, temperature: float = 0.0
    ) -> LLMResponse:
        k = self.cache.key(self.name, self.model, system, user)
        cached = self.cache.get(k)
        if cached is not None:
            return LLMResponse(
                text=cached,
                data=extract_json(cached),
                provider=self.name,
                model=self.model,
                cached=True,
            )
        try:
            text, usage = self._call(system, user, max_tokens, temperature)
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name} transport error: {e}") from e
        data = extract_json(text)
        if data is None:
            raise LLMError(
                f"{self.name} returned no parseable JSON object (first 300 chars): {text[:300]!r}"
            )
        self.cache.put(k, text, {"provider": self.name, "model": self.model, "usage": usage})
        return LLMResponse(text=text, data=data, provider=self.name, model=self.model, usage=usage)


class AnthropicProvider(_HttpProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str, base_url: str | None = None, **kw: Any):
        super().__init__(model, **kw)
        self.api_key = api_key
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")

    def _call(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> tuple[str, dict[str, int]]:
        r = httpx.post(
            f"{self.base_url}/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system + "\nRespond with a single JSON object and nothing else.",
                "messages": [{"role": "user", "content": user}],
            },
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise LLMError(f"anthropic {r.status_code}: {r.text[:300]}")
        body = r.json()
        text = "".join(
            b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"
        )
        u = body.get("usage", {})
        return text, {
            "input_tokens": u.get("input_tokens", 0),
            "output_tokens": u.get("output_tokens", 0),
        }

    def info(self) -> LLMInfo:
        return LLMInfo(self.name, self.model, True, "hosted")


class OpenAICompatibleProvider(_HttpProvider):
    """OpenAI chat completions; also OpenRouter, Cerebras, NVIDIA, Groq via ``base_url``."""

    name = "openai"

    def __init__(self, model: str, api_key: str, base_url: str | None = None, **kw: Any):
        super().__init__(model, **kw)
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")

    def _call(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> tuple[str, dict[str, int]]:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": system + "\nRespond with a single JSON object and nothing else.",
                },
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        r = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        if r.status_code == 400 and "response_format" in r.text:
            payload.pop("response_format")
            r = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=self.timeout,
            )
        if r.status_code >= 400:
            raise LLMError(f"openai-compatible {r.status_code}: {r.text[:300]}")
        body = r.json()
        text = body["choices"][0]["message"].get("content") or ""
        u = body.get("usage", {})
        return text, {
            "input_tokens": u.get("prompt_tokens", 0),
            "output_tokens": u.get("completion_tokens", 0),
        }

    def info(self) -> LLMInfo:
        return LLMInfo(self.name, self.model, True, self.base_url)


class OllamaProvider(_HttpProvider):
    name = "ollama"

    def __init__(self, model: str, base_url: str = "http://localhost:11434", **kw: Any):
        super().__init__(model, **kw)
        self.base_url = base_url.rstrip("/")

    def _call(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> tuple[str, dict[str, int]]:
        # size the context window from the prompt (roughly 4 chars/token) so long prompts are not truncated
        approx_tokens = (len(system) + len(user)) // 4 + max_tokens
        num_ctx = int(min(32768, max(8192, approx_tokens * 1.25)))
        r = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "format": "json",
                "think": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                    "num_ctx": num_ctx,
                },
                "messages": [
                    {
                        "role": "system",
                        "content": system + "\nRespond with a single JSON object and nothing else.",
                    },
                    {"role": "user", "content": user},
                ],
            },
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise LLMError(f"ollama {r.status_code}: {r.text[:300]}")
        body = r.json()
        text = body.get("message", {}).get("content", "")
        return text, {
            "input_tokens": body.get("prompt_eval_count", 0),
            "output_tokens": body.get("eval_count", 0),
        }

    def reachable(self) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            if r.status_code != 200:
                return False
            names = [m.get("name", "") for m in r.json().get("models", [])]
            return any(
                n == self.model or n.split(":")[0] == self.model.split(":")[0] for n in names
            )
        except httpx.HTTPError:
            return False

    def info(self) -> LLMInfo:
        ok = self.reachable()
        return LLMInfo(
            self.name, self.model, ok, "local" if ok else "ollama not reachable or model missing"
        )


class NoneProvider:
    """Deterministic mode: every service falls back to its code-only path."""

    name = "none"
    model = "deterministic"

    def complete_json(
        self, system: str, user: str, *, max_tokens: int = 4000, temperature: float = 0.0
    ) -> LLMResponse:
        raise LLMError("no LLM configured (deterministic mode)")

    def info(self) -> LLMInfo:
        return LLMInfo(self.name, self.model, False, "deterministic mode: no model calls")


class FakeProvider:
    """Test double. ``handler(system, user) -> dict`` decides the reply per call."""

    name = "fake"
    model = "fake"

    def __init__(self, handler: Callable[[str, str], dict[str, Any]]):
        self.handler = handler
        self.calls: list[tuple[str, str]] = []

    def complete_json(
        self, system: str, user: str, *, max_tokens: int = 4000, temperature: float = 0.0
    ) -> LLMResponse:
        self.calls.append((system, user))
        data = self.handler(system, user)
        return LLMResponse(text=json.dumps(data), data=data, provider=self.name, model=self.model)

    def info(self) -> LLMInfo:
        return LLMInfo(self.name, self.model, True, "test double")
