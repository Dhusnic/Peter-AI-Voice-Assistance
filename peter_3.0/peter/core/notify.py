"""Getting a proactive message in front of the user — never a failure path.

Centralised here because every proactive feature (reminders, meeting prep, the
inbox digest, focus mode, price alerts, CI failures) wants the same "also push
this somewhere visible" behind its spoken announcement, and none of them
should have to know how that is done or what happens when it is unavailable.

Two channels, tried independently so one being broken never suppresses the
other:

    desktop toast    instant, free, and only exists if you are at the machine
    Telegram         finds you anywhere, which is the entire point of it

Both are best-effort. A notification that fails must never fail the scheduled
job that raised it — a reminder that fires and speaks but cannot pop a toast
has still done its job.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def notify(title: str, message: str) -> None:
    """Push an announcement to every configured channel."""
    _toast(title, message)
    _telegram(title, message)


def _toast(title: str, message: str) -> None:
    try:
        from plyer import notification

        notification.notify(title=title, message=message, timeout=20)
    except Exception:  # a toast is a nicety, never a reason to fail a job
        log.debug("desktop notification unavailable", exc_info=True)


def _telegram(title: str, message: str) -> None:
    try:
        from peter.integrations import telegram

        telegram.push(title, message)
    except Exception:
        log.debug("telegram notification unavailable", exc_info=True)
