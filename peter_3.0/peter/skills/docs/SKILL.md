# docs

Full-text search over folders (plus Google Drive), with cited answers
(`peter/docs_index.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `index_folder` | write | Index a local folder — skip-if-unchanged, so re-running is cheap. |
| `index_drive_folder` | write | Index one Drive folder (non-recursive) into the same index. |
| `search_docs` | read | Search indexed passages — a SQLite query, free. |
| `ask_docs` | read | Answer a question from the indexed documents, citing files — spends a model call. |
| `docs_index_status` | read | How many files/passages are indexed, from which folders. |
| `forget_folder` | write | Remove a folder's contents from the index. |

**Named `docs`, not `gdocs` — this is the local RAG index, a different thing
from the `gdocs` skill**, which creates/reads/edits real Google Docs. See
`gdocs`'s SKILL.md for why that naming split exists (`services().docs()`
already means this local index; reusing the name for Google Docs would have
silently shadowed it).

## Setup

`integrations.docs.enabled` (default true) is the only registry gate — the
module registers even with `folders: []` (nothing indexed yet), same as
`dev` and `routines` needing something configured to be *useful*, not to be
*registered*. `DocsConfig`: `folders` (empty by default — index-on-startup
list), `extensions` (source-code and text extensions), `max_file_kb`
(default 512 — larger files are almost always generated/minified),
`chunk_chars`, `max_files`, `drive_folder_id` (empty = Drive indexing off),
`skip_directories` (`.git`, `node_modules`, `__pycache__`, etc.).

## Design notes & gotchas

- **Three tools that cost wildly different amounts, kept separate on
  purpose.** `search_docs` is a SQLite query and free. `ask_docs` spends a
  model call. `index_folder`/`index_drive_folder` walk a directory tree.
  Folding these into one "documents" tool would mean paying the highest of
  those costs every time, even for a free lookup.
- **Its own database (`docs.db`), separate from `peter.db`** — it's the one
  store that can reach hundreds of megabytes and the one a user might
  reasonably want to delete and rebuild; keeping it separate means doing so
  cannot take memory/preferences/episodes with it.
- **Indexing is incremental on (size, mtime)** — re-indexing a large tree
  after editing two files costs two files' worth of work, not a full
  re-read.
- **Chunking splits on paragraph boundaries, not a fixed character window**
  — a passage that stops mid-sentence retrieves badly and reads worse when
  quoted back in `ask_docs`'s citation.
- **Search tries every term, then any term.** Requiring all query terms to
  match returns nothing far too often for a spoken question — the fallback
  to an OR match is deliberate, not a bug in the AND path.
- **Drive is a second *source* feeding the same store, not a second
  index.** `index_drive_folder()` stores a Drive file as
  `path = "gdrive://<file_id>"`, `folder = "Google Drive"` — the existing
  `documents.path`/`documents.folder` schema (already just text fields) took
  this with zero schema change. `search_docs`/`ask_docs`/`docs_index_status`
  needed no changes either; they already query every row regardless of
  source. It exports Google Docs/Sheets/Slides to text (no native binary
  content), downloads everything else already in the `extensions` allowlist,
  and reuses the same `_chunk()`/insert path local files go through. See
  `drive`'s SKILL.md for the OAuth/gate side of this.
- `index_drive_folder` lists one folder **non-recursively** — an explicit
  target, not an open-ended Drive crawl.
- `ask_docs` is instructed to only use what's actually in the documents and
  say so plainly when they don't answer the question, rather than guessing
  from the model's own general knowledge.

## Future extension ideas

- No incremental re-index-on-a-schedule — indexing only happens when
  `index_folder`/`index_drive_folder` is explicitly called (or at startup
  for configured `folders`), never on a background timer watching for file
  changes.
- `index_drive_folder`'s non-recursive scope means a deeply nested Drive
  folder structure needs one call per level — a recursive option would be a
  natural, bounded extension if that friction shows up in practice.
