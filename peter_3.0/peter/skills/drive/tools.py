"""Google Drive tools — full read/write.

Upload/download of local files are deliberately not exposed here (they stay
client-only methods on DriveClient) — "create a doc from this text" is a far
more common voice command than "upload this local file," and keeping the
tool count minimal matters more than exhaustive API coverage. trash_drive_file
always trashes, never permanently deletes — same reversibility norm as
delete_keep_note.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.services import services

register_skill(SkillManifest(
    name="drive", version="1.0.0",
    description="Google Drive: list, search, read, create, move, rename, share, "
                "trash files, and check storage usage.",
    module=__name__, permissions=("network",),
    tools=("list_drive_files", "search_drive_files", "read_drive_file",
           "create_drive_file", "create_drive_folder", "move_drive_file",
           "rename_drive_file", "share_drive_file", "trash_drive_file",
           "drive_storage_status"),
))

_MAX_READ_CHARS = 4000


@peter_tool(tier="read")
def list_drive_files(folder_id: str = "", query: str = "") -> str:
    """List files in Google Drive, optionally inside one folder or matching a name.

    Args:
        folder_id: A Drive folder id to list inside. Empty lists across all of Drive.
        query: Only files whose name contains this text.
    """
    files = services().drive().list_files(folder_id=folder_id.strip(), query=query.strip())
    if not files:
        return "No matching Drive files."
    return "\n".join(f"[{f.id}] {f.spoken()}" for f in files)


@peter_tool(tier="read")
def search_drive_files(text: str) -> str:
    """Search Google Drive by file name.

    Args:
        text: Text to search for in file names.
    """
    text = text.strip()
    if not text:
        return "Give some text to search for."
    files = services().drive().search_files(text)
    if not files:
        return f"No Drive file matches {text!r}."
    return "\n".join(f"[{f.id}] {f.spoken()}" for f in files)


@peter_tool(tier="read")
def read_drive_file(file_id: str) -> str:
    """Read a Drive file's text content — Google Docs/Sheets/Slides are exported
    to plain text, other text files are read directly.

    Args:
        file_id: The Drive file id, from list_drive_files or search_drive_files.
    """
    text = services().drive().read_file_text(file_id.strip())
    if len(text) > _MAX_READ_CHARS:
        return text[:_MAX_READ_CHARS] + "... (truncated)"
    return text


@peter_tool(tier="read")
def drive_storage_status() -> str:
    """Report how much Google Drive storage is used and how much is free."""
    return services().drive().get_storage_quota().spoken()


@peter_tool(tier="write")
def create_drive_file(name: str, content: str, folder_id: str = "") -> str:
    """Create a new plain-text file in Google Drive from given content.

    Args:
        name: The file's name.
        content: The file's text content.
        folder_id: Optional Drive folder id to create it inside. Empty creates it at the top level.
    """
    if not name.strip():
        return "Give the file a name."
    created = services().drive().create_text_file(name.strip(), content, folder_id=folder_id.strip())
    return f"Created: {created.spoken()}"


@peter_tool(tier="write")
def create_drive_folder(name: str, parent_id: str = "") -> str:
    """Create a new folder in Google Drive.

    Args:
        name: The folder's name.
        parent_id: Optional Drive folder id to create it inside. Empty creates it at the top level.
    """
    if not name.strip():
        return "Give the folder a name."
    created = services().drive().create_folder(name.strip(), parent_id=parent_id.strip())
    return f"Created folder: {created.spoken()}"


@peter_tool(tier="write")
def move_drive_file(file_id: str, new_folder_id: str) -> str:
    """Move a Drive file into a different folder.

    Args:
        file_id: The Drive file id to move.
        new_folder_id: The destination folder's Drive id.
    """
    moved = services().drive().move_file(file_id.strip(), new_folder_id.strip())
    return f"Moved: {moved.spoken()}"


@peter_tool(tier="write")
def rename_drive_file(file_id: str, new_name: str) -> str:
    """Rename a Drive file.

    Args:
        file_id: The Drive file id to rename.
        new_name: The new name.
    """
    if not new_name.strip():
        return "Give the new name."
    renamed = services().drive().rename_file(file_id.strip(), new_name.strip())
    return f"Renamed to: {renamed.spoken()}"


@peter_tool(tier="write")
def share_drive_file(file_id: str, email: str, role: str = "reader") -> str:
    """Share a Drive file with someone by email.

    Args:
        file_id: The Drive file id to share.
        email: The email address to share with.
        role: One of "reader", "commenter", "writer".
    """
    if not email.strip():
        return "Give an email address to share with."
    try:
        services().drive().share_file(file_id.strip(), email.strip(), role=role)
    except ValueError as exc:
        return str(exc)
    return f"Shared with {email} as {role}."


@peter_tool(tier="write")
def trash_drive_file(file_id: str) -> str:
    """Move a Drive file to trash. Recoverable from the Drive trash, not permanently deleted.

    Args:
        file_id: The Drive file id to trash.
    """
    trashed = services().drive().trash_file(file_id.strip())
    return f"Trashed: {trashed.spoken()}"
