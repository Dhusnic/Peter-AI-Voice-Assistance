"""Desktop toast notifications — a nicety, never a failure path.

Centralised here because three proactive features (reminders, meeting prep,
the inbox digest, focus mode) all want the same "also pop a toast" behind
their spoken announcement, and none of them should have to know how that is
done or what happens when it is unavailable (no notification backend on this
platform, permissions denied, whatever else).
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def notify(title: str, message: str) -> None:
    try:
        from plyer import notification

        notification.notify(title=title, message=message, timeout=20)
    except Exception:  # a toast is a nicety, never a reason to fail a job
        log.debug("desktop notification unavailable", exc_info=True)
