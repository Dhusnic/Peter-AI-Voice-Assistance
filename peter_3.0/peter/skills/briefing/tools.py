"""The briefing, exposed as a tool.

Thin on purpose — the assembly lives in peter/briefing.py so the scheduled job
and the on-request tool cannot drift apart.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.briefing import build_briefing, next_briefing_time
from peter.core.services import services

register_skill(SkillManifest(
    name="briefing", version="1.0.0",
    description="On-demand morning briefing status.",
    module=__name__, permissions=(),
    tools=("daily_briefing", "briefing_schedule"),
))


@peter_tool(tier="read")
def daily_briefing() -> str:
    """Summarise today: calendar, unread mail, reminders and to-dos.

    Use this when the user asks anything like "what's my day look like",
    "brief me", "what's happening today", or "catch me up". Read the result back
    close to as written — it is already phrased for speech.
    """
    return build_briefing()


@peter_tool(tier="read")
def briefing_schedule() -> str:
    """Report when the automatic daily briefing is set to run."""
    config = services().config
    cfg = config.integrations.briefing
    if not cfg.enabled:
        return "The automatic briefing is turned off in config.yml."
    when = next_briefing_time(config)
    return (
        f"The daily briefing runs at {cfg.time}. "
        f"Next one is {when.strftime('%A at %I:%M %p').replace(' 0', ' ')}."
    )
