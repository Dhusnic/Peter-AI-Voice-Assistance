"""Skills: a manifest layer over the tool registry.

`peter/agent/registry.py`'s `@peter_tool` decorator already turns a function
into something Claude can call; this adds a second, purely descriptive layer
grouping those functions into named capability packages — "weather", "phone",
"system" — each with a version, a short description, and a small set of
advisory permission tags. Every tools module declares one manifest, right
after its imports:

    from peter.agent.skills import SkillManifest, register_skill

    register_skill(SkillManifest(
        name="weather", version="1.0.0",
        description="Current weather via Open-Meteo, no API key.",
        module=__name__, permissions=("network",), tools=("get_weather",),
    ))

**`permissions` is informational only — it enforces nothing.** The actual
enforcement is, and remains, `peter/policy/gate.py`'s interceptor: every tool
call passes through the tier + `standing_rules` + audit log regardless of
which skill registered it, and a skill's manifest cannot grant itself more
access than its own tools' declared tiers already allow. This field exists so
`list_skills`/`--skill-list` can show at a glance what kind of resource a
skill touches — a short, fixed vocabulary: `network`, `filesystem`, `shell`,
`phone`, `browser`. A skill touching none of these (memory, time, notes,
routines, perf) declares `permissions=()`.

`version` is a placeholder `"1.0.0"` on every manifest today. It only means
something once a skill can be updated independently of the rest of this
repository — not yet true, since every skill still ships in the same commit
as everything else.

This module is deliberately Stage 1 of a larger plan (see the architecture
notes): a manifest format that lives inside this codebase, not an installable
package format. There is no loader for skills outside `peter/tools/`, no
registry to fetch one from, and no sandbox to run an untrusted one in — all
three are real infrastructure this does not attempt to fake.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_KNOWN_PERMISSIONS = {"network", "filesystem", "shell", "phone", "browser"}


@dataclass(slots=True, frozen=True)
class SkillManifest:
    name: str
    version: str
    description: str
    module: str
    permissions: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        unknown = set(self.permissions) - _KNOWN_PERMISSIONS
        if unknown:
            raise ValueError(
                f"skill {self.name!r} declares unknown permission(s) {sorted(unknown)}; "
                f"the vocabulary is {sorted(_KNOWN_PERMISSIONS)}"
            )


_SKILLS: dict[str, SkillManifest] = {}


def register_skill(manifest: SkillManifest) -> None:
    if manifest.name in _SKILLS:
        raise ValueError(f"duplicate skill name: {manifest.name!r}")
    _SKILLS[manifest.name] = manifest


def get_skill(name: str) -> SkillManifest | None:
    return _SKILLS.get(name)


def all_skills() -> list[SkillManifest]:
    """Manifests sorted by name — same stability reasoning as
    registry.all_records(): deterministic order for anything that prints
    or iterates this."""
    return [_SKILLS[k] for k in sorted(_SKILLS)]


def reset_for_tests() -> None:
    _SKILLS.clear()


# ------------------------------------------------------------------ reports
def skills_report(config) -> str:
    """Every skill: what it does, its tools, and whether it's currently
    usable. Backs both the `list_skills` tool and `--skill-list`."""
    from peter.agent.registry import usable_modules

    manifests = all_skills()
    if not manifests:
        return "No skills registered."

    usable = set(usable_modules(config))
    lines = []
    for m in manifests:
        status = "enabled" if m.module in usable else "not configured"
        perms = ", ".join(m.permissions) if m.permissions else "none"
        lines.append(
            f"{m.name} ({m.version}) [{status}] — {m.description}\n"
            f"  tools: {', '.join(m.tools) if m.tools else '(none)'}\n"
            f"  permissions: {perms}"
        )
    return "\n".join(lines)


# -------------------------------------------------------------- relevance
# Same tokenise-and-filter approach memory/store.py's _fts_query and
# notes.py already use for free-form speech, reused here instead of a third
# reimplementation.
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "is", "are", "was", "were", "to", "of",
    "in", "on", "for", "it", "my", "me", "i", "you", "your", "what", "whats",
    "how", "do", "does", "did", "can", "could", "please", "peter", "hey",
}


def _words(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text) if len(w) > 2
            and w.lower() not in _STOPWORDS}


def _skill_keywords(manifest: SkillManifest) -> set[str]:
    words = _words(manifest.name) | _words(manifest.description)
    for tool in manifest.tools:
        words |= _words(tool.replace("_", " "))
    return words


def relevant_tool_names(user_text: str, cfg) -> set[str] | None:
    """Tool names worth sending this turn, or None to mean "send everything."

    None is the deliberate, safe default outcome: it is returned whenever
    nothing scored above zero, or the match would not actually shrink the
    list below what "everything" already is. A no-match utterance must never
    silently hide a tool Claude needed — sending the full set is always the
    fallback, never an empty one.

    See the architecture notes for why this ships behind
    agent.tool_filter.enabled=False by default: it trades the prompt cache's
    stable prefix for a smaller per-turn tool list, and at today's tool
    count that trade is not obviously a win.
    """
    manifests = all_skills()
    if not manifests:
        return None

    always = set(cfg.always_include)
    query_words = _words(user_text)

    scored = []
    for m in manifests:
        if m.name in always:
            continue
        score = len(query_words & _skill_keywords(m))
        if score > 0:
            scored.append((score, m))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    chosen = [m for m in manifests if m.name in always]
    chosen += [m for _, m in scored[: max(0, cfg.max_skills - len(chosen))]]

    if not scored:
        # Nothing matched at all — nothing gained by filtering, and every
        # reason not to guess wrong.
        return None

    names = {tool for m in chosen for tool in m.tools}
    return names
