"""OpenAI (GPT).

Uses the **Responses API**, not Chat Completions. It is the endpoint OpenAI
builds tool support on, and its `function_call` / `function_call_output` items
map onto this project's tool loop far more directly than Chat Completions'
`tool_calls` array with its separate `role: "tool"` messages.

Two differences from Anthropic worth knowing:

**The system prompt is `instructions`, not a message.** It sits outside the
conversation entirely, so it cannot drift into the history.

**Caching is automatic and implicit.** There is no `cache_control`: OpenAI
caches prefixes over ~1024 tokens on its own and reports the hit in
`usage.input_tokens_details.cached_tokens`. Nothing to configure, but also
nothing to control — a stable prefix still matters, it is just not requested.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from peter.core.errors import AuthError, IntegrationError
from peter.llm import pricing
from peter.llm.base import (
    STOP_END,
    STOP_LENGTH,
    STOP_REFUSAL,
    STOP_TOOLS,
    LLMProvider,
    ProviderResponse,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)

log = logging.getLogger(__name__)

# Reasoning effort. Anthropic's ladder has five rungs, OpenAI's has four —
# "xhigh" and "max" both land on "high".
_EFFORT_MAP = {
    "low": "low", "medium": "medium", "high": "high",
    "xhigh": "high", "max": "high",
}


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        model: str,
        system: str,
        api_key: str,
        max_tokens: int = 8000,
        effort: str = "low",
        timeout: float = 60.0,
        client: Any = None,
    ):
        super().__init__(model, system, max_tokens)
        self.effort = _EFFORT_MAP.get(effort, "low")
        self.items: list[dict] = []

        if client is not None:
            self._client = client
        else:
            import openai

            self._client = openai.OpenAI(api_key=api_key or None, timeout=timeout)

    # ------------------------------------------------------------- history
    def reset(self) -> None:
        self.items = []

    def add_user(self, text: str) -> None:
        self.items.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[ToolResult]) -> None:
        # Each result is its own top-level item, not nested under one message.
        for r in results:
            self.items.append({
                "type": "function_call_output",
                "call_id": r.id,
                "output": r.content,
            })

    def trim(self, max_messages: int) -> None:
        if len(self.items) <= max_messages:
            return
        cut = len(self.items) - max_messages
        # Never start the history on a function_call_output whose originating
        # function_call has been trimmed away — the API rejects the orphan.
        while cut < len(self.items) and not self._starts_turn(self.items[cut]):
            cut += 1
        if cut < len(self.items):
            self.items = self.items[cut:]

    @staticmethod
    def _starts_turn(item: dict) -> bool:
        return item.get("role") == "user" and "type" not in item

    # ------------------------------------------------------------ completion
    def complete(self, tools: list[ToolSpec]) -> ProviderResponse:
        payload = [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in tools
        ]

        try:
            response = self._client.responses.create(
                model=self.model,
                instructions=self.system,
                input=self.items,
                tools=payload,
                max_output_tokens=self.max_tokens,
                reasoning={"effort": self.effort},
                store=False,   # we keep the history ourselves
            )
        except Exception as exc:
            raise self._translate(exc) from exc

        # Echo the model's own items back verbatim next turn — reasoning items
        # in particular must round-trip or the model loses its chain.
        produced = self._output_items(response)
        self.items.extend(produced)

        usage = self._usage(response)
        self.usage.add(usage)

        tool_calls = [
            ToolCall(
                id=item.get("call_id") or item.get("id", ""),
                name=item.get("name", ""),
                arguments=_parse_arguments(item.get("arguments")),
            )
            for item in produced
            if item.get("type") == "function_call"
        ]

        return ProviderResponse(
            text=self._text(response),
            tool_calls=tool_calls,
            stop_reason=self._stop_reason(response, tool_calls),
            usage=usage,
        )

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _output_items(response) -> list[dict]:
        """Normalise SDK objects to plain dicts so history stays serialisable."""
        items: list[dict] = []
        for item in getattr(response, "output", None) or []:
            if hasattr(item, "model_dump"):
                items.append(item.model_dump(exclude_none=True))
            elif isinstance(item, dict):
                items.append(item)
        return items

    @staticmethod
    def _text(response) -> str:
        direct = getattr(response, "output_text", None)
        if direct:
            return direct.strip()

        chunks: list[str] = []
        for item in getattr(response, "output", None) or []:
            if getattr(item, "type", None) != "message":
                continue
            for block in getattr(item, "content", None) or []:
                text = getattr(block, "text", None)
                if text:
                    chunks.append(text)
        return " ".join(c.strip() for c in chunks if c.strip()).strip()

    @staticmethod
    def _stop_reason(response, tool_calls: list[ToolCall]) -> str:
        if tool_calls:
            return STOP_TOOLS

        incomplete = getattr(response, "incomplete_details", None)
        reason = getattr(incomplete, "reason", None) if incomplete else None
        if reason == "max_output_tokens":
            return STOP_LENGTH
        if reason == "content_filter":
            return STOP_REFUSAL

        for item in getattr(response, "output", None) or []:
            if getattr(item, "type", None) == "refusal":
                return STOP_REFUSAL
            for block in getattr(item, "content", None) or []:
                if getattr(block, "type", None) == "refusal":
                    return STOP_REFUSAL
        return STOP_END

    def _usage(self, response) -> Usage:
        raw = getattr(response, "usage", None)
        if raw is None:
            return Usage()

        cached = 0
        details = getattr(raw, "input_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0

        total_input = getattr(raw, "input_tokens", 0) or 0
        usage = Usage(
            # OpenAI counts cached tokens inside input_tokens; Anthropic reports
            # them separately. Subtract so the two mean the same thing here.
            input_tokens=max(0, total_input - cached),
            output_tokens=getattr(raw, "output_tokens", 0) or 0,
            cache_read=cached,
        )
        usage.cost_usd = pricing.estimate(self.model, usage)
        return usage

    def _translate(self, exc: Exception) -> Exception:
        import openai

        if isinstance(exc, openai.AuthenticationError):
            return AuthError(
                "OpenAI rejected the API key",
                service="openai",
                user_action="Check OPENAI_API_KEY in .env.",
            )
        if isinstance(exc, openai.RateLimitError):
            return IntegrationError(
                "OpenAI rate limit reached", service="openai", recoverable=True
            )
        if isinstance(exc, openai.APIStatusError):
            return IntegrationError(
                f"OpenAI error {exc.status_code}",
                service="openai", recoverable=exc.status_code >= 500,
            )
        if isinstance(exc, openai.APIConnectionError):
            return IntegrationError(
                f"could not reach OpenAI: {exc}", service="openai",
                recoverable=True, user_action="Check your internet connection.",
            )
        return IntegrationError(f"OpenAI call failed: {exc}", service="openai")

    def health(self) -> str:
        return f"openai/{self.model}"


def _parse_arguments(raw: Any) -> dict:
    """Arguments arrive as a JSON *string*, unlike Anthropic's dict."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.warning("could not parse tool arguments: %.120s", raw)
        return {}
    return parsed if isinstance(parsed, dict) else {}
