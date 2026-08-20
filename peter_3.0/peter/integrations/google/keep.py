"""Google Keep, via the unofficial `gkeepapi` client.

**Not OAuth.** There is no official Keep API for a personal Google account —
the real one is Workspace-only, gated behind an admin granting domain-wide
delegation, which does not exist for a plain @gmail.com address. `gkeepapi`
authenticates with a master token instead: the same capability level as your
account password, not a scoped, individually revocable grant. That token is
obtained once, outside Peter, following gkeepapi's own documented method
(github.com/kiwiz/gkeepapi) — Peter automating a Google sign-in for an
unofficial client would be a worse idea than a human doing it once and
pasting the result into `.env`.

This module deliberately does not import `peter.integrations.google.auth`.
Sharing that module would misleadingly suggest Keep uses the same, safer
OAuth flow Calendar/Tasks/Contacts/Drive do. It does not, and every setup
doc for this feature says so before showing how to turn it on.

`gkeepapi`'s failure modes are its own exception hierarchy, not
googleapiclient's `HttpError`, so error translation here is a separate
function rather than a copy of calendar.py's `_call()`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from peter.core.config import Config
from peter.core.errors import AuthError, IntegrationError

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Note:
    id: str
    title: str
    text: str
    pinned: bool = False
    archived: bool = False

    def spoken(self) -> str:
        head = self.title.strip() or self.text.strip()[:60] or "(empty note)"
        return f"{head} (pinned)" if self.pinned else head


def _to_note(raw) -> Note:
    return Note(
        id=raw.id,
        title=(raw.title or "").strip(),
        text=(raw.text or "").strip(),
        pinned=bool(raw.pinned),
        archived=bool(raw.archived),
    )


class KeepClient:
    def __init__(self, config: Config):
        self.config = config
        self._keep = None

    @property
    def keep(self):
        if self._keep is None:
            self._authenticate()
        return self._keep

    def _authenticate(self) -> None:
        import gkeepapi

        secrets = self.config.secrets
        keep = gkeepapi.Keep()
        try:
            keep.authenticate(secrets.google_keep_email, secrets.google_keep_token)
        except gkeepapi.exception.LoginException as exc:
            raise AuthError(
                f"Google Keep sign-in failed: {exc}",
                service="keep",
                user_action=(
                    "The master token is wrong, expired, or revoked. Generate "
                    "a fresh one (see docs/USER_MANUAL.md) and update "
                    "GOOGLE_KEEP_MASTER_TOKEN in .env."
                ),
            ) from exc
        except Exception as exc:
            raise IntegrationError(
                f"could not reach Google Keep: {exc}",
                service="keep",
                recoverable=True,
                user_action="Check your internet connection.",
            ) from exc
        self._keep = keep
        log.info("Google Keep signed in as %s", secrets.google_keep_email)

    def _sync(self, what: str) -> None:
        import gkeepapi

        # self.keep triggers _authenticate() on first access, which already
        # raises well-formed AuthError/IntegrationError of its own — those
        # must pass through unchanged, not get caught by this method's own
        # generic handler below and re-wrapped into a vaguer duplicate.
        keep = self.keep
        try:
            keep.sync()
        except gkeepapi.exception.LoginException as exc:
            self._keep = None  # force a fresh sign-in on the next call
            raise AuthError(
                f"Google Keep session expired while {what}",
                service="keep",
                user_action=(
                    "Generate a fresh master token (see docs/USER_MANUAL.md) "
                    "and update GOOGLE_KEEP_MASTER_TOKEN in .env."
                ),
            ) from exc
        except gkeepapi.exception.APIException as exc:
            raise IntegrationError(
                f"Google Keep error while {what}: {exc}",
                service="keep",
                recoverable=True,
            ) from exc
        except Exception as exc:
            raise IntegrationError(
                f"could not reach Google Keep while {what}: {exc}",
                service="keep",
                recoverable=True,
                user_action="Check your internet connection.",
            ) from exc

    def ping(self) -> bool:
        self._sync("checking access")
        return True

    # ------------------------------------------------------------- reading
    def list_notes(self, include_archived: bool = False, limit: int = 50) -> list[Note]:
        self._sync("listing notes")
        found = self.keep.find(
            archived=None if include_archived else False, trashed=False
        )
        return [_to_note(n) for n in list(found)[:limit]]

    def find_notes(self, text: str, limit: int = 20) -> list[Note]:
        self._sync("searching notes")
        found = self.keep.find(query=text, trashed=False)
        return [_to_note(n) for n in list(found)[:limit]]

    # ------------------------------------------------------------- writing
    def create_note(self, title: str, text: str, pinned: bool = False) -> Note:
        note = self.keep.createNote(title, text)
        if pinned:
            note.pinned = True
        self._sync("creating a note")
        return _to_note(note)

    def _get(self, note_id: str):
        note = self.keep.get(note_id.strip())
        if note is None or note.trashed:
            raise IntegrationError(
                f"no Keep note with id {note_id!r}", service="keep"
            )
        return note

    def pin_note(self, note_id: str, pinned: bool = True) -> Note:
        note = self._get(note_id)
        note.pinned = pinned
        self._sync("updating a note")
        return _to_note(note)

    def archive_note(self, note_id: str, archived: bool = True) -> Note:
        note = self._get(note_id)
        note.archived = archived
        self._sync("updating a note")
        return _to_note(note)

    def delete_note(self, note_id: str) -> None:
        # trash(), not delete() — matches the real Keep app's "move to trash"
        # (recoverable there for a while), rather than an irreversible purge.
        note = self._get(note_id)
        note.trash()
        self._sync("deleting a note")

    def find_by_text(self, text: str) -> list[Note]:
        """Notes to disambiguate against when the caller has no id — same
        "list matches, ask which one" shape delete_calendar_event uses."""
        needle = text.lower().strip()
        return [
            n for n in self.find_notes(text)
            if needle in n.title.lower() or needle in n.text.lower()
        ]
