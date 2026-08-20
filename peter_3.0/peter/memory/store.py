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

import logging
import re
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

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

-- Semantic search, kept in side tables rather than as columns on `facts` and
-- `preferences`. The schema is applied with CREATE TABLE IF NOT EXISTS, which
-- does not add a column to a table that already exists, so a new column would
-- silently not appear on anyone's existing peter.db. A new table does.
CREATE TABLE IF NOT EXISTS fact_vectors (
    key        TEXT PRIMARY KEY,
    vec        BLOB NOT NULL,
    model      TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- Which preferences are worth retrieving rather than always injecting.
-- Absent means 'always', so every preference stored before this existed keeps
-- exactly its previous behaviour.
CREATE TABLE IF NOT EXISTS preference_scope (
    key   TEXT PRIMARY KEY,
    scope TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preference_vectors (
    key        TEXT PRIMARY KEY,
    vec        BLOB NOT NULL,
    model      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

# A preference that shapes every reply ("keep it short", "be direct") must be
# injected on every turn; one that only matters sometimes ("prefer Amazon for
# price checks") is worth retrieving. Retrieving the first kind would quietly
# drop it on any turn whose wording did not happen to match.
SCOPE_ALWAYS = "always"
SCOPE_CONTEXTUAL = "contextual"

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
    def __init__(self, db_path: Path, embedder=None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # Optional throughout. None -- or an embedder whose model file is not
        # there -- means every search below quietly stays on the FTS5 path
        # this store has always used.
        self.embedder = embedder

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
        # Embedded on the way in, so retrieval never pays for it. Keyed on
        # "key: value" because the key carries real meaning ("commute",
        # "allergy") that the value alone often leaves implicit.
        self._store_vector("fact_vectors", key.strip(), f"{key.strip()}: {value.strip()}")

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

    # ------------------------------------------------------------- vectors
    def _embedding_config(self):
        """Retrieval tuning, falling back to the defaults if no config has
        been loaded — this store is constructed directly in plenty of tests."""
        from peter.core.config import EmbeddingsConfig

        try:
            from peter.core.config import get_config

            return get_config().memory.embeddings
        except Exception:  # pragma: no cover - config-less use
            return EmbeddingsConfig()

    def _embedding_ready(self) -> bool:
        return self.embedder is not None and self.embedder.available()

    def _store_vector(self, table: str, key: str, text: str) -> None:
        """Best-effort. A failure to embed must leave the fact itself stored
        and searchable by keyword, never roll it back."""
        if not self._embedding_ready():
            return
        try:
            from peter.memory import embeddings

            vector = self.embedder.encode_one(text)
            if vector is None:
                return
            with self._lock:
                self._conn.execute(
                    f"""INSERT INTO {table} (key, vec, model, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            vec=excluded.vec, model=excluded.model,
                            updated_at=excluded.updated_at""",
                    (key, embeddings.to_blob(vector), embeddings.MODEL_NAME, time.time()),
                )
                self._conn.commit()
        except Exception:
            log.debug("could not embed %r", key, exc_info=True)

    def _search_vectors(
        self, table: str, source: str, query: str, limit: int, threshold: float
    ) -> list[tuple[str, str, float]]:
        """Brute-force cosine over every stored vector.

        Vectors are L2-normalised at encode time, so cosine similarity is a
        single dot product. At a personal assistant's scale this is
        microseconds — a vector index would be a dependency and a failure
        mode bought for no measurable speedup.
        """
        if not self._embedding_ready():
            return []
        try:
            from peter.memory import embeddings

            probe = self.embedder.encode_one(query)
            if probe is None:
                return []
            with self._lock:
                rows = self._conn.execute(
                    f"""SELECT v.key AS key, s.value AS value, v.vec AS vec
                          FROM {table} v JOIN {source} s ON s.key = v.key
                         WHERE v.model = ?""",
                    (embeddings.MODEL_NAME,),
                ).fetchall()
            if not rows:
                return []

            matrix = np.vstack([embeddings.from_blob(r["vec"]) for r in rows])
            scores = matrix @ probe
            ranked = np.argsort(-scores)[:limit]
            # The threshold is what stops this padding the prompt: below it,
            # nothing is returned at all, rather than the least-bad match.
            return [
                (rows[i]["key"], rows[i]["value"], float(scores[i]))
                for i in ranked
                if scores[i] >= threshold
            ]
        except Exception:
            log.debug("semantic search failed, using keyword results only", exc_info=True)
            return []

    def search_facts_hybrid(
        self, query: str, limit: int = 5, threshold: float = 0.35
    ) -> list[tuple[str, str]]:
        """Semantic and keyword results, unioned.

        Both, not one: embeddings find "how do I get to work" -> "route 70
        bus", which keywords cannot; keywords find an exact registration
        number or account id, which embeddings are unreliable at because the
        model never learned that particular string. Each covers the other's
        blind spot, so the union beats either alone.

        Semantic hits lead, since when they fire they are usually the better
        match; keyword hits fill whatever is left of the budget.
        """
        semantic = self._search_vectors("fact_vectors", "facts", query, limit, threshold)
        out: list[tuple[str, str]] = [(k, v) for k, v, _ in semantic]
        seen = {k for k, _ in out}

        for key, value in self.search_facts(query, limit):
            if len(out) >= limit:
                break
            if key not in seen:
                out.append((key, value))
                seen.add(key)
        return out

    def reindex_embeddings(self) -> int:
        """Embed every fact and preference that has no current vector.

        Needed because facts stored before this feature existed have none, and
        because switching model would leave the old ones unusable.
        """
        if not self._embedding_ready():
            return 0
        done = 0
        for key, value in self.all_facts():
            self._store_vector("fact_vectors", key, f"{key}: {value}")
            done += 1
        for key, value in self.all_preferences():
            self._store_vector("preference_vectors", key, f"{key}: {value}")
            done += 1
        log.info("re-indexed %d memories for semantic search", done)
        return done

    # ---------------------------------------------------------- preferences
    def set_preference(self, key: str, value: str, scope: str = SCOPE_ALWAYS) -> None:
        """Store a standing instruction.

        `scope` decides whether it is injected on every turn (`always`, the
        default and the old behaviour) or retrieved only when the turn is
        about it (`contextual`). Defaulting to `always` is the safe direction:
        a preference wrongly marked contextual silently stops applying, while
        one wrongly marked always merely costs a few tokens.
        """
        now = time.time()
        scope = scope if scope in (SCOPE_ALWAYS, SCOPE_CONTEXTUAL) else SCOPE_ALWAYS
        with self._lock:
            self._conn.execute(
                """INSERT INTO preferences (key, value, created_at, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value=excluded.value, updated_at=excluded.updated_at""",
                (key.strip(), value.strip(), now, now),
            )
            self._conn.execute(
                """INSERT INTO preference_scope (key, scope) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET scope=excluded.scope""",
                (key.strip(), scope),
            )
            self._conn.commit()
        if scope == SCOPE_CONTEXTUAL:
            self._store_vector(
                "preference_vectors", key.strip(), f"{key.strip()}: {value.strip()}"
            )

    def all_preferences(self) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM preferences ORDER BY key"
            ).fetchall()
        return [(r["key"], r["value"]) for r in rows]

    def preference_scopes(self) -> dict[str, str]:
        """Scope per preference key. A key with no row is `always` — which is
        every preference stored before scopes existed."""
        with self._lock:
            rows = self._conn.execute("SELECT key, scope FROM preference_scope").fetchall()
        return {r["key"]: r["scope"] for r in rows}

    def preferences_for(
        self, query: str, limit: int = 3, threshold: float = 0.35
    ) -> list[tuple[str, str]]:
        """The preferences that should apply to this turn: every `always` one,
        plus the `contextual` ones the turn is actually about."""
        scopes = self.preference_scopes()
        everything = self.all_preferences()
        always = [
            (k, v) for k, v in everything
            if scopes.get(k, SCOPE_ALWAYS) != SCOPE_CONTEXTUAL
        ]
        if not any(s == SCOPE_CONTEXTUAL for s in scopes.values()):
            return always

        hits = self._search_vectors(
            "preference_vectors", "preferences", query, limit, threshold
        )
        chosen = {k for k, _, _ in hits}
        return always + [(k, v) for k, v in everything if k in chosen]

    def delete_preference(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM preferences WHERE key = ?", (key.strip(),)
            )
            self._conn.execute("DELETE FROM preference_scope WHERE key = ?", (key.strip(),))
            self._conn.execute("DELETE FROM preference_vectors WHERE key = ?", (key.strip(),))
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

    def episodes_since(self, since: float, limit: int = 100) -> list[str]:
        """Episodes recorded after `since` (epoch seconds), oldest first.

        The work log reads these to recover what happened during the day —
        focus sessions and meeting notes both leave one behind.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT summary FROM episodes WHERE created_at >= ? "
                "ORDER BY created_at LIMIT ?",
                (since, limit),
            ).fetchall()
        return [r["summary"] for r in rows]

    def related_note(self, topic: str) -> str | None:
        """The single most relevant fact or recent episode for `topic`, if any.

        For a proactive nudge (meeting prep, mainly) that wants one short
        "you mentioned this before" line rather than the full memory block a
        conversational turn gets injected in its user message.
        """
        facts = self.search_facts(topic, limit=1)
        if facts:
            return facts[0][1]

        words = {
            w.lower() for w in _WORD_RE.findall(topic)
            if len(w) > 2 and w.lower() not in _STOPWORDS
        }
        if not words:
            return None
        for summary in self.recent_episodes(limit=15):
            summary_words = {w.lower() for w in _WORD_RE.findall(summary)}
            if words & summary_words:
                return summary
        return None

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

    def completed_todos_since(self, since: float) -> list[str]:
        """To-dos ticked off after `since` (epoch seconds).

        Used by the work log to answer "what did I actually finish today"
        without it having to read and filter the whole list itself.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT text FROM todos WHERE done = 1 AND completed_at >= ? "
                "ORDER BY completed_at",
                (since,),
            ).fetchall()
        return [r["text"] for r in rows]

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
        cached prefix, and changing it every turn would void the cache. That
        also means this block is the one part of a request billed at full
        price on every turn, which is why it is worth retrieving rather than
        injecting wholesale — it is small in tokens but expensive per token.
        """
        cfg = self._embedding_config()
        prefs = self.preferences_for(
            user_text, cfg.top_k_contextual_preferences, cfg.similarity_threshold
        )
        facts = self.search_facts_hybrid(
            user_text, cfg.top_k_facts, cfg.similarity_threshold
        )
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
