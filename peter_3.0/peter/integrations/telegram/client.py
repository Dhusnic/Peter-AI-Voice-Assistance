"""Telegram Bot API, over stdlib HTTP.

No new dependency. The Bot API is a handful of JSON-over-HTTPS calls and
`urllib.request` handles it fine; pulling in `requests` for three endpoints
would be the tail wagging the dog.

Two things about this API worth knowing before reading the code:

**getUpdates is a long poll, not a poll.** Passing `timeout=25` holds one HTTP
request open for up to 25 seconds and returns the moment a message arrives.
That is both cheaper and far more responsive than asking every second, so the
"poll interval" you would expect to find here does not exist — the loop simply
calls this again as soon as it returns.

**Updates must be acknowledged by offset.** Telegram re-delivers an update
until you ask for one with a higher `update_id`. Getting this wrong means
either replaying every old message on restart, or silently dropping messages;
the bridge advances the offset only after a message has been handled.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from peter.core.errors import AuthError, IntegrationError

log = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"


@dataclass(slots=True)
class Update:
    """One inbound message, reduced to what Peter actually uses."""

    update_id: int
    chat_id: int
    text: str
    sender: str = ""


class TelegramClient:
    def __init__(
        self,
        token: str,
        timeout_seconds: float = 40.0,
        max_message_chars: int = 3800,
    ):
        if not token:
            raise AuthError(
                "no bot token", service="telegram",
                user_action="Set TELEGRAM_BOT_TOKEN in .env. Get one from @BotFather.",
            )
        self._token = token
        self.timeout = timeout_seconds
        self.max_message_chars = max_message_chars

    # ------------------------------------------------------------- transport
    def _call(self, method: str, payload: dict[str, Any], timeout: float | None = None) -> Any:
        url = f"{API_ROOT}/bot{self._token}/{method}"
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = _error_detail(exc)
            if exc.code in (401, 403):
                raise AuthError(
                    f"telegram rejected the token: {detail}",
                    service="telegram",
                    user_action="Check TELEGRAM_BOT_TOKEN in .env against @BotFather.",
                ) from exc
            raise IntegrationError(
                f"telegram {method} failed: {exc.code} {detail}",
                service="telegram",
                # 429 is a rate limit and 5xx is Telegram's own problem; both
                # come good on their own. A 400 is a bug in the request.
                recoverable=exc.code == 429 or exc.code >= 500,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise IntegrationError(
                f"telegram unreachable: {exc}", service="telegram", recoverable=True
            ) from exc
        except json.JSONDecodeError as exc:
            raise IntegrationError(
                f"telegram returned something that is not JSON: {exc}",
                service="telegram", recoverable=True,
            ) from exc

        if not parsed.get("ok"):
            raise IntegrationError(
                f"telegram {method}: {parsed.get('description', 'refused')}",
                service="telegram",
            )
        return parsed.get("result")

    # --------------------------------------------------------------- methods
    def me(self) -> str:
        """The bot's @username. Also the cheapest liveness check there is."""
        result = self._call("getMe", {}, timeout=15) or {}
        return f"@{result.get('username', '?')}"

    def send(self, chat_id: int, text: str) -> bool:
        """Send one message. Long text is truncated, never split into a flood."""
        text = (text or "").strip() or "(nothing to say)"
        if len(text) > self.max_message_chars:
            text = text[: self.max_message_chars - 20].rstrip() + "\n\n[…truncated]"
        try:
            self._call(
                "sendMessage",
                # No parse_mode: Peter's replies contain underscores, asterisks
                # and brackets from file paths and code, and Telegram rejects
                # the whole message when they do not form valid Markdown.
                {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                timeout=20,
            )
            return True
        except IntegrationError:
            log.debug("telegram send to %s failed", chat_id, exc_info=True)
            return False

    def get_updates(self, offset: int, long_poll_seconds: int = 25) -> list[Update]:
        """Long-poll for new messages. Blocks up to `long_poll_seconds`."""
        raw = self._call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": long_poll_seconds,
                # Anything else (edits, channel posts, callbacks) is noise here
                # and asking for it only means filtering it out again.
                "allowed_updates": ["message"],
            },
            # Outlive the server-side wait, or every long poll ends in a
            # client timeout and the offset never advances.
            timeout=long_poll_seconds + 15,
        ) or []

        updates: list[Update] = []
        for item in raw:
            message = item.get("message") or {}
            text = (message.get("text") or "").strip()
            chat_id = (message.get("chat") or {}).get("id")
            if not text or chat_id is None:
                # Still counts as seen — a photo with no caption must not wedge
                # the offset and get re-delivered forever.
                updates.append(Update(int(item["update_id"]), int(chat_id or 0), ""))
                continue
            sender = message.get("from") or {}
            updates.append(
                Update(
                    update_id=int(item["update_id"]),
                    chat_id=int(chat_id),
                    text=text,
                    sender=sender.get("username") or sender.get("first_name") or "",
                )
            )
        return updates


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        return json.loads(exc.read().decode("utf-8")).get("description", str(exc))
    except Exception:
        return str(exc)
