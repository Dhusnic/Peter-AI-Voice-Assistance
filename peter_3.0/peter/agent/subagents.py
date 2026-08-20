"""Reading several pages at once without flooding the conversation.

Phase 7 of the plan, and the case that genuinely earns a subagent. Comparing
one product across five sites means five page reads at ~1,500 tokens each. Fed
straight into the main conversation that is 7,500 tokens of raw page text
sitting in history for the rest of the session, re-sent on every subsequent
turn, for an answer that is two sentences long.

So each page gets its own small, isolated, tool-free model call that answers
the question about that page alone, and only those short answers come back.
The main conversation sees a comparison, not five web pages.

**The fan-out is in the model calls, not the fetches.** There is one browser
with one page, and requests to a domain are rate-limited on purpose — so pages
are read one after another. What runs in parallel is the reading-and-extracting
of what has already been fetched, which is where the wall-clock time actually
goes when a model is involved.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

_EXTRACT_SYSTEM = (
    "You read one web page and answer one question about it, using only what "
    "the page says. Be terse: three short lines at most. Lead with the "
    "concrete facts asked for — price, availability, spec, date — and give "
    "the number or value plainly. If the page does not answer the question, "
    "reply with exactly: not on this page. Never guess and never fill a gap "
    "from general knowledge."
)

_SYNTHESISE_SYSTEM = (
    "You compare findings gathered from several web pages and answer the "
    "user's question directly. Say which is best and why in one or two "
    "sentences, then list the sites with their key figures, one line each. "
    "Note plainly where a site could not be read rather than omitting it. "
    "This will be read aloud: no markdown tables, no headings."
)


@dataclass(slots=True)
class Finding:
    url: str
    site: str
    answer: str
    ok: bool = True

    def as_line(self) -> str:
        return f"{self.site}: {self.answer}"


def compare(urls: list[str], question: str) -> str:
    """Read each URL and answer `question` across all of them."""
    from peter.core.services import services

    container = services()
    cfg = container.config.agent.subagent
    if not cfg.enabled:
        return "Multi-site comparison is switched off in config.yml."

    targets = _unique(urls)[: cfg.max_sites]
    if len(targets) < 2:
        return "Give me at least two pages to compare."

    pages = _fetch_all(targets, container, cfg)
    findings = _extract_all(pages, question, container, cfg)

    if not any(f.ok for f in findings):
        return (
            "None of those pages could be read. "
            + "; ".join(f.as_line() for f in findings)
        )
    return _synthesise(findings, question, container)


# ------------------------------------------------------------------- fetching
def _fetch_all(urls: list[str], container, cfg) -> list[tuple[str, str]]:
    """Read each page in turn. Returns (url, page text or error marker)."""
    pages: list[tuple[str, str]] = []
    for url in urls:
        try:
            content = container.browser().read_page(url)
            pages.append((url, content.as_prompt(cfg.max_chars_per_site)))
        except Exception as exc:
            log.info("subagent: could not read %s (%s)", url, exc)
            pages.append((url, f"__error__{type(exc).__name__}: {exc}"))
    return pages


# ----------------------------------------------------------------- extracting
def _extract_all(pages, question: str, container, cfg) -> list[Finding]:
    readable = [(url, text) for url, text in pages if not text.startswith("__error__")]
    failed = [
        Finding(url, _site(url), text.replace("__error__", "could not read: "), ok=False)
        for url, text in pages
        if text.startswith("__error__")
    ]
    if not readable:
        return failed

    workers = min(cfg.max_workers, len(readable))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="subagent") as pool:
        results = list(
            pool.map(
                lambda item: _extract_one(item[0], item[1], question, container, cfg),
                readable,
            )
        )

    # Keep the caller's order, with the unreadable ones last.
    return results + failed


def _extract_one(url: str, page: str, question: str, container, cfg) -> Finding:
    from peter.llm import factory

    try:
        provider = factory.build_provider(
            container.config, _EXTRACT_SYSTEM,
            provider=cfg.provider or None, model=cfg.model or None,
        )
        try:
            provider.add_user(f"Question: {question}\n\nPage:\n{page}")
            response = provider.complete([])
        finally:
            provider.close()
    except Exception as exc:
        log.info("subagent: extraction failed for %s (%s)", url, exc)
        return Finding(url, _site(url), f"could not be read ({type(exc).__name__})", ok=False)

    answer = (response.text or "").strip() or "not on this page"
    return Finding(url, _site(url), answer, ok=True)


# ---------------------------------------------------------------- synthesising
def _synthesise(findings: list[Finding], question: str, container) -> str:
    from peter.llm import factory

    material = "\n\n".join(f"{f.site} ({f.url}):\n{f.answer}" for f in findings)
    try:
        provider = factory.build_provider(container.config, _SYNTHESISE_SYSTEM)
        try:
            provider.add_user(f"Question: {question}\n\nFindings:\n\n{material}")
            response = provider.complete([])
        finally:
            provider.close()
    except Exception:
        log.exception("subagent: could not synthesise, returning raw findings")
        return "\n".join(f.as_line() for f in findings)

    return (response.text or "").strip() or "\n".join(f.as_line() for f in findings)


# --------------------------------------------------------------------- helpers
def _unique(urls: list[str]) -> list[str]:
    seen, out = set(), []
    for url in urls:
        cleaned = url.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def _site(url: str) -> str:
    host = urlsplit(url if "://" in url else f"https://{url}").netloc
    return host.replace("www.", "") or url
