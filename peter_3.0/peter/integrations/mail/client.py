"""IMAP + SMTP client.

**Why IMAP and not the Gmail API.** Gmail's API scopes are classified
*restricted*. A personal Google Cloud project stuck in "Testing" status issues
refresh tokens that expire after 7 days, and escaping that requires Google's
full verification including a third-party security audit — not realistic for a
personal assistant. An app password has no expiry, needs no OAuth dance, and
survives reboots. For *reading* mail it is strictly the better engineering
choice; the only thing given up is Gmail-specific label richness.

IMAP connections drop. They drop when the laptop sleeps, when the wifi changes,
and when Gmail decides an idle socket has been idle long enough. Every operation
here goes through `_with_connection`, which reconnects on `IMAP4.abort` rather
than surfacing a dead socket to the tool layer.
"""

from __future__ import annotations

import imaplib
import logging
import re
import smtplib
import socket
import ssl
import threading
from email.message import EmailMessage as OutgoingMessage
from typing import Callable, TypeVar

from peter.core.config import MailConfig, Secrets
from peter.core.errors import AuthError, IntegrationError
from peter.integrations.mail.models import EmailMessage, EmailSummary
from peter.integrations.mail.parsing import parse_message, parse_summary

log = logging.getLogger(__name__)

T = TypeVar("T")

# IMAP responses look like: 1 (UID 42 FLAGS (\Seen) BODY[] {2048}
_UID_RE = re.compile(rb"UID\s+(\d+)")
_FLAGS_RE = re.compile(rb"FLAGS\s+\(([^)]*)\)")

# Gmail rejects a login with this when app passwords are not set up.
_AUTH_HINTS = ("authenticationfailed", "invalid credentials", "application-specific")


