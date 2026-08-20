"""Tool registry.

This is the keystone of Peter 3.0. peter_1.0 grew by adding another
`if "keyword" in mytext:` branch; Peter 3.0 grows by writing one function and
decorating it. Claude reads the function's docstring and type hints to decide
when to call it, so **the docstring is the prompt** — write it for a reader who
knows nothing about the code.

    @peter_tool(tier="read")
    def get_time(timezone: str = "local") -> str:
        '''Get the current date and time.

        Args:
            timezone: Either "local" or an IANA name like "Asia/Kolkata".
        '''
        ...

Tiers drive the permission gate (see peter/policy/gate.py):
    read   - observes the world, changes nothing
    write  - changes state on this machine or sends something outward
    spend  - commits money; never auto-executed

Every registered tool is wrapped so that calls pass through an *interceptor*
before the body runs. main.py installs the policy gate as that interceptor; in
tests it stays unset and tools run directly. This keeps the registry free of any
dependency on the policy layer, which would otherwise be a circular import.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Iterable, Literal

from anthropic import beta_tool

Tier = Literal["read", "write", "spend"]
VALID_TIERS = ("read", "write", "spend")

# name -> registration record
_REGISTRY: dict[str, "ToolRecord"] = {}

# Signature: (name, tier, fn, kwargs) -> result
Interceptor = Callable[[str, str, Callable, dict], Any]
_interceptor: Interceptor | None = None


class ToolRecord:
    """A registered tool: the SDK-wrapped callable plus Peter's metadata."""

    __slots__ = ("name", "tier", "sdk_tool", "raw_fn", "_spec")

    def __init__(self, name: str, tier: str, sdk_tool: Any, raw_fn: Callable):
        self.name = name
        self.tier = tier
        self.sdk_tool = sdk_tool
        self.raw_fn = raw_fn
        self._spec = None

    @property
    def spec(self):
        """Provider-neutral schema, derived once and cached.

        The Anthropic SDK does the signature-and-docstring inference; the result
        is plain JSON Schema, which is what all three vendors want. Anthropic is
        the derivation engine here, not the privileged provider.
        """
        if self._spec is None:
            from peter.llm.base import ToolSpec

            self._spec = ToolSpec.from_anthropic_dict(self.sdk_tool.to_dict())
        return self._spec

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ToolRecord {self.name} tier={self.tier}>"


def set_interceptor(fn: Interceptor | None) -> None:
    """Install the function every tool call is routed through (or None)."""
    global _interceptor
    _interceptor = fn


def peter_tool(tier: Tier) -> Callable[[Callable], Any]:
    """Register a function as a tool Claude can call."""
    if tier not in VALID_TIERS:
        raise ValueError(f"tier must be one of {VALID_TIERS}, got {tier!r}")

    def decorator(fn: Callable) -> Any:
        name = fn.__name__

        @functools.wraps(fn)
        def gated(**kwargs: Any) -> Any:
            if _interceptor is None:
                return fn(**kwargs)
            return _interceptor(name, tier, fn, kwargs)

        # beta_tool derives the JSON schema from the signature and docstring.
        # functools.wraps copies __doc__, __name__ and __annotations__, and sets
        # __wrapped__ so inspect.signature() sees through to the real function.
        sdk_tool = beta_tool(gated)
        resolved = getattr(sdk_tool, "name", None) or name
        if resolved in _REGISTRY:
            raise ValueError(f"duplicate tool name: {resolved!r}")
        _REGISTRY[resolved] = ToolRecord(
            name=resolved, tier=tier, sdk_tool=sdk_tool, raw_fn=fn
        )
        return sdk_tool

    return decorator


def get_record(name: str) -> ToolRecord | None:
    return _REGISTRY.get(name)


def tier_of(name: str) -> str | None:
    record = _REGISTRY.get(name)
    return record.tier if record else None


def all_records() -> list[ToolRecord]:
    """Records sorted by name.

    Order matters: the tool list is part of the cached prompt prefix, so a
    non-deterministic order silently voids the cache on every request.
    """
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def tool_specs() -> list[Any]:
    """Provider-neutral schemas for every registered tool, in stable order.

    Order matters: the tool list is part of the cached prompt prefix on every
    vendor, so a non-deterministic order silently voids the cache.
    """
    return [r.spec for r in all_records()]


def sdk_tools(extra: Iterable[Any] = ()) -> list[Any]:
    """Anthropic-SDK tool objects. Retained for tests that call tools directly."""
    return [r.sdk_tool for r in all_records()] + list(extra)


def describe() -> list[dict[str, str]]:
    """Flat summary, for tests, docs, and the tray's tool inspector."""
    return [{"name": r.name, "tier": r.tier} for r in all_records()]


def tool_manifest() -> str:
    """A compact `name [tier]` listing, injected into the system prompt.

    Claude already gets full schemas via the tools parameter; this exists so it
    knows which actions will stop and ask before running.
    """
    return "\n".join(f"- {r.name} [{r.tier}]" for r in all_records())


