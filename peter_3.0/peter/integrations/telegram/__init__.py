"""Telegram integration: outbound push, and the inbound bridge next door.

`push()` lives here rather than in the bridge because the two have opposite
lifetimes. The bridge is a thread that only exists while Peter is running an
interactive session; push is called from scheduler jobs, tools, and
`peter/core/notify.py`, none of which know or care whether that thread exists.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()


def configured(config) -> bool:
    cfg = config.integrations.telegram
    return bool(
        cfg.enabled and config.secrets.has_telegram and cfg.allowed_chat_ids
    )


def client(config):
    """The process-wide client, built once.

    Raises AuthError when no token is set; callers that must not fail
    (`push()`) catch. Callers that should report the problem (the tools, the
    health check) let it through.
    """
    global _client
    with _client_lock:
        if _client is None:
            from peter.integrations.telegram.api import TelegramClient

            cfg = config.integrations.telegram
            _client = TelegramClient(
                token=config.secrets.telegram_token,
                timeout_seconds=cfg.timeout_seconds,
                max_message_chars=cfg.max_message_chars,
            )
    return _client


def push(title: str, message: str) -> int:
    """Send a proactive announcement to every allowed chat.

    Never raises and never blocks for long: this sits behind desktop toasts on
    the path of every scheduled job, and a flaky network must not be able to
    fail a reminder. Returns how many chats it reached.
    """
    from peter.core.services import services

    try:
        config = services().config
    except Exception:  # pragma: no cover - no container yet, e.g. very early boot
        return 0

    cfg = config.integrations.telegram
    if not (configured(config) and cfg.forward_notifications):
        return 0

    text = f"{title}\n\n{message}" if title else message
    sent = 0
    try:
        api = client(config)
    except Exception:
        log.debug("telegram push unavailable", exc_info=True)
        return 0

    for chat_id in cfg.allowed_chat_ids:
        try:
            if api.send(chat_id, text):
                sent += 1
        except Exception:
            log.debug("telegram push to %s failed", chat_id, exc_info=True)
    return sent


def reset_for_tests() -> None:
    global _client
    with _client_lock:
        _client = None
