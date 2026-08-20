"""Permission gate.

Every tool call passes through here before its body runs. The gate decides
allow / confirm / handoff / deny from the `policy` section of config.yml, asks
the human when it must, and writes an audit line either way.

A refusal is **returned as a normal tool result**, not raised. Claude sees "the
user declined" and can adapt — apologise, offer an alternative, ask a clarifying
question. Raising would abort the turn and leave the user staring at a traceback
instead of hearing an answer.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from peter.core.config import PolicyConfig
from peter.core.errors import IntegrationError, PeterError
from peter.policy.audit import AuditLog

ALLOW = "allow"
CONFIRM = "confirm"
HANDOFF = "handoff"
DENY = "deny"

DECLINED_MESSAGE = (
    "The user declined this action. Do not retry it. Acknowledge briefly and "
    "ask what they would like instead."
)

HANDOFF_MESSAGE = (
    "This action would spend money, so it cannot be completed automatically. "
    "Indian regulation (RBI two-factor authentication) requires the user to "
    "authorise the payment themselves. Summarise what is ready and tell the "
    "user exactly what to tap to finish."
)


class Confirmer(Protocol):
    """Anything that can ask the human a yes/no question."""

    def ask(self, prompt: str, timeout: float) -> bool: ...


class AlwaysDeny:
    """Default confirmer — used when no UI is wired up yet.

    Fails closed on purpose. A misconfigured Peter that refuses to delete files
    is a nuisance; one that deletes them without asking is a disaster.
    """

    def ask(self, prompt: str, timeout: float) -> bool:  # noqa: ARG002
        return False


class AutoApprove:
    """Confirmer for the text-mode smoke test and unit tests."""

    def __init__(self, answer: bool = True):
        self.answer = answer
        self.asked: list[str] = []

    def ask(self, prompt: str, timeout: float) -> bool:  # noqa: ARG002
        self.asked.append(prompt)
        return self.answer


class ConsoleConfirmer:
    """Reads y/n from stdin. Used by `python -m peter.main --text`.

    `suspend` is an optional no-arg callable pair-ish hook — really a context
    manager factory — that pauses whatever is animating the console (the
    "thinking" spinner) for the duration of the prompt. Without it, a Rich
    Status redrawing mid-input mangles both the "[y/N]" prompt and whatever
    the user types, visible in practice as the answer landing inside the
    spinner glyphs instead of after the prompt.
    """

    def __init__(self, suspend=None):
        self._suspend = suspend or _no_suspend

    def ask(self, prompt: str, timeout: float) -> bool:  # noqa: ARG002
        with self._suspend():
            try:
                reply = input(f"\n[confirm] {prompt}\n  proceed? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
        return reply in ("y", "yes")


@contextmanager
def _no_suspend():
    yield


@dataclass
class Policy:
    """Runtime view of the `policy` section of config.yml."""

    default_tiers: dict[str, str] = field(
        default_factory=lambda: {"read": ALLOW, "write": CONFIRM, "spend": HANDOFF}
    )
    overrides: dict[str, str] = field(default_factory=dict)
    confirm_timeout_seconds: float = 45.0

    @classmethod
    def from_config(cls, config: PolicyConfig) -> "Policy":
        return cls(
            default_tiers={**cls().default_tiers, **config.default_tiers},
            overrides=dict(config.standing_rules),
            confirm_timeout_seconds=config.confirm_timeout_seconds,
        )

    def decide(self, tool_name: str, tier: str) -> str:
        """Per-tool override wins over the tier default."""
        if tool_name in self.overrides:
            return self.overrides[tool_name]
        return self.default_tiers.get(tier, CONFIRM)


def _summarise_args(kwargs: dict) -> str:
    if not kwargs:
        return ""
    parts = []
    for key, value in kwargs.items():
        text = str(value)
        if len(text) > 80:
            text = text[:80] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


class PolicyGate:
    """Installed as the registry interceptor by main.py."""

    def __init__(
        self,
        policy: Policy,
        audit: AuditLog,
        confirmer: Confirmer | None = None,
    ):
        self.policy = policy
        self.audit = audit
        self.confirmer: Confirmer = confirmer or AlwaysDeny()

    def set_confirmer(self, confirmer: Confirmer) -> None:
        self.confirmer = confirmer

    def __call__(self, name: str, tier: str, fn: Callable, kwargs: dict) -> Any:
        decision = self.policy.decide(name, tier)

        if decision == DENY:
            self.audit.record(tool=name, tier=tier, decision=DENY, args=kwargs)
            return f"Blocked by policy: the tool {name!r} is disabled."

        if decision == HANDOFF:
            self.audit.record(tool=name, tier=tier, decision=HANDOFF, args=kwargs)
            return HANDOFF_MESSAGE

        if decision == CONFIRM:
            prompt = f"{name}({_summarise_args(kwargs)})"
            approved = self.confirmer.ask(prompt, self.policy.confirm_timeout_seconds)
            if not approved:
                self.audit.record(
                    tool=name, tier=tier, decision="declined", args=kwargs
                )
                # A confirmer may explain its own refusal — the remote one
                # declines because nobody is at the machine, which is a
                # different thing to say than "the user declined".
                return getattr(self.confirmer, "decline_message", DECLINED_MESSAGE)

        return self._execute(name, tier, decision, fn, kwargs)

    def _execute(
        self, name: str, tier: str, decision: str, fn: Callable, kwargs: dict
    ) -> Any:
        started = time.perf_counter()
        try:
            result = fn(**kwargs)
        except IntegrationError as exc:
            # An unreachable mail server is an ordinary outcome, not a crash.
            # Claude gets a sentence it can say, including what the user should
            # do about it — never a stack trace.
            self._record_error(name, tier, decision, kwargs, started, exc)
            return exc.spoken()
        except PeterError as exc:
            self._record_error(name, tier, decision, kwargs, started, exc)
            return f"That did not work: {exc}"
        except Exception as exc:
            self._record_error(name, tier, decision, kwargs, started, exc)
            return f"Error running {name}: {type(exc).__name__}: {exc}"

        elapsed = (time.perf_counter() - started) * 1000
        self.audit.record(
            tool=name,
            tier=tier,
            decision=decision,
            args=kwargs,
            result_summary=str(result),
            duration_ms=elapsed,
        )
        return result

    def _record_error(
        self,
        name: str,
        tier: str,
        decision: str,
        kwargs: dict,
        started: float,
        exc: BaseException,
    ) -> None:
        self.audit.record(
            tool=name,
            tier=tier,
            decision=decision,
            args=kwargs,
            duration_ms=(time.perf_counter() - started) * 1000,
            error=f"{type(exc).__name__}: {exc}",
        )