# Tool modules, in import order. Adding a module here is the only wiring a new
# capability needs — registration happens as an import side effect.
TOOL_MODULES = (
    "peter.tools.system",
    "peter.tools.time_tools",
    "peter.tools.focus_tools",
    "peter.tools.memory_tools",
    "peter.tools.mail_tools",
    "peter.tools.calendar_tools",
    "peter.tools.contacts_tools",
    "peter.tools.keep_tools",
    "peter.tools.briefing_tools",
    "peter.tools.browser_tools",
    "peter.tools.desktop_tools",
    "peter.tools.llm_tools",
    "peter.tools.vision_tools",
    "peter.tools.watch_tools",
    "peter.tools.workspace_tools",
    "peter.tools.docs_tools",
    "peter.tools.recorder_tools",
    "peter.tools.dev_tools",
    "peter.tools.telegram_tools",
    "peter.tools.phone_tools",
    "peter.tools.expense_tools",
    "peter.tools.delivery_tools",
    "peter.tools.weather_tools",
    "peter.tools.routine_tools",
    "peter.tools.news_tools",
    "peter.tools.notes_tools",
    "peter.tools.perf_tools",
    "peter.tools.skill_tools",
)

# Modules whose tools cannot possibly succeed without credentials, and the
# check that decides. Every tool schema is re-sent on every API call, so a
# module listed here and not configured is pure cost: ~1,000 tokens per
# request describing an action that can only fail.
_REQUIRES = {
    "peter.tools.mail_tools":
        lambda c: c.integrations.mail.enabled and c.secrets.has_mail,
    "peter.tools.calendar_tools":
        lambda c: c.integrations.google.enabled and c.secrets.has_google,
    "peter.tools.contacts_tools":
        lambda c: c.integrations.google.enabled and c.secrets.has_google,
    # Off unless you've read the setup doc and opted in — see KeepConfig's
    # own docstring for why this integration defaults differently from
    # every other one here.
    "peter.tools.keep_tools":
        lambda c: c.integrations.keep.enabled and c.secrets.has_keep,
    "peter.tools.desktop_tools":
        lambda c: c.integrations.desktop.enabled,
    # Needs a repository to look at; with none configured every one of these
    # can only answer "no repositories are configured".
    "peter.tools.dev_tools":
        lambda c: c.integrations.dev.enabled and bool(c.integrations.dev.repos),
    # Needs both a token and somewhere to send to.
    "peter.tools.telegram_tools":
        lambda c: (c.integrations.telegram.enabled and c.secrets.has_telegram
                   and bool(c.integrations.telegram.allowed_chat_ids)),
    # Off unless USB debugging is deliberately set up.
    "peter.tools.phone_tools":
        lambda c: c.integrations.phone.enabled,
    "peter.tools.recorder_tools":
        lambda c: c.integrations.recorder.enabled,
    "peter.tools.watch_tools":
        lambda c: c.integrations.price_watch.enabled and c.integrations.browser.enabled,
    "peter.tools.workspace_tools":
        lambda c: c.integrations.workspace.enabled,
    "peter.tools.docs_tools":
        lambda c: c.integrations.docs.enabled,
    "peter.tools.vision_tools":
        lambda c: c.agent.vision.enabled,
    # Both read SMS over ADB, so neither can do anything without the phone
    # integration also switched on.
    "peter.tools.expense_tools":
        lambda c: c.integrations.expenses.enabled and c.integrations.phone.enabled,
    "peter.tools.delivery_tools":
        lambda c: c.integrations.deliveries.enabled and c.integrations.phone.enabled,
    "peter.tools.weather_tools":
        lambda c: c.integrations.weather.enabled,
    # A routine with no steps configured can only ever say so — same reasoning
    # as dev_tools needing at least one repo.
    "peter.tools.routine_tools":
        lambda c: c.integrations.routines.enabled and bool(c.integrations.routines.defs),
    "peter.tools.news_tools":
        lambda c: c.integrations.news.enabled,
    "peter.tools.notes_tools":
        lambda c: c.integrations.notes.enabled,
}


def usable_modules(config, modules: Iterable[str] = TOOL_MODULES) -> list[str]:
    """The subset of `modules` whose integration is actually set up.

    Note what is deliberately *not* gated here. Browser tools stay registered
    even with no saved login, because they genuinely work on public pages —
    dropping a tool that can succeed would cost accuracy to save tokens, which
    is the wrong trade. Only credential-gated modules are dropped.
    """
    return [m for m in modules if _REQUIRES.get(m, lambda _c: True)(config)]


def load_all_tools(
    modules: Iterable[str] = TOOL_MODULES, config=None
) -> None:
    """Import tool modules so their decorators run.

    Registration happens at import time, so something has to do the importing.
    Doing it in one place keeps main.py from growing a pile of seemingly-unused
    imports that linters will try to delete.

    With `config`, modules whose credentials are missing are skipped entirely,
    so their schemas never reach the model. Without it every module loads —
    which is what the test suite wants, and what keeps this callable from
    places that have no config to hand.

    The original design registered everything unconditionally, reasoning that a
    hidden tool would make Peter claim it *cannot* read email when the honest
    answer is "you have not set it up yet". That reasoning still holds and is
    handled — `prompts.py` states plainly which integrations are unconfigured,
    so Peter can say so without carrying ~2,000 tokens of unusable schema on
    every single request to do it.
    """
    import importlib

    chosen = list(modules) if config is None else usable_modules(config, modules)
    for module in chosen:
        importlib.import_module(module)


def reset_for_tests() -> None:
    """Clear the registry and interceptor, and make the tool modules re-importable.

    Registration happens as an import side effect, and Python caches modules —
    so simply clearing the dict would leave `load_all_tools()` a silent no-op
    and every subsequent test seeing an empty registry. Dropping the modules
    from sys.modules is what makes the reset actually a reset.
    """
    import sys

    from peter.agent import skills

    _REGISTRY.clear()
    skills.reset_for_tests()
    set_interceptor(None)
    for name in [m for m in sys.modules if m.startswith("peter.tools")]:
        del sys.modules[name]
