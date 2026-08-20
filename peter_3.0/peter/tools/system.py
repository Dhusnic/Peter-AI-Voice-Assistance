"""Windows system control.

This is what "full system access" actually means in practice. Every tool that
changes something is `write` tier, so it stops and asks before running, and
every call lands in the audit log.

`run_powershell` is the deliberate escape hatch for everything not covered by a
named tool. There is no read-only variant of it, because "this command only
reads" is not a property anyone can enforce from the outside — a shell is a
shell, and it is gated as one.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import psutil

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.services import services

register_skill(SkillManifest(
    name="system", version="1.0.0",
    description="Windows system control: apps, files, clipboard, volume, "
                 "screenshots, stats, lock, PowerShell.",
    module=__name__, permissions=("filesystem", "shell"),
    tools=("open_app", "open_url", "list_files", "read_file", "search_files",
           "write_file", "delete_file", "move_file", "take_screenshot",
           "get_clipboard", "set_clipboard", "set_volume", "system_stats",
           "lock_workstation", "run_powershell"),
))

# Reading a whole file into the context window is rarely what anyone wants, and
# a stray 200MB log would blow the request. Truncate loudly instead.
_MAX_READ_CHARS = 20_000

# Paths that are never safe to delete or overwrite, whatever the user said.
_PROTECTED_ROOTS = (
    Path("C:/Windows"),
    Path("C:/Program Files"),
    Path("C:/Program Files (x86)"),
)


def _resolve(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path.strip()))).resolve()


def _is_protected(path: Path) -> bool:
    for root in _PROTECTED_ROOTS:
        try:
            if path == root or root in path.parents:
                return True
        except (OSError, ValueError):
            continue
    return False


# ------------------------------------------------------------------- apps
@peter_tool(tier="write")
def open_app(name: str) -> str:
    """Launch an application or open a document by name.

    Resolves the way the Windows Run box does, so common names work directly:
    "notepad", "calc", "chrome", "firefox", "explorer", "code", "spotify". A
    full path to an .exe or a document also works.

    Args:
        name: Application name or full path.
    """
    target = name.strip()
    if not target:
        return "Give an application name."
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", target],
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        return f"Could not launch {target!r}: {exc}"
    return f"Launched {target}."


@peter_tool(tier="write")
def open_url(url: str) -> str:
    """Open a URL in the default browser.

    Args:
        url: Full URL including the scheme, e.g. "https://example.com".
    """
    import webbrowser

    clean = url.strip()
    if not clean.lower().startswith(("http://", "https://")):
        return "URL must start with http:// or https://"
    webbrowser.open_new_tab(clean)
    return f"Opened {clean}."


# ------------------------------------------------------------------- files
@peter_tool(tier="read")
def list_files(directory: str, pattern: str = "*") -> str:
    """List the contents of a directory.

    Args:
        directory: Path to the directory, e.g. "D:/studies" or "~/Desktop".
        pattern: Optional glob filter such as "*.pdf". Defaults to everything.
    """
    path = _resolve(directory)
    if not path.is_dir():
        return f"{path} is not a directory."

    entries = sorted(path.glob(pattern), key=lambda p: (p.is_file(), p.name.lower()))
    if not entries:
        return f"{path} has nothing matching {pattern!r}."

    lines = []
    for entry in entries[:200]:
        try:
            marker = "DIR " if entry.is_dir() else f"{entry.stat().st_size:>9,}"
        except OSError:
            marker = "    ?    "
        lines.append(f"{marker}  {entry.name}")
    if len(entries) > 200:
        lines.append(f"... and {len(entries) - 200} more")
    return f"{path}:\n" + "\n".join(lines)


@peter_tool(tier="read")
def read_file(path: str) -> str:
    """Read a text file's contents.

    Args:
        path: Full path to the file.
    """
    target = _resolve(path)
    if not target.is_file():
        return f"{target} is not a file."
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Could not read {target}: {exc}"
    if len(text) > _MAX_READ_CHARS:
        return text[:_MAX_READ_CHARS] + (
            f"\n\n... truncated, file is {len(text):,} characters total."
        )
    return text or "(the file is empty)"


@peter_tool(tier="read")
def search_files(directory: str, pattern: str, max_results: int = 50) -> str:
    """Search a directory tree for files whose name matches a glob pattern.

    Args:
        directory: Where to start searching, e.g. "D:/studies".
        pattern: Glob pattern for the filename, e.g. "*.pdf" or "*report*".
        max_results: Stop after this many hits.
    """
    root = _resolve(directory)
    if not root.is_dir():
        return f"{root} is not a directory."

    hits: list[str] = []
    try:
        for found in root.rglob(pattern):
            hits.append(str(found))
            if len(hits) >= max_results:
                break
    except OSError as exc:
        return f"Search failed: {exc}"

    if not hits:
        return f"Nothing under {root} matches {pattern!r}."
    return "\n".join(hits)


@peter_tool(tier="write")
def write_file(path: str, content: str, append: bool = False) -> str:
    """Write text to a file, creating parent directories if needed.

    Args:
        path: Full path to the file.
        content: Text to write.
        append: True to append to an existing file, False to overwrite it.
    """
    target = _resolve(path)
    if _is_protected(target):
        return f"Refusing to write inside a protected system location: {target}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a" if append else "w", encoding="utf-8") as fh:
            fh.write(content)
    except OSError as exc:
        return f"Could not write {target}: {exc}"
    return f"{'Appended to' if append else 'Wrote'} {target}."


@peter_tool(tier="write")
def delete_file(path: str) -> str:
    """Delete a file, or an empty directory.

    This is permanent — it does not go to the Recycle Bin. Directories with
    contents are refused; say so and ask the user to confirm the exact target.

    Args:
        path: Full path to delete.
    """
    target = _resolve(path)
    if _is_protected(target):
        return f"Refusing to delete inside a protected system location: {target}"
    if not target.exists():
        return f"{target} does not exist."
    try:
        if target.is_dir():
            if any(target.iterdir()):
                return f"{target} is not empty. Refusing to delete a non-empty folder."
            target.rmdir()
        else:
            target.unlink()
    except OSError as exc:
        return f"Could not delete {target}: {exc}"
    return f"Deleted {target}."


@peter_tool(tier="write")
def move_file(source: str, destination: str) -> str:
    """Move or rename a file or folder.

    Args:
        source: Path to move from.
        destination: Path to move to. A directory destination keeps the name.
    """
    src = _resolve(source)
    dst = _resolve(destination)
    if not src.exists():
        return f"{src} does not exist."
    if _is_protected(src) or _is_protected(dst):
        return "Refusing to move into or out of a protected system location."
    try:
        shutil.move(str(src), str(dst))
    except (OSError, shutil.Error) as exc:
        return f"Could not move {src}: {exc}"
    return f"Moved {src} to {dst}."


# ------------------------------------------------------------------ desktop
@peter_tool(tier="read")
def take_screenshot(save_path: str = "") -> str:
    """Capture the screen to a PNG file and return its path.

    Args:
        save_path: Where to save it. Defaults to a timestamped file in Peter's
            data directory.
    """
    from PIL import ImageGrab

    if save_path.strip():
        target = _resolve(save_path)
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = services().config.data_dir / "screenshots" / f"screen-{stamp}.png"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(all_screens=True).save(target, "PNG")
    except (OSError, ValueError) as exc:
        return f"Screenshot failed: {exc}"
    return f"Saved screenshot to {target}."


@peter_tool(tier="read")
def get_clipboard() -> str:
    """Read the current text contents of the Windows clipboard."""
    try:
        import win32clipboard

        win32clipboard.OpenClipboard()
        try:
            data = win32clipboard.GetClipboardData()
        finally:
            win32clipboard.CloseClipboard()
    except Exception as exc:
        return f"Could not read the clipboard: {exc}"
    return data or "(clipboard is empty)"


@peter_tool(tier="write")
def set_clipboard(text: str) -> str:
    """Put text on the Windows clipboard.

    Args:
        text: The text to copy.
    """
    try:
        import win32clipboard

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text)
        finally:
            win32clipboard.CloseClipboard()
    except Exception as exc:
        return f"Could not set the clipboard: {exc}"
    return f"Copied {len(text)} characters to the clipboard."


@peter_tool(tier="write")
def set_volume(percent: int) -> str:
    """Set the system master output volume.

    Args:
        percent: Target volume from 0 (mute) to 100 (maximum).
    """
    from peter.integrations.desktop import volume as vol

    level = max(0, min(100, int(percent)))
    if not vol.set(level):
        return "Could not set the volume."
    return f"Volume set to {level}%."


@peter_tool(tier="read")
def system_stats() -> str:
    """Report CPU, memory, disk and battery status for this machine."""
    lines = [
        f"CPU: {psutil.cpu_percent(interval=0.3):.0f}% across "
        f"{psutil.cpu_count(logical=True)} logical cores",
    ]
    mem = psutil.virtual_memory()
    lines.append(
        f"Memory: {mem.percent:.0f}% used "
        f"({mem.used / 1e9:.1f} GB of {mem.total / 1e9:.1f} GB)"
    )
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        lines.append(
            f"Disk {part.device.rstrip(chr(92))} {usage.percent:.0f}% used, "
            f"{usage.free / 1e9:.0f} GB free"
        )
    battery = psutil.sensors_battery()
    if battery is not None:
        state = "charging" if battery.power_plugged else "on battery"
        lines.append(f"Battery: {battery.percent:.0f}% ({state})")
    return "\n".join(lines)


@peter_tool(tier="write")
def lock_workstation() -> str:
    """Lock the Windows session, as Win+L does."""
    if not ctypes.windll.user32.LockWorkStation():
        return "Could not lock the workstation."
    return "Locked."


@peter_tool(tier="write")
def run_powershell(command: str, timeout_seconds: int = 60) -> str:
    """Run an arbitrary PowerShell command on this machine.

    The escape hatch for anything no other tool covers. Prefer a named tool when
    one exists — they are safer and their output is easier to speak aloud. Never
    use this to bypass a refusal the user already gave.

    Args:
        command: The PowerShell command to run.
        timeout_seconds: Kill the command after this long. Max 300.
    """
    if not command.strip():
        return "No command given."

    limit = max(1, min(300, int(timeout_seconds)))
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-Command", command,
            ],
            capture_output=True,
            text=True,
            timeout=limit,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {limit}s."
    except OSError as exc:
        return f"Could not run PowerShell: {exc}"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if len(out) > _MAX_READ_CHARS:
        out = out[:_MAX_READ_CHARS] + "\n... output truncated."

    parts = [f"exit code {proc.returncode}"]
    if out:
        parts.append(f"stdout:\n{out}")
    if err:
        parts.append(f"stderr:\n{err}")
    return "\n".join(parts)
