"""Per-task model routing, for providers where it is wired up (Gemini only,
for now — see `agent.models.gemini: auto` in config.yml).

The point is cost without a quality trade-off: Gemini's flash tier is roughly
4x cheaper than its pro tier and answers a routine lookup just as well. Paying
pro-tier rates for "what time is it" is waste; answering a genuinely hard or
high-stakes request with the cheap tier is the actual risk this exists to
avoid. `classify()` decides which side of that line a turn falls on from the
text alone — no extra LLM call to decide, which would spend money and add
latency doing the very thing this is meant to save on.

Deliberately a static heuristic, not a learned one: it has to be free, instant,
and auditable — a wrong classification should be obvious from reading the
pattern that caused it, not a black box you have to guess at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LIGHT = "light"
HEAVY = "heavy"

# Strips the volatile context Brain injects ahead of the actual message
# (current time, recalled memory) so routing judges the request itself, not
# how much memory happened to be relevant to it.
_CONTEXT_TAGS = re.compile(r"<(now|memory)>.*?</\1>", re.IGNORECASE | re.DOTALL)

# Genuine multi-step reasoning or technical/creative production — worth
# paying ~3x more per token for. Deliberately NOT "why", "explain", or "plan"
# on their own: those are some of the most common words in ordinary speech
# ("why is my cpu high", "explain that", "what's my plan for today") and
# would have routed a large share of routine turns to the expensive model,
# defeating the entire point of routing. Everything here requires either a
# specific technical/creative verb or an explicit request for depth — a bare
# "why"/"explain" stays LIGHT unless it is long enough to trip the word count
# below, or paired with one of these.
_HEAVY_SIGNAL = re.compile(
    r"\b("
    r"compare|analy[sz]e|design|architect(?:ure)?|strategy|"
    r"debug|refactor|optimi[sz]e|"
    r"write (?:a|some|the) (?:code|script|function|program|essay|story|report)|"
    r"summari[sz]e|research|calculate|prove|"
    r"explain (?:in detail|thoroughly|step[- ]by[- ]step)|"
    r"reason through|step[- ]by[- ]step|pros and cons|trade[- ]?offs?"
    r")\b",
    re.IGNORECASE,
)

# Actions where a careless or wrong answer is expensive to undo. Routing these
# to the smarter model is a safety margin, not a cost decision — Peter's
# policy gate still confirms/blocks the action itself regardless of model.
# Narrower than it looks: plain "cancel"/"order" alone are common in harmless
# phrasing ("cancel that reminder", "what's on order") and are deliberately
# left out — only the money-adjacent and destructive verbs trigger this.
_HIGH_STAKES_SIGNAL = re.compile(
    r"\b("
    r"delete|wipe|format|uninstall|overwrite|"
    r"buy|purchase|pay|checkout|transfer|refund|"
    r"cancel (?:my|the|this) (?:order|subscription|payment|booking)|"
    r"send money|send payment|"
    r"shell|powershell|run command"
    r")\b",
    re.IGNORECASE,
)

# "add buy milk to my todo list" is bookkeeping about a future errand, not
# Peter buying anything — but it contains "buy", which would otherwise trip
# _HIGH_STAKES_SIGNAL. A todo/reminder/shopping-list item can mention any
# verb without the turn itself being that action, so these are checked first
# and always stay LIGHT regardless of what they mention.
_LIST_BOOKKEEPING_SIGNAL = re.compile(
    r"\b("
    r"add .+ to (?:my |the )?(?:to-?do|task|shopping|grocery) list|"
    r"remind me to|"
    r"set (?:a |an )?(?:reminder|alarm|timer)"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Classification:
    tier: str    # LIGHT or HEAVY
    reason: str  # human-readable, for logs and llm_status()


def classify(text: str, heavy_word_threshold: int = 40) -> Classification:
    """Classify one turn's raw text as routine (LIGHT) or complex/high-stakes
    (HEAVY). Order matters: high-stakes beats a short message, and a keyword
    match beats plain length, since a short request can still be dangerous."""
    stripped = _CONTEXT_TAGS.sub("", text or "").strip()

    if _LIST_BOOKKEEPING_SIGNAL.search(stripped):
        return Classification(LIGHT, "routine list/reminder bookkeeping")
    if _HIGH_STAKES_SIGNAL.search(stripped):
        return Classification(HEAVY, "high-stakes action word")
    if _HEAVY_SIGNAL.search(stripped):
        return Classification(HEAVY, "reasoning/complexity keyword")

    word_count = len(stripped.split())
    if word_count > heavy_word_threshold:
        return Classification(HEAVY, f"long request ({word_count} words)")

    return Classification(LIGHT, "routine, short request")
