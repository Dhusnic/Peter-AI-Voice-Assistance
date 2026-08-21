# calendar

Google Calendar events *and* Google Tasks, via the shared Google OAuth client
(`peter/integrations/google/`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `check_calendar` | read | Events on one day ("today", "tomorrow", or a date). |
| `upcoming_events` | read | Events over the next N days. |
| `next_event` | read | The single next thing on the calendar. |
| `create_calendar_event` | write | Add an event, with optional end time / location / description / all-day flag. |
| `delete_calendar_event` | write | Remove an event, by id or fuzzy text match. |
| `list_google_tasks` | read | List Google Tasks (syncs to the phone). |
| `add_google_task` | write | Add a Google Task. |
| `complete_google_task` | write | Mark a Google Task done, by id or fuzzy text match. |

**This skill covers two different "to-do" concepts — get this right.**
`list_google_tasks`/`add_google_task`/`complete_google_task` talk to Google
Tasks, which syncs to the user's phone; `list_google_tasks`'s own docstring
says to prefer it "when the user wants something they will see on their
phone." The **local**, SQLite-backed to-do list (`add_todo`/`list_todos`/
`complete_todo`) is a separate thing entirely, living in the `time` skill —
see that skill's SKILL.md. They are not aliases of each other and are not
kept in sync.

## Setup

- `integrations.google.enabled: true` and Google OAuth secrets present
  (`secrets.has_google` — `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`
  in `.env`). Same gate `contacts`/`drive`/`sheets`/`gdocs` share.
- Needs the `calendar` and `tasks` scopes from `GoogleConfig.scopes` — the two
  original scopes this client was built for, before Contacts/Drive/Sheets/Docs
  were added on top of the same OAuth client.
- `GoogleConfig.calendar_id` (default `"primary"`) and `tasklist_id` (default
  `"@default"`) pick which calendar/task list is used.
- Calendar/Tasks scopes are only *sensitive*, not *restricted* (unlike the
  Gmail API), so this OAuth client does not carry the 7-day refresh-token
  expiry that ruled out using the Gmail API for the `mail` skill — see
  `mail`'s SKILL.md for that trade.

## Design notes & gotchas

- **Times are ISO 8601 strings, not free text.** Claude gets the current
  local time on every turn and does the "next Tuesday" arithmetic itself —
  better than a date-parsing library, and a misheard date fails loudly
  (a bad ISO string) instead of silently booking the wrong week.
- `delete_calendar_event` and `complete_google_task` both use the "id or
  matching_text" shape: an exact id from a prior list call, or free text to
  search. More than one match changes nothing and lists the candidates
  instead of guessing — the same pattern `keep`'s three "by id or
  matching_text" tools and `find_google_contact` use.
- A scope added after a token already exists doesn't retroactively cover it
  — see `drive`'s SKILL.md for the shared 403→`AuthError`→`--google-auth`
  handling in `_call()`, common to every Google client here.
- `add_google_task`'s `due_iso` stores a **date only**; Google Tasks ignores
  any time component given.

## Future extension ideas

- No recurring-event support (create/delete both operate on single events).
- No tool moves an event's time — only create and delete exist; rescheduling
  today means delete-then-recreate.
