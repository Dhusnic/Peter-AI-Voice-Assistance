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


def _usage_snapshot(usage: Usage) -> dict:
    """Cumulative counters, copied so a turn's own consumption can be derived.

    Providers accumulate usage across the whole session rather than reporting
    per call, so the only way to get one turn's cost is to subtract.
    """
    return {
        "cost_usd": usage.cost_usd,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read": usage.cache_read,
        "cache_write": usage.cache_write,
    }


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
        self.provider.on_fallback = self._on_fallback
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
        self.fallback_hook: Callable[[str, str], None] | None = None
        # Set once the daily cap has been mentioned, so a `warn` budget says
        # something the first time it is passed and not on every turn after.
        self._budget_warned = False
        # The previous (user_text, reply) pair, so a turn that reads like a
        # correction has something to be a correction *of*. See _learn().
        self._last_exchange: tuple[str, str] | None = None

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
        self.provider.on_fallback = self._on_fallback
        log.info("switched provider: %s -> %s/%s",
                 previous, self.provider.name, self.provider.model)
        return f"Switched from {previous} to {self.provider.name}/{self.provider.model}."

    def ask(self, user_text: str) -> TurnResult:
        """Run one full turn and return what should be spoken."""
        refusal = self._check_budget()
        if refusal is not None:
            return refusal

        self._session_turns.append(user_text)
        self._remember_dropped_turns()
        self.provider.trim(self.config.agent.max_history_messages)

        before = _usage_snapshot(self.provider.usage)
        result = loop.run_turn(
            provider=self.provider,
            tools=self._turn_tools(user_text),
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
        self._record_spend(before)

        spoken = result.text
        note = self._learn(user_text)
        if note:
            spoken = f"{spoken} {note}".strip()
        # Stored without the announcement: what the user is correcting is the
        # answer they actually got, not Peter's note about having learned.
        self._last_exchange = (user_text, result.text)

        return TurnResult(
            text=spoken,
            tool_calls=result.tool_calls,
            stop_reason=result.stop_reason,
        )

    def _learn(self, user_text: str) -> str | None:
        """If this turn corrected the previous one, try to turn that into a
        standing rule. Returns a short announcement, or None.

        Runs after the answer is already in hand, so a slow or failing
        extraction delays nothing the user is waiting on except the note
        itself — and only on a turn that reads like a correction, which the
        keyword pre-filter settles without a model call.
        """
        if self._last_exchange is None:
            return None
        previous_user, previous_reply = self._last_exchange
        try:
            from peter.agent import learning

            return learning.learn_from_correction(
                self.memory, previous_user, previous_reply, user_text, self.config
            )
        except Exception:  # pragma: no cover - learning must never break a turn
            log.debug("learning failed", exc_info=True)
            return None

    def _turn_tools(self, user_text: str):
        """The tool list for this turn — the full registry, unless
        agent.tool_filter is switched on.

        Off by default: see ToolFilterConfig's docstring for why a per-turn-
        varying tool list fights the prompt cache rather than obviously
        saving anything. relevant_tool_names() itself falls back to `None`
        (send everything) on a no-match, so even with filtering enabled a
        turn Claude can't classify never loses access to a tool.
        """
        tools = registry.tool_specs()
        cfg = self.config.agent.tool_filter
        if not cfg.enabled:
            return tools

        from peter.agent import skills

        subset = skills.relevant_tool_names(user_text, cfg)
        if subset is None:
            return tools
        return [t for t in tools if t.name in subset]

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

    # -------------------------------------------------------------- spending
    def _check_budget(self) -> TurnResult | None:
        """Enforce the daily cap, if one is set. Returns a refusal, or None.

        Checked before the turn rather than after, because the only moment a
        cap can actually stop anything is before the request goes out — a
        turn's cost is not knowable until it has already been paid for.
        """
        cfg = self.config.agent.budget
        if cfg.daily_inr <= 0:
            return None

        from peter import spend

        try:
            state = spend.budget_state(self.config, services().spend())
        except Exception:  # a broken ledger must never stop Peter answering
            log.debug("budget check failed", exc_info=True)
            return None

        if not state.exceeded:
            self._budget_warned = False
            return None

        if state.blocked:
            log.warning("budget: blocking turn — %s", state.message())
            return TurnResult(
                text=state.message()
                + " I have stopped for today. Raise agent.budget.daily_inr in "
                "config.yml, or set its action to warn, to carry on."
            )

        if not getattr(self, "_budget_warned", False):
            self._budget_warned = True
            log.warning("budget: %s", state.message())
            services().say(state.message() + " Carrying on anyway.")
        return None

    def _record_spend(self, before: dict) -> None:
        """Append this turn's usage to the ledger. Never fails a turn."""
        usage = self.provider.usage
        try:
            services().spend().record(
                provider=self.provider.name,
                model=self.provider.model,
                cost_usd=usage.cost_usd - before["cost_usd"],
                input_tokens=usage.input_tokens - before["input_tokens"],
                output_tokens=usage.output_tokens - before["output_tokens"],
                cache_read=usage.cache_read - before["cache_read"],
                cache_write=usage.cache_write - before["cache_write"],
            )
        except Exception:
            log.debug("could not record spend for this turn", exc_info=True)

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

    def _on_fallback(self, from_model: str, to_model: str) -> None:
        """Surface a silent same-tier model substitution (Gemini only — see
        GeminiProvider.complete) to the UI. Print-only, not spoken: this can
        fire several times inside one turn, and narrating each one aloud
        would be noise, unlike the outer retry in _on_retry which is rare
        enough to be worth saying."""
        log.info("provider fallback surfaced to UI: %s -> %s", from_model, to_model)
        if self.fallback_hook:
            self.fallback_hook(from_model, to_model)

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
