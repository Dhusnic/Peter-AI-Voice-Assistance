"""Saving and restoring a set of open applications.

"Save this as my migration setup" then, tomorrow, "restore my migration setup"
— the apps come back without hunting through the Start menu for the sixth
time. It pairs naturally with focus mode: one puts you in the right state of
mind, the other puts the right things on the screen.

**Only windows you can see are captured.** Enumerating processes would sweep
up sixty background services, none of which you meant. Walking the visible
top-level windows instead gets almost exactly the set a person would list if
asked what they had open, and the ignore list in config.yml removes the few
shell processes that always have a window anyway.

**Restoring launches executables** — the ones already recorded from your own
running programs, never an arbitrary path, and never anything on the ignore
list. Anything already running is left alone rather than opened twice.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from peter.core.db import Db

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    name       TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


@dataclass(slots=True)
class App:
    exe: str
    name: str
    title: str = ""


@dataclass(slots=True)
class Snapshot:
    name: str
    apps: list[App] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    created_at: float = 0.0

    def to_json(self) -> str:
        return json.dumps(
            {
                "apps": [asdict(a) for a in self.apps],
                "urls": self.urls,
            }
        )

    @classmethod
    def from_json(cls, name: str, payload: str, created_at: float) -> "Snapshot":
        data = json.loads(payload)
        return cls(
            name=name,
            apps=[App(**a) for a in data.get("apps", [])],
            urls=list(data.get("urls", [])),
            created_at=created_at,
        )

    def spoken(self) -> str:
        names = ", ".join(dict.fromkeys(a.name for a in self.apps))
        extra = f" and {len(self.urls)} page(s)" if self.urls else ""
        return f"{len(self.apps)} app(s){extra}: {names}" if names else "nothing"


class WorkspaceStore:
    def __init__(self, db_path: Path):
        self.db = Db(db_path, _SCHEMA)

    def close(self) -> None:
        self.db.close()

    def save(self, snapshot: Snapshot) -> None:
        self.db.execute(
            """INSERT INTO workspaces (name, payload, created_at) VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                   payload = excluded.payload, created_at = excluded.created_at""",
            (snapshot.name.strip().lower(), snapshot.to_json(), time.time()),
        )

    def get(self, name: str) -> Snapshot | None:
        row = self.db.one(
            "SELECT * FROM workspaces WHERE name = ?", (name.strip().lower(),)
        )
        if row is None:
            # Forgiving lookup: this is reached by voice, and "my migration
            # setup" should find the workspace saved as "migration".
            needle = name.strip().lower()
            for candidate in self.all():
                if needle and (needle in candidate.name or candidate.name in needle):
                    return candidate
            return None
        return Snapshot.from_json(row["name"], row["payload"], row["created_at"])

    def all(self) -> list[Snapshot]:
        return [
            Snapshot.from_json(r["name"], r["payload"], r["created_at"])
            for r in self.db.query("SELECT * FROM workspaces ORDER BY created_at DESC")
        ]

    def delete(self, name: str) -> bool:
        return self.db.execute(
            "DELETE FROM workspaces WHERE name = ?", (name.strip().lower(),)
        ).rowcount > 0


# ------------------------------------------------------------------ capture
def visible_apps(cfg) -> list[App]:
    """The applications with a visible window right now, deduplicated."""
    try:
        import psutil
        import win32gui
        import win32process
    except ImportError:
        log.debug("workspace capture needs pywin32 and psutil")
        return []

    ignore = {name.lower() for name in cfg.ignore_executables}
    found: dict[str, App] = {}

    def visit(handle, _extra):
        if not win32gui.IsWindowVisible(handle):
            return
        title = win32gui.GetWindowText(handle)
        if not title.strip():
            return  # invisible helper windows all have empty titles
        try:
            _thread, pid = win32process.GetWindowThreadProcessId(handle)
            process = psutil.Process(pid)
            exe = process.exe()
        except Exception:
            return
        filename = Path(exe).name
        if filename.lower() in ignore or not exe:
            return
        found.setdefault(exe.lower(), App(exe=exe, name=Path(exe).stem, title=title))

    try:
        win32gui.EnumWindows(visit, None)
    except Exception:
        log.debug("could not enumerate windows", exc_info=True)

    return list(found.values())[: cfg.max_apps]


def capture(name: str, config) -> Snapshot:
    """Snapshot what is open now under `name`."""
    cfg = config.integrations.workspace
    snapshot = Snapshot(name=name.strip().lower(), apps=visible_apps(cfg), created_at=time.time())

    # If Peter's own scripted browser happens to be open on something, that is
    # worth keeping too — it is part of what you were doing.
    try:
        from peter.core.services import services

        container = services()
        if container._browser is not None and container._browser.is_running:
            state = container._browser.state()
            if state.url and state.url != "about:blank":
                snapshot.urls.append(state.url)
    except Exception:
        log.debug("no browser state to capture", exc_info=True)

    return snapshot


# ------------------------------------------------------------------ restore
def restore(snapshot: Snapshot, config) -> str:
    """Launch what is missing. Returns what to say about it."""
    cfg = config.integrations.workspace
    ignore = {n.lower() for n in cfg.ignore_executables}

    running = _running_executables()
    launched: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    for app in snapshot.apps[: cfg.max_apps]:
        filename = Path(app.exe).name
        if filename.lower() in ignore:
            continue
        if filename.lower() in running:
            skipped.append(app.name)
            continue
        if not Path(app.exe).exists():
            failed.append(app.name)
            continue
        try:
            _launch(app.exe)
            launched.append(app.name)
        except Exception:
            log.debug("could not launch %s", app.exe, exc_info=True)
            failed.append(app.name)

    for url in snapshot.urls:
        try:
            from peter.integrations.desktop import browsers

            browsers.open_url(url)
            launched.append(_host(url))
        except Exception:
            log.debug("could not reopen %s", url, exc_info=True)
            failed.append(_host(url))

    parts = []
    if launched:
        parts.append(f"Opened {', '.join(dict.fromkeys(launched))}.")
    if skipped:
        parts.append(f"{', '.join(dict.fromkeys(skipped))} was already running.")
    if failed:
        parts.append(f"Could not open {', '.join(dict.fromkeys(failed))}.")
    return " ".join(parts) or "Everything in that workspace was already open."


def _running_executables() -> set[str]:
    try:
        import psutil
    except ImportError:
        return set()
    names = set()
    for process in psutil.process_iter(["name"]):
        try:
            names.add((process.info["name"] or "").lower())
        except Exception:
            continue
    return names


def _launch(exe: str) -> None:
    """Start a program detached, so it outlives Peter."""
    try:
        os.startfile(exe)  # noqa: S606 - Windows-only, and the path came from a running process
        return
    except AttributeError:  # pragma: no cover - non-Windows
        pass
    subprocess.Popen(  # noqa: S603
        [exe], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True,
    )


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).netloc or url
