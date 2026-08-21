# workspace

Saving and restoring a set of open applications by name
(`peter/workspace.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `save_workspace` | write | Save currently open apps under a name. |
| `restore_workspace` | write | Reopen the apps saved under a workspace name. |
| `list_workspaces` | read | List every saved workspace and what's in it. |
| `delete_workspace` | write | Forget a saved workspace. |

## Setup

`integrations.workspace.enabled` (default true) is the only gate — no
secret needed. `WorkspaceConfig`: `ignore_executables` (a list of processes
never captured or relaunched — `explorer.exe`, several Windows shell
processes, and Peter's own interpreter/console executables, since these are
either always running anyway or actively harmful to relaunch), `max_apps`
(default 25).

## Design notes & gotchas

- **Named after what the user says, not the mechanism — deliberately.**
  "Save my workspace" is the natural phrasing; underneath it's a window
  enumeration (via `pywin32`) plus a filtered list of executables, but the
  tool names and docstrings never expose that framing.
- **`save_workspace` needs `pywin32` and only captures windows that are
  actually visible.** If it finds nothing, it says so plainly
  ("I could not see any application windows to save...") rather than
  silently saving an empty workspace.
- **`restore_workspace` leaves an already-running app alone rather than
  reopening it a second time** — restoring a workspace that overlaps with
  the current session doesn't spawn duplicate windows.
- `ignore_executables` is checked by filename match, not full path — a
  deliberate simplification, since the goal is filtering out shell chrome
  and Peter's own process, not building a general allow/deny system.
- `save_workspace` requires a name up front — there's no "save my current
  setup" without naming it, since restoring later depends entirely on that
  name.

## Future extension ideas

- No per-app window position/size capture — a restored app reopens, but not
  necessarily in the same place on screen it was saved from.
- No auto-save-on-shutdown or scheduled snapshot — every save is an
  explicit, user-initiated action.
