"""Memory tools — how Claude reads and writes Peter's long-term memory.

These are plain registered tools rather than the SDK's file-based memory tool,
so that every memory write goes through the same policy gate and audit log as
everything else. A memory write is a `write`-tier action for a reason: an
assistant that silently rewrites what it believes about you is worse than one
that asks.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.core.services import services


@peter_tool(tier="write")
def remember_fact(key: str, value: str) -> str:
    """Store a durable fact about the user so it survives across conversations.

    Use this for stable information worth recalling weeks later: where they
    study or work, family names, their usual grocery order, their bus route
    home. Do not use it for one-off details that only matter in this
    conversation.

    Args:
        key: Short stable identifier, lowercase with underscores, e.g.
            "home_city" or "usual_groceries". Reusing an existing key
            overwrites that fact.
        value: The fact itself, written as a complete short sentence.
    """
    services().require_memory().set_fact(key, value)
    return f"Remembered {key}."


@peter_tool(tier="read")
def recall(query: str) -> str:
    """Search stored facts about the user.

    Relevant facts are already injected into each turn automatically, so only
    call this when you need something that was not injected — for example when
    the user asks "what do you know about me?" or refers to a detail you cannot
    see.

    Args:
        query: Words to search for, e.g. "grocery" or "college".
    """
    hits = services().require_memory().search_facts(query, limit=10)
    if not hits:
        return "No stored facts match that."
    return "\n".join(f"{k}: {v}" for k, v in hits)


@peter_tool(tier="write")
def forget_fact(key: str) -> str:
    """Delete a stored fact permanently.

    Args:
        key: The exact key of the fact to remove.
    """
    ok = services().require_memory().delete_fact(key)
    return f"Forgot {key}." if ok else f"No fact stored under {key!r}."


@peter_tool(tier="write")
def set_preference(key: str, value: str) -> str:
    """Record a standing instruction about how you should behave.

    Preferences are injected into every future conversation, so keep them few
    and general. Examples: reply length, tone, preferred units, which shop to
    default to.

    Args:
        key: Short identifier, e.g. "reply_length" or "default_grocery_app".
        value: The instruction, e.g. "keep spoken replies under two sentences".
    """
    services().require_memory().set_preference(key, value)
    return f"Preference {key} set."


@peter_tool(tier="read")
def list_preferences() -> str:
    """List every standing preference currently in effect."""
    prefs = services().require_memory().all_preferences()
    if not prefs:
        return "No preferences set."
    return "\n".join(f"{k}: {v}" for k, v in prefs)


@peter_tool(tier="write")
def forget_preference(key: str) -> str:
    """Remove a standing preference.

    Args:
        key: The exact key of the preference to remove.
    """
    ok = services().require_memory().delete_preference(key)
    return f"Removed preference {key}." if ok else f"No preference named {key!r}."
