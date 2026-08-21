# contacts

Google Contacts, read-only, via the People API (`peter/integrations/google/contacts.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `find_google_contact` | read | Resolve a name (or part of one) to a saved contact's email/phone. |

One tool, on purpose. This skill only *resolves* — it never sends anything
itself.

## Setup

- `integrations.google.enabled: true` and Google OAuth secrets present
  (`secrets.has_google`) — same gate as Calendar/Tasks/Drive/Docs/Sheets.
- Needs the `contacts.readonly` scope, added to `GoogleConfig.scopes`'s
  default list alongside the pre-existing Calendar/Tasks scopes. A token
  minted before this scope was added will 403 on first use — the existing
  403→`AuthError` translation in `_call()` (shared with calendar.py) catches
  this and tells the user to re-run `--google-auth`; no special-casing
  needed here.
- Uses the same OAuth client and `_call()` retry/error pattern as
  `calendar.py`/`tasks.py` — nothing contacts-specific in the transport
  layer.

## Design notes & gotchas

- **Deliberately not wired into `send_email`'s `to` param.** `send_email`
  still requires a real address; `find_google_contact` only resolves a name
  to one. This mirrors the existing `call_contact` (phone) / `make_phone_call`
  split in `peter/skills/phone/tools.py`: a write-tier action should never
  trust a name it was merely told — it should be handed the exact value a
  dedicated read-tier lookup already resolved. Do not shortcut this by
  teaching `send_email` to accept a bare name.
- On more than one match, the tool lists every candidate and asks which one
  was meant rather than guessing the first result — same
  "list matches, ask which one" shape used across delivery/calendar/notes
  tools whenever a fuzzy lookup could be ambiguous.

## Future extension ideas

- A `create_google_contact` / `update_google_contact` write pair would be
  the natural next step if a use case for it shows up — deliberately not
  built yet since nothing in Peter currently needs to *write* contacts, only
  resolve them before another tool acts.
- Could extend matching to also search phonetic/nickname variants if voice
  transcription of names turns out to be the main source of "no match"
  misses in practice — worth checking real usage before building this,
  not guessing ahead of it.
