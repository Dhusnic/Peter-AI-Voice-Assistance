"""The agentic loop — one implementation, three vendors.

    user text
        │
        ▼
    provider.complete(tools) ──▶ text? ──▶ done
        │
        ├── tool calls ──▶ execute each ──▶ provider.add_tool_results ──┐
        │                                                               │
        └── pause ──────────────────────────────────────────────────────┤
                                                                        │
        ◀───────────────────────────────────────────────────────────────┘

Keeping this shared is the point of the whole provider abstraction. Each vendor
SDK offers its own auto-executing tool runner, and using three of them would
mean three subtly different behaviours for retries, iteration caps, error
handling and — most importantly — three places where a tool could be executed
without passing through the permission gate.

Tool execution is injected. The loop never imports the registry or the gate; it
calls whatever `execute` it was handed. That is what makes it testable with a
fake provider and no network.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from peter.core.retry import OnRetry, call_with_retry
from peter.llm.base import (
    STOP_LENGTH,
    STOP_PAUSE,
    STOP_REFUSAL,
    STOP_TOOLS,
    LLMProvider,
    ToolCall,
    ToolResult,
    ToolSpec,
)

log = logging.getLogger(__name__)

# Hard cap on round-trips in a single turn. A model that keeps calling tools
# without concluding is either confused or stuck in a loop; either way, stopping
# is better than spending money in a circle.
MAX_ITERATIONS = 12


@dataclass
class TurnResult:
    text: str
    tool_calls: list[str] = field(default_factory=list)
    stop_reason: str = ""
    iterations: int = 0


# (ToolCall) -> result text. Raising is allowed; the loop reports it to the model.
Executor = Callable[[ToolCall], str]


def run_turn(
    provider: LLMProvider,
    tools: list[ToolSpec],
    user_text: str,
    execute: Executor,
    max_iterations: int = MAX_ITERATIONS,
    max_pause_restarts: int = 5,
    retry_attempts: int = 1,
    retry_base_delay: float = 10.0,
    retry_max_delay: float = 60.0,
    on_retry: OnRetry | None = None,
) -> TurnResult:
    """Run one complete turn: send, run tools, repeat until the model finishes.

    Each `provider.complete()` call is individually retried on a transient
    failure (rate limit, momentary outage — anything the provider marks
    `recoverable`). A non-transient failure (bad key, bad request) is never
    retried and propagates on the first attempt. `retry_attempts=1` disables
    retrying entirely, which is what the test suite's fake providers want.
    """
    provider.add_user(user_text)

    called: list[str] = []
    pauses = 0
    response = None

    for iteration in range(1, max_iterations + 1):
        response = call_with_retry(
            lambda: provider.complete(tools),
            attempts=retry_attempts,
            base_delay=retry_base_delay,
            max_delay=retry_max_delay,
            on_retry=on_retry,
        )

        if response.stop_reason == STOP_PAUSE:
            # A server-side tool paused mid-turn. The provider has already
            # stored its partial assistant turn, so re-sending resumes it.
            # Treating this as "finished" yields a silently truncated answer.
            pauses += 1
            if pauses > max_pause_restarts:
                log.warning("turn still paused after %d restarts", max_pause_restarts)
                break
            continue

        if response.stop_reason != STOP_TOOLS or not response.tool_calls:
            return TurnResult(
                text=_final_text(response),
                tool_calls=called,
                stop_reason=response.stop_reason,
                iterations=iteration,
            )

        results: list[ToolResult] = []
        for call in response.tool_calls:
            called.append(call.name)
            results.append(_execute_one(call, execute))
        provider.add_tool_results(results)

    log.warning("turn hit the %d-iteration cap", max_iterations)
    return TurnResult(
        text=_final_text(response) or
        "I got stuck working on that. Ask me again, more specifically?",
        tool_calls=called,
        stop_reason="max_iterations",
        iterations=max_iterations,
    )


def _execute_one(call: ToolCall, execute: Executor) -> ToolResult:
    """Run one tool. A failure becomes a result the model can react to."""
    try:
        output = execute(call)
    except Exception as exc:
        log.exception("tool %s raised", call.name)
        return ToolResult(
            id=call.id,
            name=call.name,
            content=f"Error running {call.name}: {type(exc).__name__}: {exc}",
            is_error=True,
        )

    text = output if isinstance(output, str) else str(output)
    if not text.strip():
        text = "(the tool returned nothing)"
    return ToolResult(id=call.id, name=call.name, content=text)


def _final_text(response) -> str:
    if response is None:
        return "Something went wrong and I did not get a reply."
    if response.stop_reason == STOP_REFUSAL:
        return response.text or "I can't help with that one."
    if response.stop_reason == STOP_LENGTH and not response.text:
        return "That answer got too long for me to finish. Ask for a shorter version?"
    return response.text or "Done."
