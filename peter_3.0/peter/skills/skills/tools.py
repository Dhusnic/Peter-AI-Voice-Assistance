"""Skill listing — what capability packages Peter has, and which are usable.

See peter/agent/skills.py for what a skill manifest actually is. This module
is itself a skill about skills, mostly so `--skill-list` and `list_skills`
share one formatter rather than main.py reimplementing it.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill, skills_report
from peter.core.services import services

register_skill(SkillManifest(
    name="skills", version="1.0.0",
    description="Lists every skill Peter has and whether it's currently usable.",
    module=__name__, permissions=(), tools=("list_skills",),
))


@peter_tool(tier="read")
def list_skills() -> str:
    """List every skill currently registered: what it does, its tools, and
    whether it's fully usable right now versus needing credentials or config
    it doesn't have yet.

    Only covers skills whose module was actually loaded this session — one
    gated entirely off by missing credentials (see config.yml) is not
    registered at all and won't appear here. For the complete catalog
    including those, run `python -m peter.main --skill-list` instead.
    """
    return skills_report(services().config)
