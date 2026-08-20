"""Talking to Peter from your phone.

A background thread long-polling the Telegram Bot API, handing each message to
the same `Peter.handle()` the microphone and the CLI use. Same brain, same
tools, same policy gate — the transport is the only thing that differs.

Three decisions worth stating outright, because all three are security
decisions rather than design taste:

**An unknown chat gets no reply at all.** A bot token is effectively a public
endpoint: anyone who finds the bot's name can message it. Replying "you are not
authorised" confirms the bot is alive and worth attacking. Silence does not.

**The backlog is dropped at startup.** Telegram holds undelivered updates for
24 hours. Without this, every message sent while Peter was off would execute
in a burst the moment it came back — including anything that was already
handled by hand hours ago.

**Confirmations cannot be answered over Telegram.** A `confirm`-tier tool
reads the local console or the microphone, neither of which the phone can
reach. Rather than let a remote turn hang for the confirmation timeout, the
gate is given a confirmer that declines immediately and says why, which comes
back to the phone as a normal refusal Peter can explain.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from peter.core.errors import AuthError, IntegrationError
from peter.integrations import telegram

log = logging.getLogger(__name__)

# Backoff between failed poll attempts, so a dropped wifi connection does not
# turn into a hot loop against Telegram's API.
_BACKOFF_START = 5.0
_BACKOFF_MAX = 120.0


class TelegramBridge:
    """Owns the polling thread. One per process."""

    def __init__(self, config, handler: Callable[[str], str]):
        self.config = config
        self.cfg = config.integrations.telegram
        self.handler = handler
        self.stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset = 0
        self._client = None
        # Chat ids already refused, so a stranger repeatedly messaging the bot
        # produces one log line rather than one per message.
        self._refused: set[int] = set()

    # ------------------------------------------------------------- lifecycle
    def start(self) -> bool:
        """Start polling. Returns False (with a reason logged) if it cannot."""
        if not self.cfg.enabled:
            return False
        if not self.config.secrets.has_telegram:
            log.debug("telegram bridge: no bot token set, not starting")
            return False
        if not self.cfg.allowed_chat_ids:
            log.warning(
                "telegram bridge: a bot token is set but no allowed_chat_ids — "
                "run `python -m peter.main --telegram-setup` to find yours"
            )
            return False

        try:
            self._client = telegram.client(self.config)
            username = self._client.me()
        except (AuthError, IntegrationError) as exc:
            log.warning("telegram bridge: not starting — %s", exc)
            return False

        self._drop_backlog()
        self._thread = threading.Thread(
            target=self._run, name="telegram-bridge", daemon=True
        )
        self._thread.start()
        log.info(
            "telegram bridge: listening as %s for %d chat(s)",
            username, len(self.cfg.allowed_chat_ids),
        )
        return True

    def stop(self) -> None:
        self.stopping.set()
        # Not joined: the thread is parked inside a long poll that can take up
        # to `long_poll_seconds` to return, and making shutdown wait half a
        # minute for it would be worse than letting a daemon thread die.

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------ loop
    def _drop_backlog(self) -> None:
        """Acknowledge everything already queued without acting on any of it."""
        try:
            pending = self._client.get_updates(offset=0, long_poll_seconds=0)
        except IntegrationError:
            log.debug("telegram bridge: could not read the backlog", exc_info=True)
            return
        if pending:
            self._offset = max(u.update_id for u in pending) + 1
            log.info("telegram bridge: skipped %d queued message(s)", len(pending))

    def _run(self) -> None:
        backoff = _BACKOFF_START
        while not self.stopping.is_set():
            try:
                updates = self._client.get_updates(
                    offset=self._offset, long_poll_seconds=self.cfg.long_poll_seconds
                )
                backoff = _BACKOFF_START
            except IntegrationError as exc:
                log.info("telegram bridge: %s (retrying in %.0fs)", exc, backoff)
                self.stopping.wait(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
                continue
            except Exception:
                log.exception("telegram bridge: unexpected polling failure")
                self.stopping.wait(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
                continue

            for update in updates:
                # Advance first: a message that crashes the handler must not be
                # re-delivered on the next poll and crash it again forever.
                self._offset = max(self._offset, update.update_id + 1)
                if self.stopping.is_set():
                    return
                try:
                    self._handle(update)
                except Exception:
                    log.exception("telegram bridge: failed handling a message")

    def _handle(self, update) -> None:
        if not update.text:
            return
        if update.chat_id not in self.cfg.allowed_chat_ids:
            if update.chat_id not in self._refused:
                self._refused.add(update.chat_id)
                log.warning(
                    "telegram bridge: ignoring messages from chat %s (%s) — add it "
                    "to integrations.telegram.allowed_chat_ids to allow it",
                    update.chat_id, update.sender or "unknown",
                )
            return

        text = _strip_command(update.text)
        if not text:
            self._client.send(
                update.chat_id,
                "Peter here. Just type what you want — the same things you would "
                "say out loud.",
            )
            return

        log.info("telegram from %s: %s", update.sender or update.chat_id, text)
        reply = self.handler(text)
        self._client.send(update.chat_id, reply or "(no reply)")


class RemoteConfirmer:
    """The confirmer used while a turn is being driven from Telegram.

    A `confirm`-tier tool would otherwise sit waiting on a console prompt
    nobody is standing in front of. Declining immediately turns that into a
    normal, explainable refusal instead of a turn that appears to hang for the
    full confirmation timeout and then declines anyway.
    """

    decline_message = (
        "This action needs confirming at the machine itself and the request "
        "came in remotely, so it was not run. Tell the user plainly that it "
        "needs doing at the desk, and do not retry it."
    )

    def ask(self, prompt: str, timeout: float) -> bool:  # noqa: ARG002
        log.info("telegram: auto-declining %s (needs local confirmation)", prompt)
        return False


def _strip_command(text: str) -> str:
    """Turn Telegram's slash commands into plain text.

    `/start` and `/help` are what a Telegram client sends when you first open a
    bot; they are not something to hand to the model.
    """
    stripped = text.strip()
    if stripped.lower().split()[0] in ("/start", "/help"):
        return ""
    if stripped.startswith("/ask "):
        return stripped[5:].strip()
    return stripped


def find_chat_ids(config, seconds: float = 60.0) -> list[tuple[int, str]]:
    """Poll for messages and report which chats they came from.

    Backs `--telegram-setup`. A chat id is not discoverable any other way: you
    have to message the bot once and read the id off the update.
    """
    api = telegram.client(config)
    deadline = time.monotonic() + seconds
    seen: dict[int, str] = {}
    offset = 0
    while time.monotonic() < deadline and not seen:
        updates = api.get_updates(offset=offset, long_poll_seconds=10)
        for update in updates:
            offset = max(offset, update.update_id + 1)
            if update.chat_id:
                seen.setdefault(update.chat_id, update.sender or "you")
    return sorted(seen.items())
