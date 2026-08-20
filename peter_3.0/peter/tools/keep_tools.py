"""Google Keep tools.

Off unless integrations.keep.enabled is true and GOOGLE_KEEP_EMAIL /
GOOGLE_KEEP_MASTER_TOKEN are set in .env — see docs/USER_MANUAL.md before
turning this on, the credential here is not like the OAuth ones the rest of
Peter's Google tools use. Until then services().keep() raises
NotConfiguredError, same as any other optional integration.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.services import services

register_skill(SkillManifest(
    name="keep", version="1.0.0",
    description="Google Keep notes (unofficial client — see setup docs).",
    module=__name__, permissions=("network",),
    tools=("list_keep_notes", "search_keep_notes", "create_keep_note",
           "pin_keep_note", "archive_keep_note", "delete_keep_note"),
))


@peter_tool(tier="read")
def list_keep_notes(include_archived: bool = False) -> str:
    """List Google Keep notes, most recent first.

    Args:
        include_archived: True to also include archived notes.
    """
    notes = services().keep().list_notes(include_archived=include_archived)
    if not notes:
        return "No Keep notes."
    return "\n".join(f"[{n.id}] {n.spoken()}" for n in notes)


@peter_tool(tier="read")
def search_keep_notes(query: str) -> str:
    """Search Google Keep notes by title or text.

    Args:
        query: What to look for.
    """
    notes = services().keep().find_notes(query)
    if not notes:
        return f"No Keep note matches {query!r}."
    return "\n".join(f"[{n.id}] {n.spoken()}" for n in notes)


@peter_tool(tier="write")
def create_keep_note(text: str, title: str = "", pinned: bool = False) -> str:
    """Create a new Google Keep note.

    Args:
        text: The note's content.
        title: Optional title.
        pinned: True to pin it.
    """
    if not text.strip():
        return "Give some text for the note."
    note = services().keep().create_note(title, text, pinned=pinned)
    return f"Added to Keep: {note.spoken()}"


@peter_tool(tier="write")
def pin_keep_note(note_id: str = "", matching_text: str = "", pinned: bool = True) -> str:
    """Pin or unpin a Google Keep note.

    Give either the bracketed id from list_keep_notes/search_keep_notes, or
    text to search for. If the text matches more than one note, nothing is
    changed and the matches are listed so you can ask which was meant.

    Args:
        note_id: Exact note id.
        matching_text: Part of the note's title or text, used when no id is known.
        pinned: True to pin, False to unpin.
    """
    keep = services().keep()

    if note_id.strip():
        note = keep.pin_note(note_id.strip(), pinned=pinned)
        return f"{'Pinned' if pinned else 'Unpinned'}: {note.spoken()}"

    if not matching_text.strip():
        return "Give either note_id or matching_text."

    matches = keep.find_by_text(matching_text)
    if not matches:
        return f"No Keep note matches {matching_text!r}."
    if len(matches) > 1:
        listing = "\n".join(f"[{n.id}] {n.spoken()}" for n in matches)
        return f"That matches {len(matches)} notes — ask which one:\n{listing}"

    note = keep.pin_note(matches[0].id, pinned=pinned)
    return f"{'Pinned' if pinned else 'Unpinned'}: {note.spoken()}"


@peter_tool(tier="write")
def archive_keep_note(note_id: str = "", matching_text: str = "", archived: bool = True) -> str:
    """Archive or unarchive a Google Keep note.

    Args:
        note_id: Exact note id.
        matching_text: Part of the note's title or text, used when no id is known.
        archived: True to archive, False to bring back from archive.
    """
    keep = services().keep()

    if note_id.strip():
        note = keep.archive_note(note_id.strip(), archived=archived)
        return f"{'Archived' if archived else 'Unarchived'}: {note.spoken()}"

    if not matching_text.strip():
        return "Give either note_id or matching_text."

    matches = keep.find_by_text(matching_text)
    if not matches:
        return f"No Keep note matches {matching_text!r}."
    if len(matches) > 1:
        listing = "\n".join(f"[{n.id}] {n.spoken()}" for n in matches)
        return f"That matches {len(matches)} notes — ask which one:\n{listing}"

    note = keep.archive_note(matches[0].id, archived=archived)
    return f"{'Archived' if archived else 'Unarchived'}: {note.spoken()}"


@peter_tool(tier="write")
def delete_keep_note(note_id: str = "", matching_text: str = "") -> str:
    """Move a Google Keep note to trash.

    Args:
        note_id: Exact note id.
        matching_text: Part of the note's title or text, used when no id is known.
    """
    keep = services().keep()

    if note_id.strip():
        keep.delete_note(note_id.strip())
        return "Note deleted."

    if not matching_text.strip():
        return "Give either note_id or matching_text."

    matches = keep.find_by_text(matching_text)
    if not matches:
        return f"No Keep note matches {matching_text!r}."
    if len(matches) > 1:
        listing = "\n".join(f"[{n.id}] {n.spoken()}" for n in matches)
        return f"That matches {len(matches)} notes — ask which one:\n{listing}"

    keep.delete_note(matches[0].id)
    return f"Deleted: {matches[0].spoken()}"
