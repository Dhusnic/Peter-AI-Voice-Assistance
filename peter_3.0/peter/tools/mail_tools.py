"""Email tools.

Everything returns text shaped to be read aloud: sender first, then subject,
then when. Nobody wants a message-id spoken at them, so the uid appears only as
a short bracketed handle Claude can pass back to `read_email`.

Sending is `write` tier and always confirms. An assistant that can send mail on
a misheard sentence is a liability, and unlike a deleted file, a sent email
cannot be recovered.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from peter.agent.registry import peter_tool
from peter.core.services import services

_MAX_LISTED = 15


def _format(summaries, header: str) -> str:
    if not summaries:
        return "Nothing to report."
    lines = [header]
    for item in summaries[:_MAX_LISTED]:
        star = "* " if item.starred else ""
        lines.append(f"[{item.uid}] {star}{item.spoken()}")
    if len(summaries) > _MAX_LISTED:
        lines.append(f"... and {len(summaries) - _MAX_LISTED} more")
    return "\n".join(lines)


@peter_tool(tier="read")
def check_email(limit: int = 10) -> str:
    """List unread messages in the inbox, newest first.

    Summarise these out loud rather than reading every line — say how many there
    are and name the few that look important. Use read_email for the full text
    of one of them.

    Args:
        limit: Maximum number to fetch.
    """
    mail = services().mail()
    unread = mail.list_messages("UNSEEN", limit=limit)
    if not unread:
        return "No unread email."
    return _format(unread, f"{len(unread)} unread:")


@peter_tool(tier="read")
def count_unread_email() -> str:
    """Count unread messages without fetching them. Fast — use it for briefings."""
    count = services().mail().count_unread()
    if count == 0:
        return "No unread email."
    return f"{count} unread message{'s' if count != 1 else ''}."


@peter_tool(tier="read")
def search_email(
    sender: str = "",
    subject: str = "",
    text: str = "",
    days: int = 0,
    unread_only: bool = False,
    limit: int = 10,
) -> str:
    """Search the mailbox. Combine any of the filters; they are ANDed together.

    Args:
        sender: Match the From address or name, e.g. "amma" or "hdfc".
        subject: Match words in the subject line.
        text: Match anywhere in the message, including the body.
        days: Only messages from the last N days. 0 means no date limit.
        unread_only: True to restrict to unread messages.
        limit: Maximum number of results.
    """
    criteria: list[str] = []
    if unread_only:
        criteria.append("UNSEEN")
    if sender.strip():
        criteria.append(f'FROM "{_escape(sender)}"')
    if subject.strip():
        criteria.append(f'SUBJECT "{_escape(subject)}"')
    if text.strip():
        criteria.append(f'TEXT "{_escape(text)}"')
    if days > 0:
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        criteria.append(f"SINCE {since}")

    query = " ".join(criteria) if criteria else "ALL"
    results = services().mail().list_messages(query, limit=limit)
    if not results:
        return "No messages match that."
    return _format(results, f"{len(results)} match:")


@peter_tool(tier="read")
def read_email(email_id: str) -> str:
    """Read one message in full, including its body.

    Does not mark it read — use mark_email_read for that, and only when the user
    asks.

    Args:
        email_id: The bracketed id from check_email or search_email.
    """
    message = services().mail().get_message(email_id.strip())
    return message.as_text()


@peter_tool(tier="write")
def mark_email_read(email_id: str) -> str:
    """Mark a message as read.

    Args:
        email_id: The id from check_email or search_email.
    """
    services().mail().set_flag(email_id.strip(), "\\Seen", on=True)
    return "Marked read."


@peter_tool(tier="write")
def star_email(email_id: str, starred: bool = True) -> str:
    """Star or unstar a message, to flag it for later.

    Args:
        email_id: The id from check_email or search_email.
        starred: True to star, False to remove the star.
    """
    services().mail().set_flag(email_id.strip(), "\\Flagged", on=starred)
    return "Starred." if starred else "Star removed."


@peter_tool(tier="write")
def archive_email(email_id: str) -> str:
    """Archive a message — removes it from the inbox but keeps it searchable.

    Args:
        email_id: The id from check_email or search_email.
    """
    services().mail().archive(email_id.strip())
    return "Archived."


@peter_tool(tier="write")
def delete_email(email_id: str) -> str:
    """Move a message to Trash.

    Prefer archive_email unless the user specifically says delete.

    Args:
        email_id: The id from check_email or search_email.
    """
    services().mail().trash(email_id.strip())
    return "Moved to Trash."


@peter_tool(tier="write")
def send_email(
    to: str, subject: str, body: str, cc: str = "", reply_to_id: str = ""
) -> str:
    """Send an email.

    Read the recipient, subject and the full body back to the user before
    sending, and let the confirmation prompt do the rest. This cannot be undone.

    Args:
        to: Recipient addresses, comma-separated.
        subject: Subject line.
        body: The message text. Plain text only.
        cc: Optional CC addresses, comma-separated.
        reply_to_id: If replying, the id of the message being replied to, so the
            reply threads correctly.
    """
    recipients = _addresses(to)
    if not recipients:
        return "No valid recipient address given."

    invalid = [a for a in recipients if "@" not in a]
    if invalid:
        return f"These do not look like email addresses: {', '.join(invalid)}"

    services().mail().send(
        to=recipients,
        subject=subject,
        body=body,
        cc=_addresses(cc),
        reply_to_uid=reply_to_id.strip() or None,
    )
    return f"Sent to {', '.join(recipients)}."


def _addresses(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _escape(value: str) -> str:
    """IMAP search strings are quoted; a stray quote would break the query."""
    return value.replace("\\", "").replace('"', "").strip()
