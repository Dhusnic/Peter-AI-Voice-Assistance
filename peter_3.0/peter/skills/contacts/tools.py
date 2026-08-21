"""Google Contacts tools.

One tool, read-only. Resolves a name to a real email/phone so send_email or
make_phone_call can be given something exact — it does not send or call
anything itself. Same split as call_contact vs make_phone_call in
phone_tools.py: resolve by name every time through a dedicated lookup, never
let a write-tier action trust a name it was merely told.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.services import services

register_skill(SkillManifest(
    name="contacts", version="1.0.0",
    description="Look up a Google Contacts entry by name.",
    module=__name__, permissions=("network",),
    tools=("find_google_contact",),
))


@peter_tool(tier="read")
def find_google_contact(name: str) -> str:
    """Look up a saved Google contact by name.

    Use this to resolve a name to a real email address or phone number before
    calling send_email or make_phone_call — those need the exact value, not a
    name. If more than one contact matches, list them and ask which was meant
    rather than guessing.

    Args:
        name: The contact's name, or part of it.
    """
    name = name.strip()
    if not name:
        return "Give a name to look up."

    matches = services().contacts().find_contact(name)
    if not matches:
        return f"No saved contact matching {name!r}."
    if len(matches) > 1:
        listing = "\n".join(f"- {c.spoken()}" for c in matches)
        return f"That matches {len(matches)} contacts — ask which one:\n{listing}"

    return matches[0].spoken()
