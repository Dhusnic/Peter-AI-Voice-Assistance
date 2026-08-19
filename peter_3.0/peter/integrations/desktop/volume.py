"""System master volume, via pycaw. Windows-only, like the rest of desktop/.

Split out from tools/system.py because focus mode needs to *read* the current
level before muting, so it can put it back — the set_volume tool only ever
needed to write.
"""

from __future__ import annotations


def get() -> int | None:
    """Current master volume, 0-100, or None if it could not be read."""
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        endpoint = interface.QueryInterface(IAudioEndpointVolume)
        return round(endpoint.GetMasterVolumeLevelScalar() * 100)
    except Exception:
        return None


def set(percent: int) -> bool:
    """Set the master volume, 0-100. Returns whether it actually worked."""
    level = max(0, min(100, int(percent)))
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        endpoint = interface.QueryInterface(IAudioEndpointVolume)
        endpoint.SetMasterVolumeLevelScalar(level / 100.0, None)
        return True
    except Exception:
        return False
