"""Local folders, found by describing them rather than by path.

"Open my downloads", "open the Peter project" — the standard Windows user
folders are known automatically, anything else is named in config.yml under
`desktop.places`, and both are searched together by the same fuzzy matcher the
bookmarks use.

Deliberately not a filesystem-wide search. Scanning every drive to find a
folder called "projects" would be slow, would surface dozens of irrelevant
matches, and would happily open something in a system directory. A known,
declared set is both faster and safer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Standard folders, resolved from the environment so they follow a relocated
# profile or OneDrive redirection.
_STANDARD = {
    "desktop": "%UserProfile%\\Desktop",
    "documents": "%UserProfile%\\Documents",
    "downloads": "%UserProfile%\\Downloads",
    "pictures": "%UserProfile%\\Pictures",
    "videos": "%UserProfile%\\Videos",
    "music": "%UserProfile%\\Music",
    "home": "%UserProfile%",
    "appdata": "%AppData%",
    "temp": "%Temp%",
    "recycle bin": "shell:RecycleBinFolder",
}


@dataclass(frozen=True, slots=True)
class Place:
    name: str
    path: str
    configured: bool = False

    @property
    def is_shell(self) -> bool:
        """A shell: alias (Recycle Bin) rather than a real filesystem path."""
        return self.path.lower().startswith("shell:")

    def exists(self) -> bool:
        return self.is_shell or Path(os.path.expandvars(self.path)).is_dir()


def known_places(configured: dict[str, str] | None = None) -> list[Place]:
    """Standard Windows folders plus anything named in config, existing only."""
    places = [
        Place(name=name, path=path, configured=False)
        for name, path in _STANDARD.items()
    ]
    for name, path in (configured or {}).items():
        places.append(Place(name=name, path=path, configured=True))

    return [p for p in places if p.exists()]


def resolve(place: Place) -> str:
    """The string to hand to the shell for opening."""
    if place.is_shell:
        return place.path
    return str(Path(os.path.expandvars(place.path)))
