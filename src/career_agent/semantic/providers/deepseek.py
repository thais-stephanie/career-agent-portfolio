"""DeepSeek, an API-key-backed metered provider.

Reached through the project's existing chat-completions adapter
(`llm.vendors.openai_compatible`, route `deepseek`), not a second client. The
key is read from `DEEPSEEK_API_KEY` at call time, handed to the SDK and never
printed, logged, persisted or returned.

Thinking is OFF. DeepSeek thinks by default at high effort and bills it as
output; the benchmark in docs/SEMANTIC_MATCHING.md measured whether the
contract needs it.
"""

from __future__ import annotations

import os
import time

from career_agent.llm.client import (
    Family,
    LLMError,
    LLMErrorResponse,
    LLMRequest,
    ModelConfig,
    StructuredOutput,
)
from career_agent.llm.pricing import estimate_cost, observed_price
from career_agent.llm.vendors.openai_compatible import DEEPSEEK, DeepSeekClient
from career_agent.semantic.contract import SYSTEM_PROMPT, answer_schema, user_message
from career_agent.semantic.intent import SearchIntent
from career_agent.semantic.providers.base import (
    Availability,
    Billing,
    Capabilities,
    ProviderAnswer,
    ProviderFailed,
    ProviderStatus,
)

CREDENTIAL = "DEEPSEEK_API_KEY"
MODEL = "deepseek-flash"
TIMEOUT_SECONDS = 60.0
#: A generous ceiling for the answer: forty intent items with quotes fit well
#: inside it, and a truncated answer is a rejected one, not a partial one.
MAX_OUTPUT_TOKENS = 2_500
#: With thinking on, reasoning shares the output ceiling; 2,500 truncated 12
#: of 39 benchmark answers at low effort.
MAX_OUTPUT_TOKENS_THINKING = 8_000


def key_configured() -> bool:
    return bool(os.environ.get(CREDENTIAL, "").strip())


class DeepSeekProvider:
    id = "deepseek"
    display_name = "DeepSeek Flash"

    def __init__(self, model: str = MODEL, reasoning: str | None = None) -> None:
        self.model = model
        self.reasoning = reasoning

    def _config(self, max_output_tokens: int = MAX_OUTPUT_TOKENS) -> ModelConfig:
        return ModelConfig(
            vendor=DEEPSEEK,
            identifier=self.model,
            reasoning=self.reasoning,
            structured_output=StructuredOutput.JSON_OBJECT,
            max_output_tokens=max_output_tokens,
            temperature=0.0,
        )

    def availability(self) -> ProviderStatus:
        if not key_configured():
            return ProviderStatus(
                Availability.KEY_MISSING, "Add a DeepSeek API key to use this provider."
            )
        return ProviderStatus(Availability.AVAILABLE, "API key configured.")

    def capabilities(self) -> Capabilities:
        return Capabilities(
            billing=Billing.METERED_API,
            quotes=True,
            max_concurrency=4,
            sends="Your search intent phrases and the text of each posting evaluated.",
        )

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        price = observed_price(DEEPSEEK, self.model)
        if price is None:
            return None
        return estimate_cost(price, input_tokens, output_tokens)

    def _complete(self, system: str, user: str, max_output_tokens: int) -> ProviderAnswer:
        if not key_configured():
            raise ProviderFailed("DeepSeek API key missing.", state=Availability.KEY_MISSING)
        client = DeepSeekClient(timeout_seconds=TIMEOUT_SECONDS)
        request = LLMRequest(
            family=Family.SEMANTIC,
            system=system,
            user=user,
            schema=answer_schema(),
            schema_name="semantic_answer",
        )
        started = time.monotonic()
        try:
            response = client.complete(request, self._config(max_output_tokens))
        except LLMErrorResponse as exc:
            state = (
                Availability.KEY_MISSING
                if exc.status in (401, 403)
                else Availability.LIMIT_OR_ERROR
            )
            raise ProviderFailed(f"DeepSeek answered HTTP {exc.status}.", state=state) from exc
        except LLMError as exc:
            raise ProviderFailed(
                "DeepSeek could not be reached.", state=Availability.CONNECTION_FAILED
            ) from exc
        latency = int((time.monotonic() - started) * 1000)
        if response.refusal:
            raise ProviderFailed(f"DeepSeek returned no answer: {response.refusal}")
        if not response.raw_text.strip():
            raise ProviderFailed("DeepSeek returned empty content.")
        cost = None
        if response.input_tokens is not None and response.output_tokens is not None:
            cost = self.estimate_cost(response.input_tokens, response.output_tokens)
        return ProviderAnswer(
            raw_text=response.raw_text,
            provider=self.id,
            model=response.model or self.model,
            latency_ms=latency,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=cost,
        )

    def evaluate(self, intent: SearchIntent, title: str, posting: str) -> ProviderAnswer:
        message = user_message(intent, title, posting)
        return self._complete(SYSTEM_PROMPT, message, self.max_output_tokens)

    @property
    def max_output_tokens(self) -> int:
        if self.reasoning in (None, "none"):
            return MAX_OUTPUT_TOKENS
        return MAX_OUTPUT_TOKENS_THINKING

    def healthcheck(self) -> ProviderStatus:
        try:
            self._complete('Reply with the json object {"ok": true}.', "json", 20)
        except ProviderFailed as exc:
            return ProviderStatus(exc.state, str(exc))
        return ProviderStatus(Availability.CONNECTED, "Connected.")
