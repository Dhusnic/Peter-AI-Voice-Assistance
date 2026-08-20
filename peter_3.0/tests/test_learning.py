"""Learning a standing rule from a correction.

The behaviour worth protecting here is mostly *restraint*: the extractor is
allowed — expected, usually — to decide there is nothing durable to learn, and
a preference the user set on purpose must never be deleted to make room for
one Peter inferred. Those two are the tests that matter most; the happy path
is the easy part.

No real model call is made anywhere: `peter.llm.factory.build_provider` is
monkeypatched to a scripted stand-in, same approach tests/test_subagents.py
uses for the same boundary.
"""

from __future__ import annotations

import pytest

from peter.agent import learning
from peter.agent.learning import Lesson, _parse, looks_like_correction
from peter.core.config import Config
from peter.llm.base import ProviderResponse


class ScriptedProvider:
    """Returns one canned line, whatever it is asked."""

    def __init__(self, reply: str, log: list | None = None, raises=None):
        self.reply = reply
        self.log = log if log is not None else []
        self.raises = raises
        self.sent = ""

    def add_user(self, text):
        self.sent = text
        self.log.append(text)

    def complete(self, tools):
        assert tools == []  # the extractor must never be given tools
        if self.raises is not None:
            raise self.raises
        return ProviderResponse(text=self.reply)

    def close(self): ...


@pytest.fixture
def extractor(monkeypatch):
    """Scripts the extraction call. Returns a handle whose `.reply` can be
    reassigned per-test and whose `.prompts` records what was sent."""

    class Handle:
        reply = "NOTHING"
        raises = None
        prompts: list = []

    handle = Handle()
    handle.prompts = []

    def build(*a, **k):
        return ScriptedProvider(handle.reply, handle.prompts, handle.raises)

    monkeypatch.setattr("peter.llm.factory.build_provider", build)
    return handle


# ------------------------------------------------------- the cheap pre-filter
@pytest.mark.parametrize("text", [
    "no, I meant tomorrow",
    "nope, that's wrong",
    "actually make it blue",
    "from now on keep replies short",
    "when I say the usual I mean filter coffee",
    "that's not what I asked for",
    "next time use the other account",
    "I meant the other file",
    "stop doing that",
])
def test_corrections_are_detected(text):
    assert looks_like_correction(text) is True


@pytest.mark.parametrize("text", [
    "what is the weather",
    "set a reminder for 5pm",
    "note that down",          # must not match the "no" opener
    "nothing much, thanks",    # ditto
    "open my email",
    "",
    "   ",
])
def test_ordinary_turns_are_not_corrections(text):
    """A false positive only costs one cheap call, but a pre-filter that
    fires on everything defeats the point of having one."""
    assert looks_like_correction(text) is False


def test_none_text_is_not_a_correction():
    assert looks_like_correction(None) is False


# ------------------------------------------------------------------ parsing
def test_parse_reads_a_preference():
    lesson = _parse("preference|reply_length|Keep replies to two sentences.")
    assert lesson == Lesson("preference", "reply_length", "Keep replies to two sentences.")


def test_parse_reads_a_fact():
    lesson = _parse("fact|the_usual|Filter coffee with no sugar.")
    assert lesson == Lesson("fact", "the_usual", "Filter coffee with no sugar.")


@pytest.mark.parametrize("raw", [
    "NOTHING",
    "nothing",
    "NOTHING — this was a one-off",
    "",
    "   ",
])
def test_parse_returns_none_for_nothing(raw):
    assert _parse(raw) is None


@pytest.mark.parametrize("raw", [
    "preference|only_two_fields",
    "preference|a|b|c|d",
    "bogus_kind|key|value",
    "preference||no key",
    "preference|key|",
    "just some prose with no pipes at all",
])
def test_parse_rejects_anything_off_format(raw):
    """This parses untrusted model output on its way into long-term memory,
    so the bar is the documented shape exactly, not 'close enough'."""
    assert _parse(raw) is None


def test_parse_takes_only_the_first_line():
    lesson = _parse("preference|tone|Be brief.\nAnd here is some extra chatter.")
    assert lesson == Lesson("preference", "tone", "Be brief.")


