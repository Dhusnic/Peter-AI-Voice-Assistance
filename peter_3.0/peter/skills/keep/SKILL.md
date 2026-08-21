# keep

Google Keep notes, via the unofficial `gkeepapi` client (`peter/integrations/google/keep.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `list_keep_notes` | read | Most-recent-first listing, optionally including archived. |
| `search_keep_notes` | read | Title/text search. |
| `create_keep_note` | write | New note, optional title, optional pin. |
| `pin_keep_note` | write | Pin/unpin by id or by fuzzy text match. |
| `archive_keep_note` | write | Archive/unarchive by id or by fuzzy text match. |
| `delete_keep_note` | write | Moves to trash (`note.trash()`, not a purge) by id or by fuzzy text match. |

The three "by id or matching_text" tools all share one shape: give an exact
`note_id` from a prior `list_keep_notes`/`search_keep_notes` call, or free
text to search for. A `matching_text` that hits more than one note changes
nothing and lists the candidates instead of guessing — same
"list matches, ask which one" pattern `delete_calendar_event` and
`find_google_contact` use.

## Setup

1. `integrations.keep.enabled: true` in `config.yml` — **off by default**,
   the only integration in this codebase that is (see `KeepConfig` in
   `peter/core/config.py`).
2. `GOOGLE_KEEP_EMAIL` and `GOOGLE_KEEP_MASTER_TOKEN` in `.env`. The master
   token is obtained once, outside Peter, following `gkeepapi`'s own
   documented method (github.com/kiwiz/gkeepapi) — see docs/USER_MANUAL.md
   §7.10 before doing this.
3. `gkeepapi>=0.16` must be installed (already in `requirements.txt`).

Until all three are true, `services().keep()` raises `NotConfiguredError`
and the skill doesn't even register (`_REQUIRES` in `registry.py` gates the
whole module on `integrations.keep.enabled and secrets.has_keep`).

## Design notes & gotchas

- **This is not OAuth, and is not sold as OAuth.** There is no official Keep
  API for a personal @gmail.com account — the real Keep API is
  Workspace-only, gated behind an admin granting domain-wide delegation.
  `gkeepapi` authenticates with a master token: the same capability level as
  the account password, not a scoped, individually revocable grant like
  every other Google integration here (Calendar/Tasks/Contacts/Drive/Docs/
  Sheets) uses. If the token leaks, the blast radius is "read/write access
  to the whole Google account," not "read/write access to Keep." Say this
  plainly to the user before they generate a token, don't bury it.
- `keep.py` **deliberately does not import** `peter.integrations.google.auth`
  — importing it would visually suggest Keep shares that safer OAuth flow.
  Keep this separation; don't "simplify" it away later.
- `gkeepapi` has its own exception hierarchy (`LoginException`,
  `APIException`, `SyncException`), not `googleapiclient`'s `HttpError`, so
  `KeepClient` translates errors itself rather than reusing `calendar.py`'s
  `_call()`.
- **Known bug, already fixed once — don't reintroduce it.** `_sync()`'s
  generic `except Exception` handler used to also catch the `AuthError`
  raised by lazy `self.keep` property access (which calls `_authenticate()`
  on first use), re-wrapping a well-formed auth failure into a vaguer
  `IntegrationError`. Fixed by hoisting `keep = self.keep` outside the
  `try:` block. If this module gets refactored, keep that access outside
  any blanket exception handler.
- A `LoginException` mid-session (expired token) clears `self._keep` so the
  *next* call re-authenticates cleanly rather than reusing a dead session.
- Has not been exercised against a real Google account in this codebase yet
  — all 27 tests in `tests/test_keep.py` mock `gkeepapi` at the boundary.
  Treat first real use as a live smoke test, not a formality.

## Future extension ideas

- Label support (`gkeepapi` exposes labels; not surfaced here at all yet) —
  natural next tool once there's a real account to test filtering against.
- Reminders on notes (`gkeepapi` supports these too) — would need a new
  `Note` field and a decision about how to speak a due time aloud.
- If Google ever opens a scoped, personal-account-eligible Keep API, this
  whole module should be replaced, not extended — the master-token tradeoff
  above is the reason to prefer that path the moment it exists, not a
  permanent design choice.
