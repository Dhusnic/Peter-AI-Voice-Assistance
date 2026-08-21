"""Google Docs tools.

Named `gdocs_tools`, distinct from `docs_tools.py` — that module is the local
RAG document index (search_docs, ask_docs, ...); this one creates/reads/edits
real Google Docs. See `peter/integrations/google/gdocs.py` for why the naming
avoids `docs` throughout.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.services import services

register_skill(SkillManifest(
    name="gdocs", version="1.0.0",
    description="Google Docs: create, read, and append text to documents.",
    module=__name__, permissions=("network",),
    tools=("create_google_doc", "read_google_doc", "append_to_google_doc"),
))

_MAX_READ_CHARS = 4000


@peter_tool(tier="write")
def create_google_doc(title: str, text: str = "") -> str:
    """Create a new Google Doc, optionally with initial text.

    Args:
        title: The document's title.
        text: Optional text to write into the new document.
    """
    if not title.strip():
        return "Give the document a title."
    doc = services().gdocs().create_doc(title.strip(), text)
    return f"Created: {doc.spoken()} — id {doc.id}"


@peter_tool(tier="read")
def read_google_doc(document_id: str) -> str:
    """Read a Google Doc's text content.

    Args:
        document_id: The document's id, from create_google_doc or a Drive search.
    """
    text = services().gdocs().read_doc(document_id.strip())
    if len(text) > _MAX_READ_CHARS:
        return text[:_MAX_READ_CHARS] + "... (truncated)"
    return text


@peter_tool(tier="write")
def append_to_google_doc(document_id: str, text: str) -> str:
    """Append text to the end of an existing Google Doc.

    Args:
        document_id: The document's id.
        text: The text to append.
    """
    if not text.strip():
        return "Give some text to append."
    services().gdocs().append_text(document_id.strip(), text)
    return "Appended."
