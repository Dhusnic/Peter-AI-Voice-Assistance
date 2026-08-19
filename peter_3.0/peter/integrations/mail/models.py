"""Mail domain objects.

Deliberately separate from the IMAP client, so tools depend on a stable shape
rather than on whatever `email.message.Message` happens to expose. It also makes
the tools testable without a mail server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class EmailSummary:
    """Header-level view of a message — what a list request returns."""

    uid: str
    sender: str
    sender_email: str
    subject: str
    date: datetime | None
    snippet: str = ""
    unread: bool = True
    starred: bool = False
    has_attachments: bool = False

    def when(self) -> str:
        if self.date is None:
            return "unknown date"
        now = datetime.now(self.date.tzinfo)
        delta = now - self.date
        if delta.days == 0:
            return self.date.strftime("%I:%M %p").lstrip("0")
        if delta.days == 1:
            return "yesterday"
        if delta.days < 7:
            return self.date.strftime("%A")
        return self.date.strftime("%d %b")

    def spoken(self) -> str:
        """One line, read aloud. Sender first — it is what you listen for."""
        return f"{self.sender}: {self.subject} ({self.when()})"


@dataclass(slots=True)
class EmailMessage(EmailSummary):
    """A full message, body included."""

    body: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        lines = [
            f"From: {self.sender} <{self.sender_email}>",
            f"Subject: {self.subject}",
            f"Date: {self.date.strftime('%a %d %b %Y, %I:%M %p') if self.date else '?'}",
        ]
        if self.to:
            lines.append(f"To: {', '.join(self.to)}")
        if self.attachments:
            lines.append(f"Attachments: {', '.join(self.attachments)}")
        lines.append("")
        lines.append(self.body)
        return "\n".join(lines)
