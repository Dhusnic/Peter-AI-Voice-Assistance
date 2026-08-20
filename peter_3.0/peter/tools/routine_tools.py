"""Routine tools — chaining Peter's own tools into one voice command.

A routine is defined by hand in config.yml (integrations.routines.defs), not
built up through conversation — see peter/routines.py for what it is and why
its steps do not re-confirm individually.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.services import services

register_skill(SkillManifest(
    name="routines", version="1.0.0",
    description="Named chains of Peter's own tools, run as one voice command.",
    module=__name__, permissions=(),
    tools=("run_routine", "list_routines"),
))


@peter_tool(tier="write")
def run_routine(name: str) -> str:
    """Run a named routine: a saved chain of Peter's own tools, one after another.

    Use this when the user's instruction maps to a routine already configured
    in config.yml, e.g. "run my good night routine" or "start work mode". Do
    not invent a routine name that was not actually configured — call
    list_routines first if unsure what exists.

    Args:
        name: The routine's name, e.g. "good night" or "start work".
    """
    from peter import routines

    return routines.run(name, services().config)


@peter_tool(tier="read")
def list_routines() -> str:
    """List every configured routine and the tools each one runs, in order."""
    from peter import routines

    return routines.describe(services().config)
