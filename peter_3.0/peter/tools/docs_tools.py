"""Document search tools.

Three tools rather than one, for the same reason the browser has nine: they
cost wildly different amounts. `search_docs` is a SQLite query and free.
`ask_docs` spends a model call. `index_folder` walks a directory tree. Folding
them into one "documents" tool would mean paying the highest of those every
time.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.core.services import services


@peter_tool(tier="write")
def index_folder(path: str, force: bool = False) -> str:
    """Index a folder so its contents become searchable.

    Files already indexed and unchanged since last time are skipped, so
    re-running this on a large folder is cheap.

    Args:
        path: The folder to index, e.g. "D:/notes".
        force: True to re-read every file even if it looks unchanged.
    """
    try:
        result = services().docs().index_folder(path, force=force)
    except FileNotFoundError:
        return f"There is no folder at {path}."
    except Exception as exc:
        return f"Indexing failed: {type(exc).__name__}: {exc}"
    return result.spoken(path)


@peter_tool(tier="read")
def search_docs(query: str, limit: int = 5) -> str:
    """Search the indexed documents and return the matching passages.

    Use this when the user wants to find where something is written. When they
    want an actual answer built from the documents, use ask_docs instead.

    Args:
        query: What to look for.
        limit: How many passages to return.
    """
    hits = services().docs().search(query, limit=max(1, limit))
    if not hits:
        stats = services().docs().stats()
        if not stats["files"]:
            return "Nothing is indexed yet. Ask me to index a folder first."
        return f"No passage in the {stats['files']} indexed file(s) matches {query!r}."

    return "\n\n".join(f"{hit.name}:\n{hit.body[:600]}" for hit in hits)


@peter_tool(tier="read")
def ask_docs(question: str) -> str:
    """Answer a question using the indexed documents, citing the files used.

    Only uses what is actually in the documents — if they do not answer the
    question it says so rather than guessing.

    Args:
        question: What to find out from the documents.
    """
    from peter.docs_index import ask

    return ask(question)


@peter_tool(tier="read")
def docs_index_status() -> str:
    """Report what is indexed: how many files, and from which folders."""
    stats = services().docs().stats()
    if not stats["files"]:
        return "Nothing is indexed yet."
    folders = ", ".join(stats["folders"]) or "unknown"
    return (
        f"{stats['files']} file(s) indexed into {stats['chunks']} passage(s), "
        f"from: {folders}."
    )


@peter_tool(tier="write")
def forget_folder(path: str) -> str:
    """Remove a folder's contents from the document index.

    Args:
        path: The folder to drop.
    """
    removed = services().docs().forget(path)
    if not removed:
        return f"Nothing indexed under {path}."
    return f"Removed {removed} file(s) under {path} from the index."