def test_parse_truncates_oversized_fields():
    lesson = _parse(f"preference|{'k' * 200}|{'v' * 900}")
    assert len(lesson.key) == 40
    assert len(lesson.value) == 200


# --------------------------------------------------------------- extraction
def test_extract_returns_a_lesson(extractor):
    extractor.reply = "preference|reply_length|Keep replies to two sentences."
    lesson = learning.extract_lesson("summarise this", "a long summary", "no, shorter", Config())
    assert lesson.kind == "preference"
    assert lesson.key == "reply_length"


def test_extract_sends_all_three_parts_of_the_exchange(extractor):
    """The extractor cannot judge whether a correction generalises without
    seeing what was asked and what it got wrong."""
    extractor.reply = "NOTHING"
    learning.extract_lesson("set a 5pm reminder", "Reminder set for 5pm.", "no, 6pm", Config())

    sent = extractor.prompts[0]
    assert "set a 5pm reminder" in sent
    assert "Reminder set for 5pm." in sent
    assert "no, 6pm" in sent


def test_extract_survives_a_failing_model_call(extractor):
    extractor.raises = RuntimeError("provider exploded")
    assert learning.extract_lesson("a", "b", "no, c", Config()) is None


# ------------------------------------------------------------------ storing
def test_a_correction_becomes_a_standing_preference(store, extractor):
    extractor.reply = "preference|reply_length|Keep replies to two sentences."

    note = learning.learn_from_correction(
        store, "summarise this", "a long summary", "no, keep it short always", Config()
    )

    assert dict(store.all_preferences())["reply_length"] == "Keep replies to two sentences."
    assert note and "Noted" in note


def test_a_vocabulary_correction_becomes_a_fact(store, extractor):
    extractor.reply = "fact|the_usual|Filter coffee with no sugar."

    learning.learn_from_correction(
        store, "order the usual", "Ordered a latte.",
        "no, when I say the usual I mean filter coffee", Config(),
    )

    assert dict(store.search_facts("the usual"))["the_usual"] == "Filter coffee with no sugar."


def test_a_one_off_correction_teaches_nothing(store, extractor):
    """The expected outcome most of the time. 'Make it 6pm instead' must not
    become a rule that every reminder is at 6pm."""
    extractor.reply = "NOTHING"

    note = learning.learn_from_correction(
        store, "set a 5pm reminder", "Reminder set for 5pm.", "no, make it 6pm", Config()
    )

    assert note is None
    assert store.all_preferences() == []


def test_nothing_is_learned_from_an_ordinary_turn(store, extractor):
    """No pre-filter match means no model call at all — the thing that keeps
    this feature free on a normal turn."""
    extractor.reply = "preference|x|y"

    note = learning.learn_from_correction(
        store, "what is the weather", "It is 31 degrees.", "thanks, open my email", Config()
    )

    assert note is None
    assert extractor.prompts == []


def test_learning_can_be_switched_off(store, extractor):
    extractor.reply = "preference|reply_length|Keep it short."
    config = Config()
    config.agent.learning.enabled = False

    note = learning.learn_from_correction(
        store, "summarise", "long", "no, always keep it short", config
    )

    assert note is None
    assert store.all_preferences() == []
    assert extractor.prompts == []


def test_announcement_can_be_suppressed_while_still_learning(store, extractor):
    extractor.reply = "preference|reply_length|Keep it short."
    config = Config()
    config.agent.learning.announce = False

    note = learning.learn_from_correction(
        store, "summarise", "long", "no, always keep it short", config
    )

    assert note is None                     # nothing said
    assert store.all_preferences()          # but it was still learned


def test_no_previous_exchange_means_nothing_to_correct(store, extractor):
    extractor.reply = "preference|x|y"
    assert learning.learn_from_correction(store, "", "", "no, I meant that", Config()) is None
    assert extractor.prompts == []


# ------------------------------------------------- the cap never deletes data
def test_at_the_cap_a_new_preference_is_declined_not_swapped_in(store, extractor):
    """The rule that keeps this trustworthy: a preference the user set on
    purpose is never evicted to make room for one Peter inferred."""
    config = Config()
    config.agent.learning.max_preferences = 3
    for i in range(3):
        store.set_preference(f"mine_{i}", f"value {i}")

    extractor.reply = "preference|inferred|Something Peter worked out."
    note = learning.learn_from_correction(
        store, "summarise", "long", "no, always keep it short", config
    )

    prefs = dict(store.all_preferences())
    assert len(prefs) == 3
    assert "inferred" not in prefs
    assert set(prefs) == {"mine_0", "mine_1", "mine_2"}   # nothing was deleted
    assert note and "full" in note.lower()                # and it said so


