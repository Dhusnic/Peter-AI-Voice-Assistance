"""Notes & journal tools — quick voice capture, searchable later.

Distinct from remember_fact/set_preference in memory_tools.py: a note is a
one-off timestamped entry Peter never reasons from automatically, only
recalls when asked. Use this for "note that ...", not for something Peter
should already know on every future turn.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.services import services
from peter.notes import spoken

register_skill(SkillManifest(
    name="notes", version="1.0.0",
    description="A personal journal — quick timestamped notes, searchable later.",
    module=__name__, permissions=(),
    tools=("add_note", "search_notes", "recent_notes", "delete_note"),
))


@peter_tool(tier="write")
def add_note(text: str) -> str:
    """Capture a quick journal note, timestamped, for later recall.

    Use this for "note that ...", "write this down", or "remember I said ..."
    — a one-off entry. For something Peter should recall unprompted in every
    future conversation, use remember_fact instead.

    Args:
        text: What to write down, as the user said it.
    """
    if not text.strip():
        return "There is nothing to note."
    note_id = services().notes().add(text)
    return f"Noted (#{note_id})."


@peter_tool(tier="read")
def search_notes(query: str) -> str:
    """Search past notes by keyword.

    Args:
        query: Words to search for, e.g. "client demo" or "wifi password".
    """
    return spoken(services().notes().search(query))


@peter_tool(tier="read")
def recent_notes(limit: int = 10) -> str:
    """List the most recently added notes, newest first.

    Args:
        limit: How many to return.
    """
    return spoken(services().notes().recent(limit))


@peter_tool(tier="write")
def delete_note(note_id: int) -> str:
    """Delete a note permanently.

    Args:
        note_id: The note's number, shown in brackets by search_notes /
            recent_notes, e.g. 7 for "[7] ...".
    """
    ok = services().notes().delete(note_id)
    return f"Deleted note #{note_id}." if ok else f"No note numbered {note_id}."
