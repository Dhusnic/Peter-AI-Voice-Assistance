# system

Windows system control: apps, files, clipboard, volume, screenshots, stats,
lock, and a raw PowerShell escape hatch. This is what "full system access"
actually means for Peter in practice.

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `open_app` | write | Launch an app or document by name (Run-box style resolution). |
| `open_url` | write | Open a URL in the default browser. |
| `list_files` | read | Directory listing with a glob filter. |
| `read_file` | read | Read a text file (truncated past `_MAX_READ_CHARS` = 20,000 chars). |
| `search_files` | read | Recursive filename glob search. |
| `write_file` | write | Write/append text; refuses protected system roots. |
| `delete_file` | write | Delete a file or *empty* directory — permanent, no Recycle Bin. |
| `move_file` | write | Move/rename; refuses protected system roots on either end. |
| `take_screenshot` | read | Full-screen capture (all monitors) to PNG. |
| `get_clipboard` / `set_clipboard` | read / write | Windows clipboard text. |
| `set_volume` | write | System master volume, 0–100. |
| `system_stats` | read | CPU / memory / disk / battery snapshot. |
| `lock_workstation` | write | Win+L equivalent. |
| `run_powershell` | write | Arbitrary PowerShell command — the escape hatch. |

## Setup

Always registered — no config flag, no credential. It's core, not an
optional integration.

## Design notes & gotchas

- **`run_powershell` has no read-only variant, deliberately.** "This
  command only reads" is not a property enforceable from outside a shell —
  a shell is a shell, gated as one `write`-tier tool, always confirmed,
  always audit-logged. Never add a "safe mode" flag to it; that would be a
  false promise, not a safety feature.
- **`_PROTECTED_ROOTS`** (`C:/Windows`, `C:/Program Files`,
  `C:/Program Files (x86)`) blocks `write_file`/`delete_file`/`move_file`
  from touching system directories, checked via `_is_protected()` against
  both the path itself and its parents. This list is intentionally short
  and Windows-specific — it is a backstop against an obviously destructive
  mistake, not a general sandbox; `run_powershell` bypasses it entirely by
  design (see above).
- `delete_file` refuses non-empty directories outright rather than doing a
  recursive delete — a spoken "delete that folder" is exactly the kind of
  command where silently deleting more than intended would be the worst
  possible failure mode. If recursive delete is ever wanted, it needs its
  own explicit, loudly-confirmed tool, not a flag bolted onto this one.
- `read_file`/`run_powershell` truncate output at `_MAX_READ_CHARS`
  (20,000 chars) loudly, with a note in the response — never silently, and
  never by reading a huge file/output fully into memory first without a cap.
- Every `write`-tier tool here passes through the policy gate (§2.4) exactly
  like every other skill's tools — nothing about "system" tools gets special
  treatment in the gate itself; the caution is entirely in the tool bodies
  above.

## Future extension ideas

- `move_file`/`delete_file` currently only protect three hardcoded roots;
  worth revisiting if real usage ever surfaces a near-miss near another
  sensitive path (e.g. the user's own `.ssh`, Peter's own `data/` directory)
  — but resist the urge to pre-guess every path worth protecting.
- No Recycle-Bin-based delete exists as an alternative to the permanent
  `delete_file` — could be added as a separate, softer tool if accidental
  deletions turn out to be a real problem in practice.
