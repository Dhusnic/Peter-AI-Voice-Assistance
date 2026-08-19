"""Provider-neutral types for the LLM layer.

Three vendors, three incompatible wire formats. The differences are real and
this module does not pretend otherwise — instead it defines the *narrow* set of
concepts all three genuinely share, and lets each provider keep its own native
conversation history internally.

    tool schema     all three want JSON Schema, wrapped differently
    tool call       id + name + arguments dict
    tool result     id + text
    stop reason     did it finish, want tools, pause, or refuse

Everything else — message shape, content blocks, caching, reasoning config —
stays inside the provider. Building a "universal message format" is the usual
mistake here: it is lossy in both directions, and every provider then needs
conversion code *plus* workarounds for what the format cannot express.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# What a turn ended with, normalised across vendors.
STOP_END = "end"          # finished speaking
STOP_TOOLS = "tools"      # wants tools run
STOP_PAUSE = "pause"      # server-side tool paused mid-turn; re-send to resume
STOP_REFUSAL = "refusal"  # declined on policy grounds
STOP_LENGTH = "length"    # hit the output token cap


@dataclass(slots=True)
class ToolSpec:
    """A tool as every provider needs to see it: name, description, JSON Schema."""

    name: str
    description: str
    parameters: dict[str, Any]

    @classmethod
    def from_anthropic_dict(cls, raw: dict) -> "ToolSpec":
        """Adapt the schema the Anthropic SDK derives from a function signature.

        The SDK's docstring and type-hint parsing is genuinely good, so it is
        used as the *derivation* step for every provider — not because Anthropic
        is privileged, but because rewriting signature-to-JSON-Schema inference
        would be a source of bugs with no upside.
        """
        return cls(
            name=raw.get("name", ""),
            description=(raw.get("description") or "").strip(),
            parameters=raw.get("input_schema") or {"type": "object", "properties": {}},
        )


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class ToolResult:
    id: str
    name: str
    content: str
    is_error: bool = False


@dataclass
class Usage:
    """Token accounting, normalised.

    `cache_read` and `cache_write` are Anthropic-shaped because it is the only
    one that exposes both. OpenAI reports cached input only; Gemini reports
    cached content tokens. Both land in `cache_read`, which is what they mean.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    turns: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write
        self.cost_usd += other.cost_usd

    def summary(self, provider: str = "", model: str = "", usd_to_inr: float = 88.0) -> str:
        where = f"{provider}/{model} | " if provider else ""
        return (
            f"{where}{self.turns} turns | in {self.input_tokens:,} "
            f"(cache r{self.cache_read:,}/w{self.cache_write:,}) "
            f"out {self.output_tokens:,} | ~₹{self.cost_usd * usd_to_inr:.2f}"
        )


@dataclass
class ProviderResponse:
    """One round-trip's result, normalised."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = STOP_END
    usage: Usage = field(default_factory=Usage)


class LLMProvider(ABC):
    """One conversation with one model.

    The provider owns its own message history in its native format. The shared
    loop in `peter/llm/loop.py` never inspects it.
    """

    #: Short identifier used in config and logs: "anthropic", "openai", "gemini".
    name: str = ""

    def __init__(self, model: str, system: str, max_tokens: int = 8000):
        self.model = model
        self.system = system
        self.max_tokens = max_tokens
        self.usage = Usage()

    @abstractmethod
    def reset(self) -> None:
        """Start a fresh conversation, keeping cumulative usage."""

    @abstractmethod
    def add_user(self, text: str) -> None:
        """Append a user turn."""

    @abstractmethod
    def add_tool_results(self, results: list[ToolResult]) -> None:
        """Append the results of the tool calls from the last response."""

    @abstractmethod
    def complete(self, tools: list[ToolSpec]) -> ProviderResponse:
        """Send the conversation and return the normalised response.

        Implementations must append their own assistant turn to their history
        before returning, so the next call resumes correctly.
        """

    @abstractmethod
    def trim(self, max_messages: int) -> None:
        """Drop the oldest turns, never orphaning a tool call from its result."""

    def health(self) -> str:
        """One-line status for `--health`. Overridden where a cheap check exists."""
        return f"{self.name}/{self.model}"

    def close(self) -> None:
        """Release any server-side resource this provider is paying to hold.

        Only meaningful where a provider keeps something billable alive between
        calls — currently Gemini's explicit prompt cache, which is charged per
        hour of storage. A no-op everywhere else.
        """

