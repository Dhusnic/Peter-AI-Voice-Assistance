"""Persistent memory: SQLite + FTS5.

Three kinds of memory, deliberately separated because they are used differently:

    facts        durable statements about the user's world
                 ("college: Anna University", "usual groceries: milk, eggs")
    preferences  how Peter should behave ("keep replies under two sentences")
    episodes     rolling summaries of past conversations

Preferences are always injected. Facts are searched and only the relevant ones
are injected, because injecting all of them would grow without bound. Injection
goes into the *user* message, never the system prompt — the system prompt is the
cached prefix and must stay byte-identical between turns.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT UNIQUE NOT NULL,
    value      TEXT NOT NULL,
    source     TEXT DEFAULT 'user',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS preferences (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT UNIQUE NOT NULL,
    value      TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS episodes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    summary    TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS todos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    text         TEXT NOT NULL,
    done         INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    completed_at REAL
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
    USING fts5(key, value, content='facts', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, key, value) VALUES (new.id, new.key, new.value);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, key, value)
        VALUES ('delete', old.id, old.key, old.value);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, key, value)
        VALUES ('delete', old.id, old.key, old.value);
    INSERT INTO facts_fts(rowid, key, value) VALUES (new.id, new.key, new.value);
END;
"""

# FTS5 treats these as query syntax; a raw transcript full of them raises
# sqlite3.OperationalError. Tokenise instead of trying to escape.
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "is", "are", "was", "were", "to", "of", "in",
    "on", "for", "it", "my", "me", "i", "you", "your", "what", "whats", "how",
    "do", "does", "did", "can", "could", "please", "peter", "hey",
}


def _fts_query(text: str) -> str:
    """Turn free-form speech into a safe FTS5 OR-query."""
    words = [w.lower() for w in _WORD_RE.findall(text)]
    words = [w for w in words if len(w) > 2 and w not in _STOPWORDS]
    unique = list(dict.fromkeys(words))[:12]
    if not unique:
        return ""
    return " OR ".join(f'"{w}"' for w in unique)


class MemoryStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- facts
    def set_fact(self, key: str, value: str, source: str = "user") -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO facts (key, value, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value=excluded.value,
                       source=excluded.source,
                       updated_at=excluded.updated_at""",
                (key.strip(), value.strip(), source, now, now),
            )
            self._conn.commit()

    def get_fact(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM facts WHERE key = ?", (key.strip(),)
            ).fetchone()
        return row["value"] if row else None

    def delete_fact(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM facts WHERE key = ?", (key.strip(),))
            self._conn.commit()
        return cur.rowcount > 0

    def all_facts(self) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM facts ORDER BY key"
            ).fetchall()
        return [(r["key"], r["value"]) for r in rows]

    def search_facts(self, query: str, limit: int = 8) -> list[tuple[str, str]]:
        """Relevance-ranked fact lookup. Falls back to recency on an empty query."""
        match = _fts_query(query)
        if not match:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT key, value FROM facts ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [(r["key"], r["value"]) for r in rows]

        with self._lock:
            try:
                rows = self._conn.execute(
                    """SELECT f.key, f.value
                         FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid
                        WHERE facts_fts MATCH ?
                        ORDER BY rank
                        LIMIT ?""",
                    (match, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [(r["key"], r["value"]) for r in rows]

    # ---------------------------------------------------------- preferences
    def set_preference(self, key: str, value: str) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO preferences (key, value, created_at, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value=excluded.value, updated_at=excluded.updated_at""",
                (key.strip(), value.strip(), now, now),
            )
            self._conn.commit()

    def all_preferences(self) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM preferences ORDER BY key"
            ).fetchall()
        return [(r["key"], r["value"]) for r in rows]

    def delete_preference(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM preferences WHERE key = ?", (key.strip(),)
            )
            self._conn.commit()
        return cur.rowcount > 0

    # -------------------------------------------------------------- episodes
    def add_episode(self, summary: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO episodes (summary, created_at) VALUES (?, ?)",
                (summary.strip(), time.time()),
            )
            self._conn.commit()

    def recent_episodes(self, limit: int = 3) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT summary FROM episodes ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [r["summary"] for r in rows]

    # ----------------------------------------------------------------- todos
    def add_todo(self, text: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO todos (text, created_at) VALUES (?, ?)",
                (text.strip(), time.time()),
            )
            self._conn.commit()
        return int(cur.lastrowid)

    def list_todos(self, include_done: bool = False) -> list[tuple[int, str, bool]]:
        sql = "SELECT id, text, done FROM todos"
        if not include_done:
            sql += " WHERE done = 0"
        sql += " ORDER BY done, created_at"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [(r["id"], r["text"], bool(r["done"])) for r in rows]

    def complete_todo(self, todo_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE todos SET done = 1, completed_at = ? WHERE id = ? AND done = 0",
                (time.time(), todo_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def find_todos(self, needle: str) -> list[tuple[int, str]]:
        pattern = f"%{needle.strip().lower()}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, text FROM todos WHERE done = 0 AND lower(text) LIKE ?",
                (pattern,),
            ).fetchall()
        return [(r["id"], r["text"]) for r in rows]

    # ------------------------------------------------------------- injection
    def context_block(self, user_text: str) -> str:
        """The memory snippet prepended to a turn's user message.

        Deliberately not part of the system prompt: the system prompt is the
        cached prefix, and changing it every turn would void the cache.
        """
        prefs = self.all_preferences()
        facts = self.search_facts(user_text)
        episodes = self.recent_episodes(limit=2)

        if not (prefs or facts or episodes):
            return ""

        lines = ["<memory>"]
        if prefs:
            lines.append("Standing preferences:")
            lines += [f"- {k}: {v}" for k, v in prefs]
        if facts:
            lines.append("Possibly relevant facts:")
            lines += [f"- {k}: {v}" for k, v in facts]
        if episodes:
            lines.append("Recent context:")
            lines += [f"- {e}" for e in episodes]
        lines.append("</memory>")
        return "\n".join(lines)