class MailClient:
    def __init__(self, config: MailConfig, secrets: Secrets):
        self.config = config
        self.address = secrets.mail_address
        self._password = secrets.mail_password
        self._imap: imaplib.IMAP4_SSL | None = None
        self._selected: str | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------ plumbing
    def _connect(self) -> imaplib.IMAP4_SSL:
        try:
            context = ssl.create_default_context()
            imap = imaplib.IMAP4_SSL(
                self.config.imap_host,
                self.config.imap_port,
                ssl_context=context,
                timeout=self.config.timeout_seconds,
            )
            imap.login(self.address, self._password)
        except imaplib.IMAP4.error as exc:
            detail = str(exc).lower()
            if any(hint in detail for hint in _AUTH_HINTS):
                raise AuthError(
                    f"mail login rejected: {exc}",
                    service="mail",
                    user_action=(
                        "Check PETER_MAIL_ADDRESS and PETER_MAIL_APP_PASSWORD in "
                        ".env. The password must be a Google app password, not "
                        "your account password, and needs 2-step verification on."
                    ),
                ) from exc
            raise IntegrationError(
                f"IMAP error: {exc}", service="mail", recoverable=True
            ) from exc
        except (OSError, socket.timeout, ssl.SSLError) as exc:
            raise IntegrationError(
                f"could not reach {self.config.imap_host}: {exc}",
                service="mail",
                recoverable=True,
                user_action="Check your internet connection.",
            ) from exc

        self._selected = None
        log.debug("IMAP connected as %s", self.address)
        return imap

    def _with_connection(self, operation: Callable[[imaplib.IMAP4_SSL], T]) -> T:
        """Run an operation, reconnecting once if the socket has gone stale."""
        with self._lock:
            for attempt in (1, 2):
                if self._imap is None:
                    self._imap = self._connect()
                try:
                    return operation(self._imap)
                except (imaplib.IMAP4.abort, OSError, ssl.SSLError) as exc:
                    log.info("IMAP connection lost (%s); reconnecting", exc)
                    self._drop()
                    if attempt == 2:
                        raise IntegrationError(
                            f"mail connection failed: {exc}",
                            service="mail",
                            recoverable=True,
                        ) from exc
            raise AssertionError("unreachable")

    def _drop(self) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            except Exception:
                pass
        self._imap = None
        self._selected = None

    def _select(self, imap: imaplib.IMAP4_SSL, folder: str, readonly: bool = False):
        if self._selected != folder:
            status, data = imap.select(f'"{folder}"', readonly=readonly)
            if status != "OK":
                raise IntegrationError(
                    f"cannot open folder {folder!r}: {_first(data)}", service="mail"
                )
            self._selected = folder

    def close(self) -> None:
        with self._lock:
            self._drop()

    def ping(self) -> bool:
        """Cheap connectivity + credential check, for --health."""
        def op(imap: imaplib.IMAP4_SSL) -> bool:
            status, _ = imap.noop()
            return status == "OK"

        return self._with_connection(op)

    # -------------------------------------------------------------- reading
    def list_messages(
        self,
        criteria: str = "UNSEEN",
        limit: int | None = None,
        folder: str | None = None,
    ) -> list[EmailSummary]:
        """Search a folder and return header summaries, newest first.

        Args:
            criteria: A raw IMAP search key, e.g. "UNSEEN", "ALL",
                'FROM "amma"', 'SINCE 01-Aug-2026'.
            limit: Maximum messages to return.
            folder: Defaults to the configured inbox.
        """
        folder = folder or self.config.inbox_folder
        cap = min(limit or self.config.fetch_limit, self.config.fetch_limit)

        def op(imap: imaplib.IMAP4_SSL) -> list[EmailSummary]:
            self._select(imap, folder, readonly=True)
            status, data = imap.uid("SEARCH", None, criteria)
            if status != "OK":
                raise IntegrationError(
                    f"search failed for {criteria!r}: {_first(data)}", service="mail"
                )

            uids = (data[0] or b"").split()
            if not uids:
                return []
            uids = uids[-cap:][::-1]  # newest first

            summaries: list[EmailSummary] = []
            for uid in uids:
                status, fetched = imap.uid(
                    "FETCH",
                    uid,
                    "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])",
                )
                if status != "OK" or not fetched:
                    continue
                headers, flags = _extract(fetched)
                if headers is None:
                    continue
                summaries.append(
                    parse_summary(uid.decode(), headers, flags)
                )
            return summaries

        return self._with_connection(op)

    def get_message(self, uid: str, folder: str | None = None) -> EmailMessage:
        """Fetch one message in full, without marking it read."""
        folder = folder or self.config.inbox_folder

        def op(imap: imaplib.IMAP4_SSL) -> EmailMessage:
            self._select(imap, folder, readonly=True)
            status, fetched = imap.uid("FETCH", uid, "(FLAGS BODY.PEEK[])")
            if status != "OK" or not fetched or fetched[0] is None:
                raise IntegrationError(
                    f"no message with id {uid}", service="mail"
                )
            raw, flags = _extract(fetched)
            if raw is None:
                raise IntegrationError(
                    f"could not read message {uid}", service="mail"
                )
            return parse_message(uid, raw, self.config.body_chars, flags)

        return self._with_connection(op)

    def count_unread(self, folder: str | None = None) -> int:
        folder = folder or self.config.inbox_folder

        def op(imap: imaplib.IMAP4_SSL) -> int:
            self._select(imap, folder, readonly=True)
            status, data = imap.uid("SEARCH", None, "UNSEEN")
            if status != "OK":
                return 0
            return len((data[0] or b"").split())

        return self._with_connection(op)

    # -------------------------------------------------------------- writing
    def set_flag(self, uid: str, flag: str, on: bool = True,
                 folder: str | None = None) -> None:
        folder = folder or self.config.inbox_folder

        def op(imap: imaplib.IMAP4_SSL) -> None:
            self._select(imap, folder)
            status, data = imap.uid(
                "STORE", uid, "+FLAGS" if on else "-FLAGS", f"({flag})"
            )
            if status != "OK":
                raise IntegrationError(
                    f"could not update message {uid}: {_first(data)}", service="mail"
                )

        self._with_connection(op)

    def move(self, uid: str, destination: str, folder: str | None = None) -> None:
        """Move a message between folders.

        Gmail exposes labels as IMAP folders, so 'archive' is a move to All Mail
        and 'delete' is a move to Trash. UID MOVE is preferred; servers without
        the MOVE capability fall back to COPY + delete + expunge.
        """
        folder = folder or self.config.inbox_folder

        def op(imap: imaplib.IMAP4_SSL) -> None:
            self._select(imap, folder)
            if "MOVE" in getattr(imap, "capabilities", ()):
                status, data = imap.uid("MOVE", uid, f'"{destination}"')
                if status != "OK":
                    raise IntegrationError(
                        f"could not move message {uid}: {_first(data)}",
                        service="mail",
                    )
                return

            status, data = imap.uid("COPY", uid, f'"{destination}"')
            if status != "OK":
                raise IntegrationError(
                    f"could not copy message {uid} to {destination}: {_first(data)}",
                    service="mail",
                )
            imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            imap.expunge()

        self._with_connection(op)

    def archive(self, uid: str) -> None:
        self.move(uid, self.config.archive_folder)

    def trash(self, uid: str) -> None:
        self.move(uid, self.config.trash_folder)

    # -------------------------------------------------------------- sending
    def send(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        reply_to_uid: str | None = None,
    ) -> None:
        message = OutgoingMessage()
        message["From"] = self.address
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        message["Subject"] = subject
        message.set_content(body)

        # Threading: without In-Reply-To, a reply starts a new conversation.
        if reply_to_uid:
            try:
                original = self.get_message(reply_to_uid)
                if original.subject and not subject.lower().startswith("re:"):
                    message.replace_header("Subject", f"Re: {original.subject}")
            except IntegrationError:
                log.debug("could not load %s for threading", reply_to_uid)

        recipients = list(to) + list(cc or [])
        try:
            with smtplib.SMTP(
                self.config.smtp_host,
                self.config.smtp_port,
                timeout=self.config.timeout_seconds,
            ) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(self.address, self._password)
                smtp.send_message(message, self.address, recipients)
        except smtplib.SMTPAuthenticationError as exc:
            raise AuthError(
                f"SMTP login rejected: {exc}",
                service="mail",
                user_action="Check PETER_MAIL_APP_PASSWORD in .env.",
            ) from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise IntegrationError(
                f"could not send mail: {exc}", service="mail", recoverable=True
            ) from exc

        log.info("sent mail to %s", ", ".join(recipients))


# ----------------------------------------------------------------- helpers
def _extract(fetched: list) -> tuple[bytes | None, str]:
    """Pull the payload and flags out of an imaplib FETCH response.

    imaplib returns a ragged list — tuples for literals, bare bytes for the
    trailing parenthesis — and the shape varies by server. This normalises it.
    """
    raw: bytes | None = None
    flags = ""
    for item in fetched:
        if isinstance(item, tuple) and len(item) >= 2:
            descriptor, payload = item[0], item[1]
            if raw is None and isinstance(payload, (bytes, bytearray)):
                raw = bytes(payload)
            if isinstance(descriptor, (bytes, bytearray)):
                match = _FLAGS_RE.search(descriptor)
                if match:
                    flags = match.group(1).decode(errors="replace")
        elif isinstance(item, (bytes, bytearray)):
            match = _FLAGS_RE.search(item)
            if match:
                flags = match.group(1).decode(errors="replace")
    return raw, flags


def _first(data) -> str:
    if not data:
        return "no detail"
    head = data[0]
    if isinstance(head, (bytes, bytearray)):
        return head.decode(errors="replace")
    return str(head)
