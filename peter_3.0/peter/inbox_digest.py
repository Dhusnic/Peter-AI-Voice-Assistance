"""Inbox triage digest — read-only, on-demand or on a schedule.

Distinguishes "this needs your attention" from routine mail. A keyword
heuristic ("contains a question mark") is not good enough to separate an HR
reminder from a production incident, so the split is a genuine model call —
but a tiny, isolated, tool-free one against a plain sender/subject list, not
a turn in the live conversation, so it costs a few hundred tokens rather than
a full agent turn. It never drafts or sends anything; that stays a human
decision.

The same `build_digest()` backs both the inbox_digest tool (on demand) and
the scheduled watcher (proactive), so the two never behave differently.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_CLASSIFY_SYSTEM = (
    "You triage a personal inbox from a numbered sender/subject list. For "
    "each email that plausibly needs a reply from the user — a direct "
    "question, an approval request, an escalation, an action item addressed "
    "to them — reply with one line: the number, a colon, then a short two to "
    "five word label for it (not the raw subject). Skip newsletters, "
    "automated alerts with nothing to do, marketing, and FYI-only threads. "
    "If nothing needs a response, reply with exactly: none. No other text, "
    "no preamble, no explanation."
)

_LINE = re.compile(r"^\s*(\d+)\s*:\s*(.+?)\s*$")


@dataclass(frozen=True, slots=True)
class Digest:
    unread: int
    needs_response: list[str] = field(default_factory=list)

    def spoken(self) -> str:
        if self.unread == 0:
            return "Inbox is clear."
        text = f"You have {self.unread} unread email{'s' if self.unread != 1 else ''}."
        n = len(self.needs_response)
        if n == 1:
            text += f" One looks like it needs a response: {self.needs_response[0]}."
        elif n > 1:
            text += (
                f" {n} look like they need a response: "
                f"{', '.join(self.needs_response)}."
            )
        return text


def build_digest(max_emails: int = 15) -> Digest:
    """Fetch unread mail and classify it. Raises if mail is not reachable —
    callers that must never fail (the scheduled watcher) catch around this;
    the on-demand tool lets it propagate like every other mail tool does."""
    from peter.core.services import services

    mail = services().mail()
    unread = mail.count_unread()
    if unread == 0:
        return Digest(unread=0)

    recent = mail.list_messages("UNSEEN", limit=max_emails)
    labels = _classify(recent)
    return Digest(unread=unread, needs_response=labels)


def _classify(messages) -> list[str]:
    """Best-effort. Any failure here degrades to "no labels", not a crash —
    the unread count alone is still a useful digest."""
    if not messages:
        return []
    try:
        from peter.core.services import services
        from peter.llm import factory

        config = services().config
        listing = "\n".join(
            f"{i}. {m.sender}: {m.subject}" for i, m in enumerate(messages, start=1)
        )
        provider = factory.build_provider(config, _CLASSIFY_SYSTEM)
        try:
            provider.add_user(listing)
            response = provider.complete([])
        finally:
            provider.close()
    except Exception:
        log.debug("inbox digest: classification unavailable", exc_info=True)
        return []

    text = (response.text or "").strip()
    if not text or text.lower() == "none":
        return []

    labels = []
    for line in text.splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        index = int(match.group(1))
        if 1 <= index <= len(messages):
            labels.append(match.group(2))
    return labels


# Last unread count actually spoken, so the watcher does not repeat itself
# every poll while nothing has changed. Lost on restart — harmless, worst
# case is one repeated announcement.
_last_announced: int | None = None


def check_inbox_digest() -> None:
    """Scheduler job target. Must stay importable at this exact path."""
    from peter.core.errors import NotConfiguredError, PeterError
    from peter.core.notify import notify
    from peter.core.services import services

    global _last_announced
    container = services()
    cfg = container.config.integrations.inbox_digest
    if not cfg.enabled:
        return

    try:
        digest = build_digest(cfg.max_emails)
    except NotConfiguredError:
        return
    except PeterError:
        log.info("inbox digest: mail unreachable this poll", exc_info=True)
        return
    except Exception:
        log.exception("inbox digest: unexpected failure")
        return

    if digest.unread == _last_announced:
        return  # nothing new since the last check — do not nag
    _last_announced = digest.unread

    text = digest.spoken()
    log.info("inbox digest: %s", text)
    container.say(text)
    if digest.needs_response:
        notify("Peter — inbox", text)


def schedule_inbox_digest(scheduler, config) -> None:
    """Install (or re-install) the inbox-digest poll."""
    cfg = config.integrations.inbox_digest
    if not cfg.enabled:
        return
    scheduler.add_interval_job(
        job_id="inbox-digest-poll",
        minutes=cfg.poll_interval_minutes,
        func=check_inbox_digest,
        name="inbox digest watcher",
    )
    log.info(
        "inbox digest watcher: polling every %d minute(s)", cfg.poll_interval_minutes
    )
