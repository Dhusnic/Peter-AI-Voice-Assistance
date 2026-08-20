"""Focus mode — a timed, distraction-muted work block.

Thin wrappers over peter.focus, which owns the actual state (the scheduled
restore, in particular). Kept here only so the tool-facing surface matches
every other tool module: plain functions with real docstrings Claude reads
to decide when to call them, gated through the same registry as everything
else.
"""

from __future__ import annotations

from peter import focus
from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill

register_skill(SkillManifest(
    name="focus", version="1.0.0",
    description="Timed, distraction-muted work blocks.",
    module=__name__, permissions=(),
    tools=("start_focus_session", "end_focus_session", "focus_status"),
))


@peter_tool(tier="write")
def start_focus_session(minutes: float = 50.0, label: str = "") -> str:
    """Start a timed focus session: mutes system volume, restores it when done.

    Use for "start a focus session", "give me 90 minutes to work on X", a
    Pomodoro-style block, or similar. Only one session runs at a time — check
    focus_status first if you are not sure one is already running.

    Args:
        minutes: How long, in minutes. Defaults to 50.
        label: What it's for, e.g. "the OpenObserve migration" — read back in
            the confirmation and in the end-of-session summary.
    """
    return focus.start(minutes, label)


@peter_tool(tier="write")
def end_focus_session() -> str:
    """End the current focus session early and restore the system volume."""
    return focus.end()


@peter_tool(tier="read")
def focus_status() -> str:
    """Report whether a focus session is running and how much time is left."""
    return focus.status()
