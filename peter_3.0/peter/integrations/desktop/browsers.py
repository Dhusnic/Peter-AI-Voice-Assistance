"""Browsers and their bookmarks.

Two jobs: launch a URL in the browser Dhusnic actually uses rather than
whatever Windows has registered as default, and read the bookmarks out of every
installed browser so "open my staging dashboard" can resolve to a real URL.

Bookmarks live in two different shapes and both are read directly off disk:

    Firefox     places.sqlite, a real SQLite database
    Chromium    Bookmarks, a JSON file (Chrome, Edge and Brave all share it)

**Firefox's database is locked while Firefox is running**, which is precisely
when you want to read it. Opening it read-only still fails on Windows, so it is
copied to a temp file first and the copy is queried. The copy is cheap next to
being unable to answer at all while the browser is open.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Browser name -> the places its executable is normally installed.
_BROWSER_PATHS: dict[str, tuple[str, ...]] = {
    "firefox": (
        r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
        r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe",
    ),
    "chrome": (
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    ),
    "edge": (
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    ),
    "brave": (
        r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
    ),
}

# Chromium-family user-data roots, for reading their Bookmarks JSON.
_CHROMIUM_DATA = {
    "chrome": r"%LocalAppData%\Google\Chrome\User Data",
    "edge": r"%LocalAppData%\Microsoft\Edge\User Data",
    "brave": r"%LocalAppData%\BraveSoftware\Brave-Browser\User Data",
}

_FIREFOX_PROFILES = r"%AppData%\Mozilla\Firefox\Profiles"


@dataclass(frozen=True, slots=True)
class Bookmark:
    title: str
    url: str
    browser: str
    folder: str = ""

    def spoken(self) -> str:
        where = f" in {self.folder}" if self.folder else ""
        return f"{self.title}{where}"


def _expand(path: str) -> Path:
    return Path(os.path.expandvars(path))


def detect_browsers() -> dict[str, Path]:
    """Installed browsers, name -> executable path."""
    found: dict[str, Path] = {}
    for name, candidates in _BROWSER_PATHS.items():
        for candidate in candidates:
            path = _expand(candidate)
            if path.is_file():
                found[name] = path
                break
    return found


def browser_path(name: str) -> Path | None:
    if not name or name.lower() in ("default", "system"):
        return None
    return detect_browsers().get(name.strip().lower())


def open_url(url: str, browser: str = "", profile: str = "",
             new_window: bool = False) -> str:
    """Open `url`, preferring the named browser. Falls back to the system default.

    Returns a short description of what was actually used, so the caller can
    tell the user the truth rather than what was asked for — "opened in Chrome,
    Firefox is not installed" beats silently doing something else.
    """
    exe = browser_path(browser)
    if exe is None:
        import webbrowser

        webbrowser.open_new_tab(url)
        if browser and browser.lower() not in ("default", "system"):
            return f"opened in your default browser ({browser} is not installed)"
        return "opened in your default browser"

    args: list[str] = [str(exe)]
    name = browser.strip().lower()
    if profile:
        # The two families spell "use this profile" differently.
        if name == "firefox":
            args += ["-P", profile]
        else:
            args += [f"--profile-directory={profile}"]
    if new_window:
        args.append("-new-window" if name == "firefox" else "--new-window")
    args.append(url)

    subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return f"opened in {name}"


# --------------------------------------------------------------- bookmarks
def _firefox_bookmarks() -> list[Bookmark]:
    root = _expand(_FIREFOX_PROFILES)
    if not root.is_dir():
        return []

    out: list[Bookmark] = []
    for profile in root.iterdir():
        db = profile / "places.sqlite"
        if not db.is_file():
            continue
        # Locked while Firefox runs — copy, then read the copy.
        tmp = Path(tempfile.gettempdir()) / f"peter_places_{profile.name}.sqlite"
        try:
            shutil.copy2(db, tmp)
            conn = sqlite3.connect(str(tmp))
            try:
                rows = conn.execute(
                    """
                    SELECT b.title, p.url, COALESCE(parent.title, '')
                    FROM moz_bookmarks b
                    JOIN moz_places p ON b.fk = p.id
                    LEFT JOIN moz_bookmarks parent ON b.parent = parent.id
                    WHERE b.type = 1 AND b.title IS NOT NULL AND b.title != ''
                    """
                ).fetchall()
            finally:
                conn.close()
            out += [
                Bookmark(title=t, url=u, browser="firefox", folder=f or "")
                for t, u, f in rows
                if u and not u.startswith("place:")
            ]
        except (OSError, sqlite3.Error) as exc:
            log.debug("could not read firefox bookmarks from %s: %s", profile, exc)
        finally:
            tmp.unlink(missing_ok=True)
    return out


def _walk_chromium(node: dict, browser: str, folder: str) -> list[Bookmark]:
    out: list[Bookmark] = []
    if node.get("type") == "url" and node.get("url"):
        out.append(Bookmark(
            title=node.get("name") or node["url"],
            url=node["url"], browser=browser, folder=folder,
        ))
    for child in node.get("children") or ():
        out += _walk_chromium(child, browser, node.get("name") or folder)
    return out


def _chromium_bookmarks(browser: str) -> list[Bookmark]:
    location = _CHROMIUM_DATA.get(browser)
    if not location:
        return []
    root = _expand(location)
    if not root.is_dir():
        return []

    out: list[Bookmark] = []
    for profile in root.iterdir():
        path = profile / "Bookmarks"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.debug("could not read %s bookmarks: %s", browser, exc)
            continue
        for node in (data.get("roots") or {}).values():
            if isinstance(node, dict):
                out += _walk_chromium(node, browser, "")
    return out


def read_bookmarks(sources: tuple[str, ...] = ()) -> list[Bookmark]:
    """Every bookmark from every requested browser, de-duplicated by URL."""
    wanted = tuple(s.lower() for s in sources) or ("firefox", "chrome", "edge", "brave")

    collected: list[Bookmark] = []
    if "firefox" in wanted:
        collected += _firefox_bookmarks()
    for name in ("chrome", "edge", "brave"):
        if name in wanted:
            collected += _chromium_bookmarks(name)

    seen: set[str] = set()
    unique: list[Bookmark] = []
    for mark in collected:
        key = mark.url.rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            unique.append(mark)
    return unique
