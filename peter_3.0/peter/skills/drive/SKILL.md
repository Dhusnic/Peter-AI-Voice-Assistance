# drive

Google Drive, full read/write, via the shared Google OAuth client
(`peter/integrations/google/drive.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `list_drive_files` | read | List files, optionally inside one folder or matching a name. |
| `search_drive_files` | read | Search Drive by file name. |
| `read_drive_file` | read | Read a file's text (Docs/Sheets/Slides exported to plain text). |
| `create_drive_file` | write | Create a new plain-text file from given content. |
| `create_drive_folder` | write | Create a new folder. |
| `move_drive_file` | write | Move a file into a different folder. |
| `rename_drive_file` | write | Rename a file. |
| `share_drive_file` | write | Share with someone by email (reader/commenter/writer). |
| `trash_drive_file` | write | Move to trash — recoverable, never a permanent delete. |

## Setup

- `integrations.google.enabled: true` and `secrets.has_google` — same gate as
  `calendar`/`contacts`/`sheets`/`gdocs`, all reusing one OAuth client
  (`peter/integrations/google/auth.py`).
- Needs the `drive` scope (the **full** scope, not `drive.readonly`) in
  `GoogleConfig.scopes` — required because this skill writes as well as
  reads. A token minted before this scope existed keeps working for
  Calendar/Tasks/Contacts but 403s on first Drive call; the existing
  403→`AuthError` handling in `_call()` (shared with `calendar.py`/
  `tasks.py`/`contacts.py`) catches this and names `--google-auth` as the
  fix — one re-auth run covers every Google skill at once, since scopes live
  on the shared token, not per API.

## Design notes & gotchas

- **`trash_drive_file` always trashes, never permanently deletes** —
  `files().update(body={"trashed": True})`. Same reversibility norm
  `delete_keep_note` uses in the `keep` skill: a voice command that misheard
  a file name should be recoverable, not gone.
- **Upload/download of local files is deliberately not exposed as a tool.**
  "Create a doc from this text" is a far more common voice command than
  "upload this local file," and the client keeps those methods internal
  rather than growing the tool count for a rarer case.
- `share_drive_file` validates `role` (`reader`/`commenter`/`writer`) before
  the API call — some client libraries would otherwise silently accept an
  invalid role.
- **Drive is a second source for the local document index, not a second
  store.** `index_drive_folder` (in the `docs` skill) treats a Drive file as
  `path = "gdrive://<file_id>"`, `folder = "Google Drive"` inside the exact
  same `documents` table local folders use — `search_docs`/`ask_docs` needed
  zero changes to support it. See the `docs` skill's SKILL.md.
- `read_drive_file` truncates at 4,000 chars with a visible "(truncated)"
  marker, never silently.

## Future extension ideas

- No permanent-delete tool exists at all (`trash_drive_file` is the only
  removal path) — consistent with the reversibility-first stance, but worth
  knowing if a user ever explicitly wants storage reclaimed.
- No tool copies a file (only move/rename) — Drive's API supports it; not
  built because nothing has needed it yet.
