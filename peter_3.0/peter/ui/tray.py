"""System tray icon.

An always-on microphone needs a visible state indicator. Not a nicety — if you
cannot tell at a glance whether Peter is listening, you will eventually say
something near it that you did not mean it to hear.

    grey    idle, waiting for the wake word
    green   listening to you
    amber   thinking (model or tools running)
    blue    speaking
    red     paused, microphone ignored
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

STATE_COLOURS = {
    "idle": (110, 110, 118),
    "listening": (46, 174, 94),
    "thinking": (222, 160, 42),
    "speaking": (58, 130, 214),
    "paused": (198, 62, 62),
}


def _icon_image(colour: tuple[int, int, int]) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 4, size - 4), fill=colour)
    # A small microphone glyph, so the icon reads as Peter and not a status dot.
    draw.rounded_rectangle((26, 16, 38, 36), radius=6, fill=(255, 255, 255, 235))
    draw.arc((20, 26, 44, 46), start=0, end=180, fill=(255, 255, 255, 235), width=4)
    draw.line((32, 44, 32, 50), fill=(255, 255, 255, 235), width=4)
    return img


class Tray:
    """Wraps pystray. Degrades to a no-op if the tray is unavailable."""

    def __init__(
        self,
        on_pause: Callable[[bool], None] | None = None,
        on_quit: Callable[[], None] | None = None,
    ):
        self._on_pause = on_pause or (lambda paused: None)
        self._on_quit = on_quit or (lambda: None)
        self._icon = None
        self._paused = False
        self._state = "idle"
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        try:
            import pystray

            self._icon = pystray.Icon(
                "peter",
                _icon_image(STATE_COLOURS["idle"]),
                "Peter — idle",
                menu=pystray.Menu(
                    pystray.MenuItem(
                        "Pause listening",
                        self._toggle_pause,
                        checked=lambda _item: self._paused,
                    ),
                    pystray.MenuItem("Quit", self._quit),
                ),
            )
            self._thread = threading.Thread(
                target=self._icon.run, name="peter-tray", daemon=True
            )
            self._thread.start()
        except Exception:
            log.warning("system tray unavailable; running without an indicator")
            self._icon = None

    def set_state(self, state: str) -> None:
        self._state = state
        if self._icon is None:
            return
        colour = STATE_COLOURS.get(state, STATE_COLOURS["idle"])
        try:
            self._icon.icon = _icon_image(colour)
            self._icon.title = f"Peter — {state}"
        except Exception:
            log.debug("could not update tray icon", exc_info=True)

    @property
    def paused(self) -> bool:
        return self._paused

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass

    # ----------------------------------------------------------------- menu
    def _toggle_pause(self, _icon=None, _item=None) -> None:
        self._paused = not self._paused
        self.set_state("paused" if self._paused else "idle")
        self._on_pause(self._paused)

    def _quit(self, _icon=None, _item=None) -> None:
        self._on_quit()
        self.stop()


class NullTray:
    """Stand-in for text mode."""

    paused = False

    def start(self) -> None: ...
    def set_state(self, state: str) -> None: ...
    def stop(self) -> None: ...
