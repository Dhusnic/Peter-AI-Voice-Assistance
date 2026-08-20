"""Mail you sent that nobody ever answered.

The thing that actually falls through the cracks is not the mail in your inbox
— that is visible, and the digest already covers it. It is the message *you*
sent a week ago that got no reply, which is invisible by construction: nothing
in a mail client shows you an absence.

**How a reply is detected.** For each message in Sent, the base subject
(without any `Re:` / `Fwd:` prefixes) is searched for in the inbox, and a hit
newer than the sent message counts as a reply. IMAP's SUBJECT search is a
substring match, so "Re: Azure feature discussion" matches a search for
"Azure feature discussion" without any thread-id handling.

That is a heuristic and it has two honest failure modes: a reply whose subject
was rewritten is missed (reported as still waiting when it is not), and an
unrelated message that happens to share a subject line counts as a reply
(reported as answered when it is not). Both are acceptable for a read-only
nudge; neither would be for anything that acted on the result.

Read-only, entirely. It never nudges anyone, never drafts a follow-up, and
never sends anything — it just tells you what is outstanding.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

# "Re:", "RE :", "Fwd:", "FW:", any number of times, in any order.
_PREFIX = re.compile(r"^\s*((re|fwd?|fw|aw|sv)\s*:\s*)+", re.I)

# Characters IMAP would take as syntax inside a quoted search string.
_UNSAFE = str.maketrans({'"': " ", "\\": " ", "\r": " ", "\n": " "})


@dataclass(slots=True)
class Outstanding:
    subject: str
    sent_at: datetime | None
    days: int

    def spoken(self) -> str:
        when = f"{self.days} day{'s' if self.days != 1 else ''} ago"
        return f"{self.subject} — sent {when}"


def base_subject(subject: str) -> str:
    """Strip reply/forward prefixes down to the thread's actual subject."""
    return _PREFIX.sub("", subject or "").strip()


def build_waiting_on(quiet_days: int = 0, lookback_days: int = 0) -> list[Outstanding]:
    """Sent messages older than `quiet_days` with no reply found.

    Raises if mail is unreachable — like every other mail tool. The briefing
    catches around it; the tool lets it surface.
    """
    from peter.core.services import services

    container = services()
    cfg = container.config.integrations.waiting_on
    mail_cfg = container.config.integrations.mail
    quiet_days = quiet_days or cfg.quiet_days
    lookback_days = lookback_days or cfg.lookback_days

    mail = container.mail()
    now = datetime.now().astimezone()
    since = (now - timedelta(days=lookback_days)).strftime("%d-%b-%Y")

    sent = mail.list_messages(
        f"SINCE {since}", limit=cfg.max_messages, folder=mail_cfg.sent_folder
    )

    cutoff = now - timedelta(days=quiet_days)
    outstanding: list[Outstanding] = []
    seen: set[str] = set()

    for message in sent:
        subject = base_subject(message.subject)
        if not subject or len(subject) < 4:
            continue
        # Only the newest message per thread is worth reporting: if you sent
        # three mails in one thread, it is one thing you are waiting on.
        key = subject.lower()
        if key in seen:
            continue
        seen.add(key)

        if message.date is not None and message.date > cutoff:
            continue  # too recent to count as unanswered

        if _has_reply(mail, subject, message.date, mail_cfg.inbox_folder):
            continue

        days = (now - message.date).days if message.date else quiet_days
        outstanding.append(
            Outstanding(subject=message.subject, sent_at=message.date, days=days)
        )

    outstanding.sort(key=lambda item: item.days, reverse=True)
    return outstanding


def _has_reply(mail, subject: str, sent_at: datetime | None, inbox: str) -> bool:
    """Is there an inbox message with this subject, newer than the sent one?"""
    needle = subject.translate(_UNSAFE).strip()
    if not needle:
        return False
    try:
        replies = mail.list_messages(f'SUBJECT "{needle}"', limit=5, folder=inbox)
    except Exception:
        # A failed search must not turn into "everything is outstanding" — the
        # conservative reading of "I could not check" is "assume answered".
        log.debug("waiting-on: reply search failed for %r", subject, exc_info=True)
        return True

    if sent_at is None:
        return bool(replies)
    return any(reply.date is not None and reply.date > sent_at for reply in replies)


def spoken_summary(items: list[Outstanding], limit: int = 4) -> str:
    """One paragraph, for the briefing or a spoken answer."""
    if not items:
        return "Nothing is waiting on a reply."
    listing = "; ".join(item.spoken() for item in items[:limit])
    more = f", and {len(items) - limit} more" if len(items) > limit else ""
    count = f"{len(items)} message{'s' if len(items) != 1 else ''}"
    return f"{count} you sent got no reply: {listing}{more}."
