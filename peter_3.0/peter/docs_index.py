"""Full-text search over folders you point Peter at, and answers built from it.

The memory store already runs FTS5 over facts. This is the same idea aimed at
files: notes, specs, configs, source. It exists because the alternatives are
both bad — `search_files` greps filenames and cannot find a phrase inside a
document, and pasting a folder into a prompt is neither affordable nor
possible past a few files.

**Kept in its own database.** This is the one store that can reach hundreds of
megabytes and the one you might reasonably want to delete and rebuild.
`data/docs.db` can be removed at any time without touching memory.

**Indexing is incremental.** A file whose size and modification time are
unchanged since last time is skipped without being read, so re-indexing a
large folder after editing two files costs two files' work.

**Searching is not asking.** `search` returns matching passages, which is
often all you want and costs nothing. `ask` spends a model call to turn those
passages into an answer with citations. Keeping them separate means the cheap
one stays cheap.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from peter.core.db import Db

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    path       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    folder     TEXT NOT NULL,
    mtime      REAL NOT NULL,
    size       INTEGER NOT NULL,
    chunks     INTEGER NOT NULL DEFAULT 0,
    indexed_at REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks
    USING fts5(path UNINDEXED, name, body, tokenize='porter unicode61');
"""

_WORD = re.compile(r"[A-Za-z0-9_.\-]+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "is", "are", "was", "were", "to", "of",
    "in", "on", "for", "it", "my", "me", "i", "you", "your", "what", "whats",
    "how", "do", "does", "did", "can", "could", "please", "peter", "hey",
    "about", "with", "from", "that", "this", "there", "where", "when", "why",
}

_ANSWER_SYSTEM = (
    "You answer a question using only the document passages provided. Cite the "
    "file each fact came from, in brackets, e.g. [notes.md]. If the passages "
    "do not contain the answer, say so plainly and name what they do cover — "
    "never fill the gap from general knowledge. Be brief; this may be read "
    "aloud."
)


@dataclass(slots=True)
class Hit:
    path: str
    name: str
    body: str

    def as_block(self, max_chars: int = 900) -> str:
        return f"[{self.name}] {self.body[:max_chars]}"


@dataclass(slots=True)
class IndexResult:
    files: int = 0
    chunks: int = 0
    skipped: int = 0
    unchanged: int = 0

    def spoken(self, folder: str) -> str:
        if not self.files and self.unchanged:
            return f"{folder} is already indexed — {self.unchanged} file(s), nothing changed."
        if not self.files:
            return f"Nothing indexable found in {folder}."
        extra = f", {self.unchanged} unchanged" if self.unchanged else ""
        skipped = f", {self.skipped} skipped" if self.skipped else ""
        return (
            f"Indexed {self.files} file(s) from {folder} into {self.chunks} "
            f"passage(s){extra}{skipped}."
        )