def test_at_the_cap_an_existing_preference_can_still_be_updated(store, extractor):
    """Updating in place does not grow the list, so the cap has no reason to
    block it — otherwise a correction to an existing rule would be refused."""
    config = Config()
    config.agent.learning.max_preferences = 2
    store.set_preference("reply_length", "Keep replies to five sentences.")
    store.set_preference("tone", "Be formal.")

    extractor.reply = "preference|reply_length|Keep replies to two sentences."
    learning.learn_from_correction(
        store, "summarise", "long", "no, even shorter from now on", config
    )

    prefs = dict(store.all_preferences())
    assert len(prefs) == 2
    assert prefs["reply_length"] == "Keep replies to two sentences."


def test_a_storage_failure_never_escapes(monkeypatch, store, extractor):
    extractor.reply = "preference|reply_length|Keep it short."
    monkeypatch.setattr(
        store, "set_preference",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk is full")),
    )

    note = learning.learn_from_correction(
        store, "summarise", "long", "no, always keep it short", Config()
    )

    assert note is None


# ------------------------------------------------------------ Brain wiring
def _brain(store, config=None):
    from types import SimpleNamespace

    from peter.agent.brain import Brain

    brain = Brain.__new__(Brain)
    brain.memory = store
    brain.config = config or Config()
    brain.provider = SimpleNamespace()
    brain._last_exchange = None
    return brain


def test_brain_skips_learning_on_the_very_first_turn(store, extractor):
    extractor.reply = "preference|x|y"
    assert _brain(store)._learn("no, I meant something else") is None
    assert extractor.prompts == []


def test_brain_learns_from_the_previous_exchange(store, extractor):
    extractor.reply = "preference|reply_length|Keep replies to two sentences."
    brain = _brain(store)
    brain._last_exchange = ("summarise this", "a long summary")

    note = brain._learn("no, always keep it short")

    assert note and "Noted" in note
    assert "reply_length" in dict(store.all_preferences())


def test_ask_appends_the_note_and_feeds_the_next_turn(monkeypatch, store, extractor):
    """The whole loop in one test: a correction turn answers normally, says
    what it learned, and the rule is in memory in time for the next turn."""
    from types import SimpleNamespace

    from peter.llm.base import Usage

    extractor.reply = "preference|reply_length|Keep replies to two sentences."
    monkeypatch.setattr(
        "peter.llm.loop.run_turn",
        lambda **kw: SimpleNamespace(
            text="Here is the short version.", tool_calls=[], stop_reason="end"
        ),
    )

    brain = _brain(store)
    brain._last_exchange = ("summarise this", "a long summary")
    brain._session_turns = []
    brain._budget_warned = False
    brain.progress_hook = brain.retry_hook = None
    brain.provider = SimpleNamespace(usage=Usage(), trim=lambda n: None)
    monkeypatch.setattr(brain, "_check_budget", lambda: None)
    monkeypatch.setattr(brain, "_remember_dropped_turns", lambda: None)
    monkeypatch.setattr(brain, "_record_spend", lambda before: None)
    monkeypatch.setattr(brain, "_turn_tools", lambda t: [])
    monkeypatch.setattr(brain, "_build_user_content", lambda t: t)

    result = brain.ask("no, always keep it short")

    assert result.text.startswith("Here is the short version.")
    assert "Noted" in result.text
    # Stored without the announcement — the user corrects the answer they
    # got, not Peter's note about having learned from it.
    assert brain._last_exchange == ("no, always keep it short", "Here is the short version.")
    # And it reaches the next turn through the memory block, for free.
    assert "Keep replies to two sentences." in store.context_block("summarise the news")


def test_brain_learning_failure_never_breaks_the_turn(monkeypatch, store):
    brain = _brain(store)
    brain._last_exchange = ("a", "b")
    monkeypatch.setattr(
        "peter.agent.learning.learn_from_correction",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert brain._learn("no, I meant something else") is None
