"""Performance reporting — how long Peter's own tools actually take.

Every tool call is timed automatically by the policy gate (see peter/perf.py)
with no per-tool changes needed, so this is purely a reporting layer over
data that was already being collected. `python -m peter.main --perf-report`
gives the detailed table; this tool gives the short, speakable version.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill

register_skill(SkillManifest(
    name="performance", version="1.0.0",
    description="Per-tool timing report: busiest tools, native-rewrite candidates.",
    module=__name__, permissions=(),
    tools=("performance_report",),
))


@peter_tool(tier="read")
def performance_report(hours: int = 168) -> str:
    """Summarise how long Peter's tools have actually been taking.

    Reports the busiest tools by total time spent, and flags any tool whose
    own CPU time (not time spent waiting on a network call) is both large in
    absolute terms and most of what the call takes — the two-factor bar for
    "this might be worth rewriting in a faster language," which for a
    personal assistant is rare. For the full per-tool table with
    percentiles, run `python -m peter.main --perf-report` instead.

    Args:
        hours: How far back to summarise. Default one week.
    """
    from peter import perf

    return perf.spoken_summary(hours=hours)
