# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Vendor-neutral LLM port.

Core services depend only on ``LLMProvider``. Each vendor adapter is one file
and is the only place that knows the vendor's HTTP dialect. No vendor SDKs are
imported anywhere; ``httpx`` is enough for three JSON APIs.

Every call is a *JSON task*: system + user prompt in, a parsed ``dict`` out.
Parsing is lenient (fenced code blocks, ``<think>`` blocks and leading prose
are stripped) because small local models are sloppy, and strict at the
boundary above this port (pydantic schemas in the services).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(RuntimeError):
    pass


@dataclass
class LLMInfo:
    provider: str
    model: str
    available: bool
    note: str = ""


@dataclass
class LLMResponse:
    text: str
    data: dict[str, Any] | None
    provider: str
    model: str
    cached: bool = False
    usage: dict[str, int] = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str
    model: str

    def complete_json(
        self, system: str, user: str, *, max_tokens: int = 4000, temperature: float = 0.0
    ) -> LLMResponse:
        """Return a parsed JSON object. Raise ``LLMError`` on transport/parse failure."""
        ...

    def info(self) -> LLMInfo: ...


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of the first JSON object in a model reply."""
    if not text:
        return None
    t = _THINK.sub("", text).strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # find the outermost {...}
    start = t.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                chunk = t[start : i + 1]
                try:
                    obj = json.loads(chunk)
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    # try to repair trailing commas
                    repaired = re.sub(r",\s*([}\]])", r"\1", chunk)
                    try:
                        obj = json.loads(repaired)
                        return obj if isinstance(obj, dict) else None
                    except json.JSONDecodeError:
                        return None
    return None
