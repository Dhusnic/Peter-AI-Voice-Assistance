# gdocs

Google Docs, via the shared Google OAuth client
(`peter/integrations/google/gdocs.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `create_google_doc` | write | Create a new doc, optionally with initial text. |
| `read_google_doc` | read | Read a doc's text content. |
| `append_to_google_doc` | write | Append text to the end of an existing doc. |

## Setup

- `integrations.google.enabled: true` and `secrets.has_google` — same shared
  OAuth client and gate as `calendar`/`contacts`/`drive`/`sheets`.
- Needs the `documents` scope in `GoogleConfig.scopes`; same
  scope-added-after-token-issued caveat as `drive`/`sheets`.

## Design notes & gotchas

- **Named `gdocs`, not `docs`, throughout — module, client class, accessor,
  package — deliberately.** `services().docs()` already means something
  else entirely: the local RAG document index (`peter/docs_index.py`, the
  `docs` skill). Reusing `docs` here would have silently shadowed that
  accessor. If you ever add a tool or accessor touching Google Docs, keep
  the `gdocs` name; if you touch the local index, keep `docs`.
- **Reading goes through Drive's export endpoint, not the Docs API's own
  structural JSON.** `read_google_doc` calls the same
  `files().export(mimeType="text/plain")` path the `docs` skill's
  `index_drive_folder` already uses for Google Docs — far simpler than
  walking the Docs API's paragraph/run tree for something as basic as "give
  me the text."
- **Writing does go through the Docs API's own `documents().create()`/
  `batchUpdate()`**, since Drive's API has no path for inserting text into a
  doc's body — `create_google_doc` and `append_to_google_doc` are the two
  tools that actually touch the Docs API rather than Drive's.
- `read_google_doc` truncates at 4,000 chars with a visible marker.

## Future extension ideas

- No tool to insert text at a specific position or replace a range — only
  append exists. Structural editing (headings, tables) would need the Docs
  API's `batchUpdate` request objects, a meaningfully bigger surface than
  what's here now.
- No delete-doc tool — deleting a Google Doc today means going through
  `trash_drive_file` in the `drive` skill, since a Doc is still a Drive file
  underneath.
