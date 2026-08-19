"""The agent.

Thin by design. Since the multi-provider refactor, the vendor-specific work
lives in `peter/llm/providers/` and the tool loop lives in `peter/llm/loop.py`.
What is left here is the part that is genuinely Peter's own:

  * building each turn's user message — current time and relevant memories go
    **here**, never in the system prompt, which is the cached prefix on all
    three vendors
  * trimming history so a long day does not grow the request without bound
  * routing tool calls through the registry, so the permission gate sees them
  * switching provider mid-session without losing memory or usage totals
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from peter.agent import registry
from peter.agent.prompts import system_prompt
from peter.core.config import Config, get_config
from peter.core.services import services
from peter.llm import factory, loop
from peter.llm.base import LLMProvider, ToolCall, Usage
from peter.memory.store import MemoryStore

log = logging.getLogger(__name__)


@dataclass
class TurnResult:
    text: str
    tool_calls: list[str] = field(default_factory=list)
    stop_reason: str | None = None


class Brain:
    def __init__(
        self,
        memory: MemoryStore,
        config: Config | None = None,
        provider: LLMProvider | None = None,
        provider_name: str | None = None,
    ):
        self.config = config or get_config()
        self.memory = memory
        self._system = system_prompt(self.config)
        self.provider = provider or factory.build_provider(
            self.config, self._system, provider_name
        )
        # Cumulative across provider switches — the point of tracking cost is
        # comparing vendors, which needs the total to survive a switch.
        self.usage = Usage()
        # What the user has said this session, oldest first. Used to fold
        # turns into an episode before the provider drops them — see
        # _remember_dropped_turns.
        self._session_turns: list[str] = []
        # UI hooks, wired up by main.py in text mode for the live status
        # line. Both stay None in voice mode and in tests — nothing here
        # requires them to be set.
        self.progress_hook: Callable[[str, ToolCall | None], None] | None = None
        self.retry_hook: Callable[[str, int, int, float], None] | None = None

    # ------------------------------------------------------------------ public
    @property
    def provider_name(self) -> str:
        return self.provider.name

    @property
    def model(self) -> str:
        return self.provider.model

    def reset(self) -> None:
        """Start a fresh conversation, keeping memory and usage totals."""
        self.provider.reset()

    def close(self) -> None:
        """Release provider-side resources. Safe to call more than once."""
        try:
            self.provider.close()
        except Exception:  # pragma: no cover - shutdown must not raise
            log.debug("provider close failed", exc_info=True)

    def switch_provider(self, name: str) -> str:
        """Move to another vendor mid-session.

        The conversation does not carry over: three incompatible history
        formats cannot be losslessly translated, and a half-converted history
        is worse than a clean start. Long-term memory and usage totals do
        carry over, which is what actually matters.
        """
        previous = f"{self.provider.name}/{self.provider.model}"
        self.usage.add(self.provider.usage)
        # Release the old provider's prompt cache — otherwise switching away
        # from Gemini leaves storage billing for a cache nothing will use.
        self.provider.close()

        self.provider = factory.build_provider(self.config, self._system, name)
        log.info("switched provider: %s -> %s/%s",
                 previous, self.provider.name, self.provider.model)
        return f"Switched from {previous} to {self.provider.name}/{self.provider.model}."

    def ask(self, user_text: str) -> TurnResult:
        """Run one full turn and return what should be spoken."""
        self._session_turns.append(user_text)
        self._remember_dropped_turns()
        self.provider.trim(self.config.agent.max_history_messages)

        result = loop.run_turn(
            provider=self.provider,
            tools=registry.tool_specs(),
            user_text=self._build_user_content(user_text),
            execute=self._execute,
            max_pause_restarts=self.config.agent.max_pause_restarts,
            retry_attempts=self.config.agent.retry.max_attempts,
            retry_base_delay=self.config.agent.retry.base_delay_seconds,
            retry_max_delay=self.config.agent.retry.max_delay_seconds,
            on_retry=self._on_retry,
            on_progress=self.progress_hook,
        )

        self.provider.usage.turns += 1
        return TurnResult(
            text=result.text,
            tool_calls=result.tool_calls,
            stop_reason=result.stop_reason,
        )

    def usage_summary(self) -> str:
        """Session totals, including any provider used earlier."""
        total = Usage(
            input_tokens=self.usage.input_tokens,
            output_tokens=self.usage.output_tokens,
            cache_read=self.usage.cache_read,
            cache_write=self.usage.cache_write,
            turns=self.usage.turns,
            cost_usd=self.usage.cost_usd,
        )
        total.add(self.provider.usage)
        total.turns += self.provider.usage.turns
        return total.summary(
            self.provider.name, self.provider.model, self.config.agent.usd_to_inr_rate
        )

    # ----------------------------------------------------------------- private
    def _remember_dropped_turns(self) -> None:
        """Fold turns about to age out of history into a stored episode.

        Conversation history is the one part of a request that cannot be
        cached — it changes every turn — so it is pure marginal cost, and
        `provider.trim` bounds it by simply discarding the oldest turns. That
        silently loses context: ask something in turn 2 and refer back to it
        in turn 40 and Peter has no idea what you mean.

        Recording them as an episode keeps the gist reachable, because
        `memory.context_block` already injects recent episodes back into each
        turn. This is deliberately extractive — the alternative, asking a
        model to summarise, would spend tokens to save tokens.
        """
        keep = max(2, self.config.agent.max_history_messages // 2)
        if len(self._session_turns) <= keep:
            return

        dropped = self._session_turns[:-keep]
        self._session_turns = self._session_turns[-keep:]
        gist = "; ".join(t.strip().replace("\n", " ")[:120] for t in dropped if t.strip())
        if gist:
            self.memory.add_episode(f"Earlier this session, {self.config.app.user_name} asked: {gist}")

    def _on_retry(self, attempt: int, attempts: int, delay: float,
                   error: BaseException) -> None:
        """Announce a transient provider failure, spoken and printed alike.

        `services().say()` already handles both — voice mode speaks it,
        text/CLI mode prints it — so this needs no mode-awareness of its own.
        """
        log.warning("provider retry %d/%d after %r", attempt, attempts, error)
        if self.retry_hook:
            self.retry_hook(self.provider.name, attempt, attempts, delay)
        services().say(
            f"{self.provider.name} isn't responding (attempt {attempt} of "
            f"{attempts}). Retrying in {delay:.0f} seconds..."
        )

    def _execute(self, call: ToolCall) -> str:
        """Run one tool through the registry, so the permission gate applies.

        Every vendor SDK ships an auto-executing tool runner. Using any of them
        would run tools without passing the gate — which is the one thing the
        gate exists to prevent. This is the only path tools are ever called on.
        """
        record = registry.get_record(call.name)
        if record is None:
            log.warning("model asked for unknown tool %r", call.name)
            return (
                f"There is no tool called {call.name!r}. "
                "Use one of the tools you were given."
            )
        return str(record.sdk_tool.call(call.arguments))

    def _build_user_content(self, user_text: str) -> str:
        """Volatile context goes here, never in the system prompt.

        The current time and injected memories change every turn. In the system
        prompt they would invalidate the cached prefix on every single request —
        the most expensive mistake available here, and it fails silently.
        """
        now = datetime.now().astimezone().strftime("%A, %d %B %Y, %I:%M %p")
        parts = [f"<now>{now}</now>"]

        block = self.memory.context_block(user_text)
        if block:
            parts.append(block)

        parts.append(user_text)
        return "\n\n".join(parts)
