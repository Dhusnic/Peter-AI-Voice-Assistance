"""Learning what you meant, from the moment you correct Peter.

The memory layer could already store a durable instruction (`set_preference`)
or a durable meaning (`remember_fact`), and `MemoryStore.context_block()`
already replays both into every later turn. What was missing was the
*trigger*: none of that happened unless the model itself decided to call a
memory tool mid-conversation, which it rarely did. So the same correction got
made again a week later.

This closes that loop. When a turn looks like a correction of the previous
one, the pair is handed to one small, isolated, tool-free model call whose
only job is to answer: **is there a rule here that will still be true next
week?** Usually there is not, and saying so is the expected answer.

Three deliberate restraints, because a system that learns the wrong thing
permanently is worse than one that never learns:

**It prefers silence.** The extractor is told to return NOTHING unless the
lesson generalises past this one request, and NOTHING is the documented
default rather than a failure case. A one-off ("no, make it 6pm instead") must
not become a standing rule.

**It never deletes to make room.** Preferences are injected on *every* turn,
so they cannot grow without bound — but the cap is enforced by declining to
learn something new, never by evicting a preference you set on purpose. The
user is told the list is full instead; `forget_preference` is one sentence
away. Silently dropping a rule someone deliberately set is the kind of thing
that makes a memory feature untrustworthy.

**It says what it learned.** Every stored lesson returns a one-line
announcement that gets appended to the spoken reply, and `list_preferences`
already exists to audit the result. Behaviour that changes without telling you
is indistinguishable from a bug.

Cost is bounded by a keyword pre-filter (`looks_like_correction`): an ordinary
turn never reaches the model call at all, so this is free except on the rare
turn that actually reads like a correction — and a false positive there costs
one cheap call that returns NOTHING, not a bad rule.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

_SYSTEM = (
    "You extract durable, reusable lessons from a moment where a user "
    "corrected an assistant. Output exactly one line and nothing else.\n\n"
    "If the correction teaches something that will still be true next week "
    "and applies beyond this single request, output one of:\n"
    "  preference|<key>|<instruction>   how the assistant should behave\n"
    "  fact|<key>|<meaning>             what a word or phrase means to this user\n\n"
    "If it does not generalise -- a one-off, a typo, a changed mind about "
    "this specific request, or simple dissatisfaction with no instruction in "
    "it -- output exactly:\n"
    "  NOTHING\n\n"
    "Strongly prefer NOTHING. A wrong permanent rule is far worse than no "
    "rule. Only emit a line when the user has told you what they want in "
    "general, not merely what they wanted this time.\n\n"
    "<key> is snake_case, under 30 characters, naming the topic.\n"
    "<instruction> and <meaning> are one short sentence, under 120 "
    "characters, written as a standing instruction.\n\n"
    "Examples:\n"
    "  User asked for a summary, got five paragraphs, said 'no, I want it "
    "short, always two sentences max'\n"
    "  -> preference|summary_length|Keep summaries to two sentences at most.\n\n"
    "  User said 'when I say the usual I mean filter coffee, no sugar'\n"
    "  -> fact|the_usual|Filter coffee with no sugar.\n\n"
    "  User asked to set a 5pm reminder then said 'no, make it 6pm'\n"
    "  -> NOTHING"
)

# Openers that begin a correction. Word-bounded so "note that..." does not
# match "no", and anchored to the start because "no" mid-sentence is usually
# ordinary speech ("there is no rush").
_OPENERS = re.compile(r"^\s*(no|nope|nah|not|wrong|actually|incorrect)\b", re.I)

# Phrases that read as a correction wherever they appear.
_PHRASES = re.compile(
    r"\b("
    r"i meant|i said|i asked for|when i say|when i ask|"
    r"from now on|next time|in future|in the future|"
    r"that's not|thats not|that is not|not what i|"
    r"didn't mean|didnt mean|did not mean|"
    r"stop doing|instead of|rather than"
    r")\b",
    re.I,
)

# Bounds on what the extractor is allowed to write into memory, applied on our
# side rather than trusted from the model's output.
_MAX_KEY = 40
_MAX_VALUE = 200


@dataclass(slots=True, frozen=True)
class Lesson:
    kind: str  # "preference" | "fact"
    key: str
    value: str

    def announcement(self) -> str:
        if self.kind == "fact":
            return f"Noted — I'll remember {self.key.replace('_', ' ')} means {self.value}"
        return f"Noted — {self.value}"


def looks_like_correction(text: str) -> bool:
    """Cheap pre-filter, no model call. True if this turn reads like the user
    telling Peter it got the previous one wrong.

    Deliberately loose: a false positive costs one small model call that
    returns NOTHING, while a false negative silently loses the lesson.
    """
    if not text or not text.strip():
        return False
    return bool(_OPENERS.search(text) or _PHRASES.search(text))


def _parse(raw: str) -> Lesson | None:
    """Turn the extractor's one line into a Lesson, or None.

    Everything unrecognised becomes None on purpose. This parses untrusted
    model output that is about to be written into long-term memory, so the
    bar is "exactly the documented shape" rather than "close enough".
    """
    line = (raw or "").strip().splitlines()[0].strip() if (raw or "").strip() else ""
    if not line or line.upper().startswith("NOTHING"):
        return None

    parts = [p.strip() for p in line.split("|")]
    if len(parts) != 3:
        log.debug("learning: unparseable extractor output %r", line)
        return None

    kind, key, value = parts
    kind = kind.lower()
    if kind not in ("preference", "fact") or not key or not value:
        log.debug("learning: unusable extractor output %r", line)
        return None

    return Lesson(kind=kind, key=key[:_MAX_KEY], value=value[:_MAX_VALUE])


def extract_lesson(
    previous_user: str, previous_reply: str, correction: str, config
) -> Lesson | None:
    """One isolated, tool-free model call. None when there is nothing durable
    to learn, which is the common case. Never raises."""
    from peter.llm import factory

    cfg = config.agent.learning
    material = (
        f"Earlier, the user said:\n{previous_user}\n\n"
        f"The assistant replied:\n{previous_reply}\n\n"
        f"The user then corrected it with:\n{correction}"
    )
    try:
        provider = factory.build_provider(
            config, _SYSTEM,
            provider=cfg.provider or None, model=cfg.model or None,
        )
        try:
            provider.add_user(material)
            response = provider.complete([])
        finally:
            provider.close()
    except Exception as exc:
        log.info("learning: extraction call failed (%s)", exc)
        return None

    return _parse(response.text or "")


def learn_from_correction(
    memory, previous_user: str, previous_reply: str, correction: str, config
) -> str | None:
    """Detect, extract, and store a lesson. Returns a one-line announcement to
    append to the reply, or None if nothing was learned.

    Best-effort throughout: this runs inside a live turn, so no failure here
    may propagate into the answer the user is waiting for.
    """
    cfg = config.agent.learning
    if not cfg.enabled:
        return None
    if not (previous_user and correction):
        return None
    if not looks_like_correction(correction):
        return None

    lesson = extract_lesson(previous_user, previous_reply, correction, config)
    if lesson is None:
        log.debug("learning: nothing durable in this correction")
        return None

    try:
        if lesson.kind == "preference":
            existing = dict(memory.all_preferences())
            if lesson.key not in existing and len(existing) >= cfg.max_preferences:
                # Never evict to make room -- see the module docstring.
                log.info(
                    "learning: preference list is full (%d), not storing %r",
                    len(existing), lesson.key,
                )
                return (
                    "I'd note that, but my standing-preferences list is full — "
                    "ask me to forget one first."
                )
            memory.set_preference(lesson.key, lesson.value)
        else:
            memory.set_fact(lesson.key, lesson.value, source="learned")
    except Exception:
        log.exception("learning: could not store %r", lesson.key)
        return None

    log.info("learning: stored %s %r = %r", lesson.kind, lesson.key, lesson.value)
    return lesson.announcement() if cfg.announce else None
