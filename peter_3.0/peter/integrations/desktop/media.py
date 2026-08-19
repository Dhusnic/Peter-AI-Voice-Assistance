"""Playback control, via the media keys a keyboard would send.

These are the same virtual keys as the play/pause and track buttons on a media
keyboard, injected with `keybd_event`. Windows routes them to whichever
application currently holds media focus, which is exactly the behaviour wanted
here: it controls YouTube in the browser, Spotify, VLC or anything else,
without Peter needing to know which is playing or to talk to any of them.

The alternative — driving the YouTube page through Playwright — would only work
for YouTube, only in Peter's own browser instance, and would break whenever the
page markup changed.
"""

from __future__ import annotations

import ctypes
import logging
import time

log = logging.getLogger(__name__)

# Virtual-key codes. See learn.microsoft.com "Virtual-Key Codes".
_VK = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
    "mute": 0xAD,
    "volume_down": 0xAE,
    "volume_up": 0xAF,
}

_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002

ACTIONS = tuple(_VK)


def send(action: str, repeat: int = 1) -> bool:
    """Press a media key. Returns False if the action is not one we know."""
    code = _VK.get(action.strip().lower().replace(" ", "_"))
    if code is None:
        return False

    user32 = ctypes.windll.user32
    for index in range(max(1, repeat)):
        if index:
            # Volume steps sent back-to-back get coalesced; a short gap makes
            # each one register.
            time.sleep(0.04)
        user32.keybd_event(code, 0, _KEYEVENTF_EXTENDEDKEY, 0)
        user32.keybd_event(code, 0, _KEYEVENTF_EXTENDEDKEY | _KEYEVENTF_KEYUP, 0)
    log.debug("sent media key %s x%d", action, repeat)
    return True
