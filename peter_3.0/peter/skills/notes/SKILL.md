# notes

A personal journal — quick timestamped notes, searchable later
(`peter/notes.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `add_note` | write | Capture a one-off journal entry, timestamped. |
| `search_notes` | read | Search past notes by keyword. |
| `recent_notes` | read | List the most recently added notes. |
| `delete_note` | write | Delete a note permanently, by number. |

## Setup

`integrations.notes.enabled` (default true) is the only gate — no secret
needed; storage lives in the shared `peter.db`.

## Design notes & gotchas

- **A fourth kind of memory, deliberately kept separate from facts,
  preferences, and episodes — this is the whole reason this skill exists
  rather than living inside `memory/tools.py`.** A stored fact is durable
  and gets searched and injected into every relevant *future* turn
  automatically; that's wrong for "note that the client wants the demo
  moved to Friday" — a one-off, timestamped entry that should only surface
  when explicitly asked for. `add_note`'s docstring draws this line
  directly against `remember_fact`: "For something Peter should recall
  unprompted in every future conversation, use remember_fact instead."
  Don't merge these two tool families — the distinction is worth keeping
  sharp for the model choosing between them.
- **A new `notes` + `notes_fts` FTS5 pair, built on the same `Db` helper
  `expenses.py`/`deliveries.py` use** — so it lives in the shared
  `peter.db`, not a separate file (unlike `docs`, which does get its own
  database because it can grow to hundreds of megabytes; a personal journal
  won't).
- Uses the identical tokenise-and-OR-query approach `memory/store.py`'s
  `search_facts` already established for keeping free-form speech safe
  against FTS5's query syntax — no new parsing pattern invented here.
- `delete_note`'s docstring points at the bracketed `#id` shown by
  `search_notes`/`recent_notes` output — the same "show a short handle, take
  it back as an argument" shape `mail`'s uid brackets and `calendar`'s event
  ids use.

## Future extension ideas

- No tags or categories on a note — pure timestamp + free text + full-text
  search. A "show me all notes about the client" relies entirely on the
  word "client" appearing in the note text.
- No edit tool — correcting a note today means delete-and-re-add, losing
  the original timestamp.