class DocIndex:
    def __init__(self, db_path: Path, cfg):
        self.db = Db(db_path, _SCHEMA)
        self.cfg = cfg

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------- indexing
    def index_folder(self, folder: str | Path, force: bool = False) -> IndexResult:
        """Walk a folder and index every file matching the configured types."""
        root = Path(folder).expanduser()
        if not root.exists():
            raise FileNotFoundError(str(root))
        if root.is_file():
            return self._index_one(root, root.parent, IndexResult(), force)

        result = IndexResult()
        extensions = {e.lower() for e in self.cfg.extensions}
        skip_dirs = {d.lower() for d in self.cfg.skip_directories}
        seen = 0

        for path in root.rglob("*"):
            if seen >= self.cfg.max_files:
                log.info("doc index: stopped at max_files (%d)", self.cfg.max_files)
                break
            if not path.is_file():
                continue
            if any(part.lower() in skip_dirs for part in path.parts):
                continue
            if path.suffix.lower() not in extensions:
                continue
            seen += 1
            self._index_one(path, root, result, force)

        return result

    def _index_one(
        self, path: Path, root: Path, result: IndexResult, force: bool
    ) -> IndexResult:
        try:
            stat = path.stat()
        except OSError:
            result.skipped += 1
            return result

        if stat.st_size > self.cfg.max_file_kb * 1024:
            result.skipped += 1
            return result

        if not force:
            row = self.db.one(
                "SELECT mtime, size FROM documents WHERE path = ?", (str(path),)
            )
            if row and row["mtime"] == stat.st_mtime and row["size"] == stat.st_size:
                result.unchanged += 1
                return result

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            result.skipped += 1
            return result

        chunks = list(self._chunk(text))
        if not chunks:
            result.skipped += 1
            return result

        # Replace wholesale rather than diffing: chunk boundaries move when a
        # file is edited, so there is no stable identity to update against.
        self.db.execute("DELETE FROM doc_chunks WHERE path = ?", (str(path),))
        self.db.execute_many(
            "INSERT INTO doc_chunks (path, name, body) VALUES (?, ?, ?)",
            [(str(path), path.name, chunk) for chunk in chunks],
        )
        self.db.execute(
            """INSERT INTO documents (path, name, folder, mtime, size, chunks, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                   mtime = excluded.mtime, size = excluded.size,
                   chunks = excluded.chunks, indexed_at = excluded.indexed_at""",
            (str(path), path.name, str(root), stat.st_mtime, stat.st_size,
             len(chunks), time.time()),
        )

        result.files += 1
        result.chunks += len(chunks)
        return result

    def _chunk(self, text: str):
        """Split on blank lines, packing up to chunk_chars per passage.

        Paragraph boundaries beat a fixed character window: a passage that
        stops mid-sentence retrieves badly and reads worse when quoted back.
        """
        size = self.cfg.chunk_chars
        current: list[str] = []
        length = 0
        for block in text.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            if length + len(block) > size and current:
                yield "\n\n".join(current)
                current, length = [], 0
            # A single block bigger than the window is cut on its own.
            while len(block) > size:
                yield block[:size]
                block = block[size:]
            current.append(block)
            length += len(block)
        if current:
            yield "\n\n".join(current)

    def forget(self, folder: str | Path) -> int:
        """Drop everything indexed under a folder."""
        prefix = f"{Path(folder).expanduser()}%"
        rows = self.db.query(
            "SELECT path FROM documents WHERE path LIKE ?", (prefix,)
        )
        for row in rows:
            self.db.execute("DELETE FROM doc_chunks WHERE path = ?", (row["path"],))
        self.db.execute("DELETE FROM documents WHERE path LIKE ?", (prefix,))
        return len(rows)

    # ------------------------------------------------------------ searching
    def search(self, query: str, limit: int = 6) -> list[Hit]:
        """Ranked passages. Tries every term, then any term."""
        terms = [
            w.lower() for w in _WORD.findall(query)
            if len(w) > 2 and w.lower() not in _STOPWORDS
        ]
        terms = list(dict.fromkeys(terms))[:10]
        if not terms:
            return []

        quoted = [f'"{t}"' for t in terms]
        for expression in (" AND ".join(quoted), " OR ".join(quoted)):
            hits = self._match(expression, limit)
            if hits:
                return hits
        return []

    def _match(self, expression: str, limit: int) -> list[Hit]:
        import sqlite3

        try:
            rows = self.db.query(
                """SELECT path, name, body FROM doc_chunks
                    WHERE doc_chunks MATCH ? ORDER BY rank LIMIT ?""",
                (expression, limit),
            )
        except sqlite3.OperationalError:
            # A malformed FTS expression is a bad query, not a broken index.
            log.debug("doc index: bad FTS expression %r", expression, exc_info=True)
            return []
        return [Hit(r["path"], r["name"], r["body"]) for r in rows]

    # ---------------------------------------------------------------- stats
    def stats(self) -> dict:
        return {
            "files": int(self.db.scalar("SELECT COUNT(*) FROM documents")),
            "chunks": int(self.db.scalar("SELECT COUNT(*) FROM doc_chunks")),
            "folders": [
                r["folder"] for r in self.db.query(
                    "SELECT DISTINCT folder FROM documents ORDER BY folder"
                )
            ],
        }


# ------------------------------------------------------------------ answering
def ask(question: str, limit: int = 6) -> str:
    """Answer a question from the indexed documents, with citations."""
    from peter.core.services import services
    from peter.llm import factory

    container = services()
    hits = container.docs().search(question, limit=limit)
    if not hits:
        stats = container.docs().stats()
        if not stats["files"]:
            return (
                "Nothing is indexed yet. Point me at a folder first — "
                "\"index my notes folder\"."
            )
        return f"Nothing in the {stats['files']} indexed file(s) matches that."

    passages = "\n\n---\n\n".join(hit.as_block() for hit in hits)
    try:
        provider = factory.build_provider(container.config, _ANSWER_SYSTEM)
        try:
            provider.add_user(f"Question: {question}\n\nPassages:\n\n{passages}")
            response = provider.complete([])
        finally:
            provider.close()
    except Exception:
        log.exception("doc index: could not answer, returning the passages")
        return "I could not phrase an answer, but here is what matched:\n\n" + passages

    return (response.text or "").strip() or passages


def index_configured_folders(config) -> None:
    """Index the folders listed in config.yml. Called at startup, in the
    background — a first index of a large tree takes a while and must not
    delay Peter answering."""
    from peter.core.services import services

    cfg = config.integrations.docs
    if not (cfg.enabled and cfg.folders):
        return

    index = services().docs()
    for folder in cfg.folders:
        try:
            result = index.index_folder(folder)
            log.info("doc index: %s", result.spoken(folder))
        except FileNotFoundError:
            log.warning("doc index: %s does not exist", folder)
        except Exception:
            log.exception("doc index: failed indexing %s", folder)
