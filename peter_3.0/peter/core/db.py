"""A small SQLite helper for the feature stores.

`peter/memory/store.py` is deliberately about *memory* — facts, preferences,
episodes. The features added later each need a little persistence of their own
(price watches, saved workspaces, the spend ledger, the document index) and
none of them belong inside the memory store just because it happened to own a
connection first.

So each feature owns its own table and its own small store class, all built on
this. What lives here is only the part that is genuinely shared and easy to get
subtly wrong:

    one connection per store, usable from any thread   scheduler jobs, the
                                                        Telegram thread and the
                                                        turn loop all write
    a lock around every statement                       sqlite3 objects are not
                                                        safe to share otherwise
    a busy timeout                                      APScheduler holds the
                                                        same file open through
                                                        SQLAlchemy; without
                                                        this a concurrent write
                                                        raises "database is
                                                        locked" instead of
                                                        waiting the moment out
    WAL journalling                                     readers never block on a
                                                        writer, which is what
                                                        a background poll
                                                        writing while you talk
                                                        actually looks like
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

# How long a statement waits for another connection to release its lock before
# giving up. Generous on purpose: every writer here holds the lock for well
# under a millisecond, so waiting is always the right answer over failing.
BUSY_TIMEOUT_SECONDS = 10.0


class Db:
    """One SQLite file, one connection, serialised access."""

    def __init__(self, path: Path, schema: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            timeout=BUSY_TIMEOUT_SECONDS,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            # WAL is a file-level property, so this is a no-op after the first
            # time. It fails harmlessly on a filesystem that cannot support it
            # (a network share), which is why it is not asserted.
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:  # pragma: no cover - filesystem-specific
                log.debug("WAL unavailable for %s", self.path, exc_info=True)
            self._conn.executescript(schema)
            self._conn.commit()

    # ------------------------------------------------------------- statements
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        """Run one statement and commit."""
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
        return cursor

    def execute_many(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = 0) -> Any:
        row = self.one(sql, params)
        if row is None or row[0] is None:
            return default
        return row[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
