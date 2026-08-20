"""Workspace tools — saving and restoring what you had open.

Deliberately named after what the user says ("save my workspace"), not after
the mechanism (a window enumeration and a list of executables).
"""

from __future__ import annotations

from datetime import datetime

from peter.agent.registry import peter_tool
from peter.core.services import services
from peter.workspace import capture, restore


@peter_tool(tier="write")
def save_workspace(name: str) -> str:
    """Save the applications currently open, under a name you can restore later.

    Args:
        name: What to call this setup, e.g. "migration" or "writing".
    """
    container = services()
    cfg = container.config.integrations.workspace
    if not cfg.enabled:
        return "Workspaces are switched off in config.yml."
    if not name.strip():
        return "Give the workspace a name so it can be restored later."

    snapshot = capture(name, container.config)
    if not snapshot.apps:
        return (
            "I could not see any application windows to save. This needs pywin32, "
            "and it only captures windows that are actually visible."
        )

    container.workspaces().save(snapshot)
    return f"Saved '{snapshot.name}' — {snapshot.spoken()}."


@peter_tool(tier="write")
def restore_workspace(name: str) -> str:
    """Reopen the applications saved under a workspace name.

    Anything already running is left alone rather than opened a second time.

    Args:
        name: The saved workspace, e.g. "migration".
    """
    container = services()
    snapshot = container.workspaces().get(name)
    if snapshot is None:
        saved = container.workspaces().all()
        if not saved:
            return "No workspaces have been saved yet."
        return (
            f"There is no workspace called {name!r}. Saved: "
            + ", ".join(s.name for s in saved)
        )
    return restore(snapshot, container.config)


@peter_tool(tier="read")
def list_workspaces() -> str:
    """List every saved workspace and what is in it."""
    saved = services().workspaces().all()
    if not saved:
        return "No workspaces have been saved yet."
    return "\n".join(
        f"{s.name} — {s.spoken()} "
        f"(saved {datetime.fromtimestamp(s.created_at):%d %b})"
        for s in saved
    )


@peter_tool(tier="write")
def delete_workspace(name: str) -> str:
    """Forget a saved workspace.

    Args:
        name: The workspace to delete.
    """
    if services().workspaces().delete(name):
        return f"Deleted the '{name.strip().lower()}' workspace."
    return f"There is no workspace called {name!r}."
