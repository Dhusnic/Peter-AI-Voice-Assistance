"""Ranking spoken text against a list of named things.

Bookmarks, saved places and installed apps all need the same behaviour: take
something half-remembered and said out loud — "the staging dashboard" — and
either land on one obvious answer or come back with the two or three it might
have been.

Speech is why this is not a plain substring test. A transcript rarely matches a
stored title word for word: word order moves, filler words appear, plurals and
punctuation drift. So scoring is token-based with a sequence-similarity
fallback, and the *gap* between the best and second-best score decides whether
there is a clear winner or a question to ask.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Sequence, TypeVar

T = TypeVar("T")

# Words too common to carry meaning in a short spoken request.
_NOISE = frozenset({
    "the", "a", "an", "my", "me", "please", "open", "go", "to", "for", "of",
    "and", "on", "in", "at", "it", "that", "this", "some", "up", "page",
    "site", "website", "link", "bookmark", "folder", "directory", "show",
})


def tokens(text: str) -> list[str]:
    """Lowercase word tokens, punctuation stripped, noise words removed."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    meaningful = [w for w in words if w not in _NOISE]
    # If the request was nothing but noise words, keep them rather than
    # matching against an empty query and returning everything.
    return meaningful or words


def score(query: str, candidate: str) -> float:
    """0.0-1.0. How well `candidate` answers `query`."""
    q_tokens = tokens(query)
    c_tokens = tokens(candidate)
    if not q_tokens or not c_tokens:
        return 0.0

    q_set, c_set = set(q_tokens), set(c_tokens)
    exact = len(q_set & c_set) / len(q_set)

    # Credit prefixes too: "dash" should find "dashboard", which matters when
    # a transcript clips the end of a word. And credit near-misses within a
    # single word — "dashbord" for "dashboard" — because that is what a
    # speech transcript actually produces. Comparing word to word keeps this
    # tight; comparing whole phrases (below) is what lets noise through.
    def _close(q: str, c: str) -> bool:
        if c.startswith(q) or q.startswith(c):
            return True
        if min(len(q), len(c)) < 4:
            return False
        return SequenceMatcher(None, q, c).ratio() >= 0.8

    partial = sum(
        1 for q in q_set
        if q not in c_set and any(_close(q, c) for c in c_set)
    ) / len(q_set)

    overlap = exact + 0.6 * partial

    # Whole-string similarity catches transpositions and small mis-hearings
    # that token overlap misses entirely.
    fuzzy = SequenceMatcher(
        None, " ".join(q_tokens), " ".join(c_tokens)
    ).ratio()

    if overlap == 0.0:
        # Character similarity on its own is not evidence. Two unrelated
        # phrases share vowels and spaces: "zzz nothing" scores 0.44 against
        # "HDFC Net Banking" purely on incidental letters. Without at least one
        # word in common, discount it hard so noise cannot clear the floor.
        fuzzy *= 0.5

    best = max(overlap, fuzzy)
    # A query that appears verbatim is as good as it gets.
    if " ".join(q_tokens) in " ".join(c_tokens):
        best = max(best, 0.95)
    return min(1.0, best)


@dataclass
class Match:
    """The outcome of a lookup: one clear winner, or several to choose between."""

    best: object | None = None
    candidates: list = None            # type: ignore[assignment]
    confident: bool = False

    def __post_init__(self) -> None:
        if self.candidates is None:
            self.candidates = []


def rank(
    query: str,
    items: Sequence[T],
    key: Callable[[T], str],
    *,
    limit: int = 5,
    floor: float = 0.34,
    clear_win: float = 0.72,
    lead: float = 0.18,
) -> Match:
    """Rank `items` against `query` and decide whether the top one is obvious.

    Confident means: it scored well *and* it beat the runner-up by a clear
    margin. Two near-identical scores mean the request was genuinely ambiguous
    — the caller should ask rather than pick, because silently opening the
    wrong one of two similar bookmarks is worse than one short question.
    """
    scored = sorted(
        ((score(query, key(item)), item) for item in items),
        key=lambda pair: pair[0],
        reverse=True,
    )
    viable = [(s, item) for s, item in scored if s >= floor][:limit]
    if not viable:
        return Match(best=None, candidates=[], confident=False)

    top_score, top_item = viable[0]
    runner_up = viable[1][0] if len(viable) > 1 else 0.0
    confident = top_score >= clear_win and (top_score - runner_up) >= lead

    return Match(
        best=top_item,
        candidates=[item for _s, item in viable],
        confident=confident,
    )
