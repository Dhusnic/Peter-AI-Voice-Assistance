"""Named chains of Peter's own tools, run as one voice command.

"Good night" as a single routine — stop the music, arm tomorrow's alarm, lock
the workstation — is worth more than the tools it calls put together. This is
pure orchestration over the existing tool registry: no new integration, no new
credential, just a config-defined sequence of tools that already exist.

Steps run via each tool's `raw_fn` directly, **bypassing the policy gate**.
That is deliberate, not an oversight: a routine is defined by hand in
config.yml, and writing it there already is the standing instruction — the
same trust model `policy.standing_rules` uses for individual tools. Asking
"proceed?" six times for a routine invoked as a single "run my good night
routine" would defeat the point of naming it. As a second line of defence
regardless, a `spend`-tier tool can never run from a routine step — there are
none registered anywhere in this codebase today, and a routine is exactly the
kind of standing instruction that must never be the thing that adds one.
"""

from __future__ import annotations

import logging

from peter.agent.registry import get_record

log = logging.getLogger(__name__)


def _lookup(defs: dict, name: str) -> tuple[str, list[dict]] | None:
    """Forgiving lookup, the same style as workspace names: "my good night
    routine" should find the routine saved as "good night"."""
    needle = name.strip().lower()
    normalized = {key.strip().lower(): (key, steps) for key, steps in defs.items()}
    if needle in normalized:
        return normalized[needle]
    for key, entry in normalized.items():
        if needle and (needle in key or key in needle):
            return entry
    return None


def run(name: str, config) -> str:
    """Run every step of a configured routine, in order.

    One failed step does not stop the rest — a routine is a convenience list,
    not a transaction. "3 of 4 done, alarm app was not responding" is more
    useful than an all-or-nothing rollback for something this low-stakes.
    """
    defs = config.integrations.routines.defs
    if not defs:
        return (
            "No routines are set up yet. Add one under "
            "integrations.routines.defs in config.yml."
        )
    found = _lookup(defs, name)
    if found is None:
        return f"There is no routine called {name!r}. Configured: {', '.join(sorted(defs))}."

    _, steps = found
    if not steps:
        return f"The {name!r} routine has no steps configured."

    done: list[str] = []
    failed: list[str] = []
    for step in steps:
        tool_name = str(step.get("tool", "")).strip()
        args = step.get("args") or {}
        record = get_record(tool_name)
        if record is None:
            failed.append(f"{tool_name or '(unnamed)'} — not a known tool")
            continue
        if record.tier == "spend":
            failed.append(f"{tool_name} — spend-tier tools cannot run from a routine")
            continue
        try:
            record.raw_fn(**args)
            done.append(tool_name)
        except Exception as exc:
            log.debug("routine step %s failed", tool_name, exc_info=True)
            failed.append(f"{tool_name} — {exc}")

    parts = []
    if done:
        parts.append(f"Ran: {', '.join(done)}.")
    if failed:
        parts.append(f"Could not run: {', '.join(failed)}.")
    return " ".join(parts)


def describe(config) -> str:
    """Every configured routine and the tools each one runs, for list_routines."""
    defs = config.integrations.routines.defs
    if not defs:
        return (
            "No routines are set up yet. Add one under "
            "integrations.routines.defs in config.yml."
        )
    lines = []
    for name in sorted(defs):
        tool_names = ", ".join(str(s.get("tool", "?")) for s in defs[name])
        lines.append(f"{name}: {tool_names}")
    return "\n".join(lines)
