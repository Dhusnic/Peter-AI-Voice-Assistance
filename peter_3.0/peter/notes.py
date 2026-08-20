"""A personal journal — quick voice notes, searchable later.

Distinct from peter/memory/store.py's facts and preferences: a note is a
timestamped, free-form entry ("note that the client wants the demo moved to
Friday"), never injected into a future turn automatically — only recalled
when asked. Distinct from peter/meeting_notes.py too: that transcribes a
recording; this is a single line captured in the moment.

Same FTS5 pattern as memory/store.py's facts table, built on the shared `Db`
helper the other feature stores (expenses, deliveries, workspaces) already
use, since this lives in the same peter.db file as those.
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from peter.core.db import Db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts
    USING fts5(text, content='notes', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""

# FTS5 treats these as query syntax; a raw sentence full of them raises
# sqlite3.OperationalError. Tokenise instead of trying to escape, same as
# memory/store.py.
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "is", "are", "was", "were", "to", "of", "in",
    "on", "for", "it", "my", "me", "i", "you", "your", "what", "whats", "how",
    "do", "does", "did", "can", "could", "please", "peter", "hey", "note",
    "notes", "that", "about", "down", "this",
}


def _fts_query(text: str) -> str:
    words = [w.lower() for w in _WORD_RE.findall(text)]
    words = [w for w in words if len(w) > 2 and w not in _STOPWORDS]
    unique = list(dict.fromkeys(words))[:12]
    if not unique:
        return ""
    return " OR ".join(f'"{w}"' for w in unique)


class NoteStore:
    def __init__(self, db_path: Path):
        self.db = Db(db_path, _SCHEMA)

    def close(self) -> None:
        self.db.close()

    def add(self, text: str) -> int:
        cur = self.db.execute(
            "INSERT INTO notes (text, created_at) VALUES (?, ?)",
            (text.strip(), time.time()),
        )
        return int(cur.lastrowid)

    def recent(self, limit: int = 10) -> list[tuple[int, str, float]]:
        # id DESC as a tiebreaker: time.time()'s resolution on Windows is
        # coarse enough that two notes added in quick succession can land on
        # the exact same created_at, and ORDER BY created_at alone would then
        # sort them arbitrarily. id is monotonic with insertion order, so it
        # always breaks the tie correctly.
        rows = self.db.query(
            "SELECT id, text, created_at FROM notes ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        )
        return [(r["id"], r["text"], r["created_at"]) for r in rows]

    def search(self, query: str, limit: int = 10) -> list[tuple[int, str, float]]:
        """Relevance-ranked note lookup. Falls back to recency on a query
        with no usable keywords, the same as memory/store.py's search_facts."""
        match = _fts_query(query)
        if not match:
            return self.recent(limit)
        try:
            rows = self.db.query(
                """SELECT n.id, n.text, n.created_at
                     FROM notes_fts JOIN notes n ON n.id = notes_fts.rowid
                    WHERE notes_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?""",
                (match, limit),
            )
        except sqlite3.OperationalError:
            return []
        return [(r["id"], r["text"], r["created_at"]) for r in rows]

    def delete(self, note_id: int) -> bool:
        return self.db.execute(
            "DELETE FROM notes WHERE id = ?", (note_id,)
        ).rowcount > 0


def spoken(rows: list[tuple[int, str, float]]) -> str:
    if not rows:
        return "No notes found."
    lines = []
    for note_id, text, created_at in rows:
        when = datetime.fromtimestamp(created_at).strftime("%d %b, %H:%M")
        lines.append(f"[{note_id}] {when} — {text}")
    return "\n".join(lines)
