"""Anthropic (Claude).

Uses `messages.create` with a manual loop rather than the SDK's `tool_runner`.
The runner is excellent, but it owns the loop, and this project needs one loop
shared across three vendors. Two things the manual path must get right:

**Prompt caching is a prefix match.** The system prompt carries
`cache_control`, so it must be byte-identical between turns. Volatile context
goes in the user message — see `peter/agent/brain.py`.

**`pause_turn` is a real stop reason.** A server-side tool (web search) can
pause mid-turn. The assistant turn is appended and `STOP_PAUSE` returned; the
shared loop re-sends without tool results, which resumes it. Treating a pause
as "finished" produces a silently truncated answer with no error.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from peter.core.errors import AuthError, IntegrationError
from peter.llm import pricing
from peter.llm.base import (
    STOP_END,
    STOP_LENGTH,
    STOP_PAUSE,
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

# Server-side tools, run on Anthropic's infrastructure. Only this provider has
# them; the others get local web search or nothing.
SERVER_TOOLS: list[dict] = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 5},
]

_STOP_MAP = {
    "end_turn": STOP_END,
    "stop_sequence": STOP_END,
    "tool_use": STOP_TOOLS,
    "pause_turn": STOP_PAUSE,
    "refusal": STOP_REFUSAL,
    "max_tokens": STOP_LENGTH,
}


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        model: str,
        system: str,
        api_key: str,
        max_tokens: int = 8000,
        effort: str = "low",
        cache_ttl: str = "1h",
        enable_web: bool = True,
        timeout: float = 60.0,
        client: Any = None,
    ):
        super().__init__(model, system, max_tokens)
        self.effort = effort
        self.cache_ttl = cache_ttl
        self.enable_web = enable_web
        self.messages: list[dict] = []
        self._client = client or anthropic.Anthropic(
            api_key=api_key or None, timeout=timeout
        )

    # ------------------------------------------------------------- history
    def reset(self) -> None:
        self.messages = []

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self.messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": r.id,
                    "content": r.content,
                    **({"is_error": True} if r.is_error else {}),
                }
                for r in results
            ],
        })

    def trim(self, max_messages: int) -> None:
        if len(self.messages) <= max_messages:
            return
        cut = len(self.messages) - max_messages
        # The cut must land on a plain user message. Slicing between a
        # tool_use and its tool_result makes the API reject the request.
        while cut < len(self.messages) and not self._starts_turn(self.messages[cut]):
            cut += 1
        if cut < len(self.messages):
            self.messages = self.messages[cut:]

    @staticmethod
    def _starts_turn(message: dict) -> bool:
        if message.get("role") != "user":
            return False
        content = message.get("content")
        if isinstance(content, str):
            return True
        return not any(
            (getattr(b, "type", None) or (isinstance(b, dict) and b.get("type")))
            == "tool_result"
            for b in content or []
        )

    # ------------------------------------------------------------ completion
    def complete(self, tools: list[ToolSpec]) -> ProviderResponse:
        payload: list[Any] = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]
        if self.enable_web:
            payload.extend(SERVER_TOOLS)

        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[{
                    "type": "text",
                    "text": self.system,
                    "cache_control": {"type": "ephemeral", "ttl": self.cache_ttl},
                }],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                tools=payload,
                messages=self.messages,
            )
        except anthropic.AuthenticationError as exc:
            raise AuthError(
                "Anthropic rejected the API key",
                service="anthropic",
                user_action="Check ANTHROPIC_API_KEY in .env.",
            ) from exc
        except anthropic.RateLimitError as exc:
            raise IntegrationError(
                "Anthropic rate limit reached", service="anthropic",
                recoverable=True,
            ) from exc
        except anthropic.APIStatusError as exc:
            raise IntegrationError(
                f"Anthropic error {exc.status_code}: {exc.message}",
                service="anthropic", recoverable=exc.status_code >= 500,
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise IntegrationError(
                f"could not reach Anthropic: {exc}", service="anthropic",
                recoverable=True, user_action="Check your internet connection.",
            ) from exc

        # Keep the full content list — thinking and tool_use blocks must be
        # echoed back verbatim or the next request is rejected.
        self.messages.append({"role": "assistant", "content": response.content})

        usage = self._usage(response)
        self.usage.add(usage)

        return ProviderResponse(
            text=self._text(response),
            tool_calls=self._tool_calls(response),
            stop_reason=_STOP_MAP.get(response.stop_reason or "", STOP_END),
            usage=usage,
        )

    def _usage(self, response) -> Usage:
        raw = response.usage
        usage = Usage(
            input_tokens=getattr(raw, "input_tokens", 0) or 0,
            output_tokens=getattr(raw, "output_tokens", 0) or 0,
            cache_read=getattr(raw, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(raw, "cache_creation_input_tokens", 0) or 0,
        )
        usage.cost_usd = pricing.estimate(self.model, usage)
        return usage

    @staticmethod
    def _text(response) -> str:
        parts = [
            b.text for b in response.content
            if getattr(b, "type", None) == "text" and getattr(b, "text", "")
        ]
        return " ".join(p.strip() for p in parts if p.strip()).strip()

    @staticmethod
    def _tool_calls(response) -> list[ToolCall]:
        return [
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input or {}))
            for b in response.content
            if getattr(b, "type", None) == "tool_use"
        ]

    def health(self) -> str:
        return f"anthropic/{self.model}"
