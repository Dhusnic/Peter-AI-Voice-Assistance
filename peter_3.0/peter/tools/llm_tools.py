"""Switching and inspecting the LLM provider.

Peter runs on Claude, GPT or Gemini. Being able to change that by voice makes
the choice cheap to revisit — "you're being slow, switch to Gemini" is a
reasonable thing to say to an assistant, and comparing cost across vendors on
the same day's work is the only honest way to pick one.

The Brain object is reachable through the service container, which is set up by
main.py. In text-only tests where no Brain exists, these report that rather
than raising.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.services import services
from peter.llm import factory, pricing

register_skill(SkillManifest(
    name="llm", version="1.0.0",
    description="Switch/inspect the active LLM provider, and the spend report.",
    module=__name__, permissions=("network",),
    tools=("switch_llm_provider", "llm_status", "spend_report"),
))


@peter_tool(tier="write")
def switch_llm_provider(provider: str) -> str:
    """Change which AI model provider answers from now on.

    Valid providers are "anthropic" (Claude), "openai" (GPT) and "gemini".
    The current conversation restarts — the three vendors use incompatible
    history formats and cannot be translated between mid-conversation — but
    stored memory and the session's running cost carry over. Say that the
    conversation is starting fresh so the user is not surprised.

    Args:
        provider: One of "anthropic", "openai", "gemini".
    """
    brain = services().brain
    if brain is None:
        return "No agent is running, so there is nothing to switch."

    name = provider.strip().lower()
    aliases = {
        "claude": "anthropic", "chatgpt": "openai", "gpt": "openai",
        "google": "gemini", "bard": "gemini",
    }
    name = aliases.get(name, name)

    if name not in factory.PROVIDERS:
        return f"Unknown provider {provider!r}. Choose anthropic, openai, or gemini."
    if name == brain.provider_name:
        return f"Already using {name}/{brain.model}."

    if not factory.api_key_for(services().config, name):
        return (
            f"No API key for {name}. Add the key to .env, then ask again."
        )

    return brain.switch_provider(name)


@peter_tool(tier="read")
def llm_status() -> str:
    """Report which AI provider and model are in use, and what the session cost.

    Use this when the user asks which model they are talking to, how much this
    is costing, or which providers are set up.
    """
    container = services()
    brain = container.brain
    if brain is None:
        return "No agent is running."

    configured = factory.available(container.config)
    lines = [
        f"Running on {brain.provider_name}, model {brain.model}.",
        brain.usage_summary(),
    ]

    if getattr(brain.provider, "auto", False):
        reason = getattr(brain.provider, "last_route_reason", "") or "no turn yet"
        lines.append(
            f"Smart model routing is on for gemini: last turn used "
            f"{brain.model} ({reason})."
        )

    others = [p for p in configured if p != brain.provider_name]
    if others:
        lines.append(f"Also available: {', '.join(others)}.")

    missing = [p for p in factory.PROVIDERS if p not in configured]
    if missing:
        lines.append(f"No key configured for: {', '.join(missing)}.")

    if not pricing.is_known(brain.model):
        lines.append(
            f"Cost for {brain.model} is not in the price table, so the total "
            "above undercounts."
        )
    return "\n".join(lines)


@peter_tool(tier="read")
def spend_report(days: int = 7) -> str:
    """Report what Peter has cost in rupees, per day and per model.

    Use this for anything about running cost over time — "how much have I
    spent this week", "is Gemini actually cheaper", "what did today cost".
    For the current session only, llm_status is enough.

    Args:
        days: How far back to total. 1 is today.
    """
    from peter import spend

    return spend.report(days)
