"""Turning raw RFC-822 bytes into something worth reading aloud.

Email is a hostile format: mislabelled charsets, RFC 2047 encoded headers,
nested multiparts, and HTML bodies wrapped in tracking pixels and CSS. This
module exists so none of that leaks into the tool layer.
"""

from __future__ import annotations

import email
import email.utils
import re
from datetime import datetime
from email.header import decode_header, make_header
from email.message import Message

from peter.integrations.mail.models import EmailMessage, EmailSummary

_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")
# Quoted reply chains and signatures: everything after these is noise when the
# point is "what does this email say".
_QUOTE_MARKERS = (
    re.compile(r"^\s*On .{5,80} wrote:\s*$", re.MULTILINE),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.MULTILINE | re.I),
    re.compile(r"^\s*_{10,}\s*$", re.MULTILINE),
    re.compile(r"^\s*--\s*$", re.MULTILINE),
)


def decode_mime_header(raw: str | None) -> str:
    """Decode an RFC 2047 header (`=?utf-8?B?...?=`) to plain text."""
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except (UnicodeDecodeError, LookupError, ValueError):
        return raw.strip()


def split_address(raw: str | None) -> tuple[str, str]:
    """`"Amma" <amma@example.com>` -> ("Amma", "amma@example.com")."""
    if not raw:
        return ("unknown", "")
    name, address = email.utils.parseaddr(decode_mime_header(raw))
    if not name:
        # Fall back to the local part, which reads better aloud than an address.
        name = address.split("@")[0].replace(".", " ").title() if address else "unknown"
    return (name.strip(), address.strip().lower())


def parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def html_to_text(html: str) -> str:
    try:
        import html2text

        converter = html2text.HTML2Text()
        converter.ignore_links = True
        converter.ignore_images = True
        converter.ignore_emphasis = True
        converter.body_width = 0
        return converter.handle(html)
    except Exception:
        # Crude fallback beats returning raw markup to a text-to-speech engine.
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html,
                      flags=re.DOTALL | re.I)
        text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        return _unescape(text)


def _unescape(text: str) -> str:
    import html as html_module

    return html_module.unescape(text)


def _decode_payload(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        # Mislabelled charset — very common in real mail.
        return payload.decode("utf-8", errors="replace")


def extract_body(message: Message, limit: int) -> tuple[str, list[str]]:
    """Best-effort plain text body plus attachment filenames.

    Prefers text/plain; falls back to converting text/html. Returns the body
    trimmed to `limit` characters.
    """
    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[str] = []

    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue
            disposition = str(part.get("Content-Disposition") or "")
            filename = part.get_filename()
            if "attachment" in disposition.lower() or filename:
                if filename:
                    attachments.append(decode_mime_header(filename))
                continue

            content_type = part.get_content_type()
            if content_type == "text/plain":
                plain_parts.append(_decode_payload(part))
            elif content_type == "text/html":
                html_parts.append(_decode_payload(part))
    else:
        content = _decode_payload(message)
        if message.get_content_type() == "text/html":
            html_parts.append(content)
        else:
            plain_parts.append(content)

    raw = "\n".join(plain_parts).strip()
    if not raw and html_parts:
        raw = html_to_text("\n".join(html_parts))

    return clean_body(raw, limit), attachments


def clean_body(raw: str, limit: int) -> str:
    """Collapse whitespace, drop quoted replies, and trim to a spoken length."""
    if not raw:
        return ""

    text = raw.replace("\r\n", "\n").replace("\r", "\n")

    # Cut at the first quoted-reply marker — the chain below is rarely the point.
    earliest = len(text)
    for pattern in _QUOTE_MARKERS:
        match = pattern.search(text)
        if match and match.start() < earliest:
            earliest = match.start()
    if earliest < len(text):
        text = text[:earliest]

    # Drop lines that are entirely a quote of the previous message.
    lines = [line for line in text.split("\n") if not line.lstrip().startswith(">")]
    text = "\n".join(lines)

    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    text = "\n".join(line.strip() for line in text.split("\n")).strip()

    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + f"\n\n[trimmed at {limit} characters]"
    return text


def parse_summary(uid: str, raw: bytes, flags: str = "") -> EmailSummary:
    message = email.message_from_bytes(raw)
    name, address = split_address(message.get("From"))
    return EmailSummary(
        uid=uid,
        sender=name,
        sender_email=address,
        subject=decode_mime_header(message.get("Subject")) or "(no subject)",
        date=parse_date(message.get("Date")),
        unread="\\Seen" not in flags,
        starred="\\Flagged" in flags,
    )


def parse_message(uid: str, raw: bytes, body_limit: int, flags: str = "") -> EmailMessage:
    message = email.message_from_bytes(raw)
    name, address = split_address(message.get("From"))
    body, attachments = extract_body(message, body_limit)

    return EmailMessage(
        uid=uid,
        sender=name,
        sender_email=address,
        subject=decode_mime_header(message.get("Subject")) or "(no subject)",
        date=parse_date(message.get("Date")),
        unread="\\Seen" not in flags,
        starred="\\Flagged" in flags,
        has_attachments=bool(attachments),
        body=body or "(this message has no readable text body)",
        to=[a for _, a in email.utils.getaddresses([message.get("To") or ""]) if a],
        cc=[a for _, a in email.utils.getaddresses([message.get("Cc") or ""]) if a],
        attachments=attachments,
    )
