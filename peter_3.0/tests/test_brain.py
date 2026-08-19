"""The agent.

Since the multi-provider refactor, vendor behaviour is tested in test_llm.py.
What remains here is what Brain still owns: building each turn's user message,
routing tool calls through the registry so the permission gate sees them, and
switching provider without losing memory or cost totals.
"""

import pytest

from peter.agent import registry
from peter.agent.brain import Brain
from peter.llm.base import (
    STOP_END,
    STOP_TOOLS,
    LLMProvider,
    ProviderResponse,
    ToolCall,
    Usage,
)


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, responses=(), model="fake-model"):
        super().__init__(model, "SYSTEM")
        self.queued = list(responses)
        self.user_messages: list[str] = []
        self.tool_results: list = []
        self.trimmed_to: int | None = None
        self.was_reset = False

    def reset(self):
        self.was_reset = True

    def add_user(self, text):
        self.user_messages.append(text)

    def add_tool_results(self, results):
        self.tool_results.append(results)

    def trim(self, max_messages):
        self.trimmed_to = max_messages

    def complete(self, tools):
        self.last_tools = tools
        if self.queued:
            response = self.queued.pop(0)
        else:
            response = ProviderResponse(text="ok", stop_reason=STOP_END)
        self.usage.add(response.usage)
        return response


def make_brain(store, config, responses=()):
    provider = FakeProvider(responses)
    return Brain(memory=store, config=config, provider=provider), provider


# ------------------------------------------------------- the turn's user message
def test_current_time_goes_in_the_user_turn(store, config):
    brain, provider = make_brain(store, config)
    brain.ask("what time is it")

    sent = provider.user_messages[0]
    assert "<now>" in sent
    assert sent.endswith("what time is it")


def test_relevant_memory_is_injected(store, config):
    store.set_fact("bus_route", "route 70 to Gandhipuram")
    brain, provider = make_brain(store, config)

    brain.ask("which bus do I take home")

    assert "bus_route" in provider.user_messages[0]
    assert "route 70" in provider.user_messages[0]


def test_nothing_volatile_reaches_the_system_prompt(store, config):
    """The system prompt is the cached prefix on every vendor. Anything that
    changes per turn would void the cache on every single request."""
    store.set_fact("bus_route", "route 70")
    brain, provider = make_brain(store, config)

    brain.ask("which bus")

    assert "<now>" not in provider.system
    assert "bus_route" not in provider.system


def test_the_system_prompt_is_built_once(store, config):
    brain, provider = make_brain(store, config)
    before = provider.system
    brain.ask("one")
    brain.ask("two")
    assert provider.system == before


def test_history_is_trimmed_to_the_configured_limit(store, config):
    brain, provider = make_brain(store, config)
    brain.ask("hello")
    assert provider.trimmed_to == config.agent.max_history_messages


# --------------------------------------------------------------- tool routing
def test_tool_calls_run_through_the_registry(store, config):
    """Every vendor SDK offers an auto-executing runner. Using one would run
    tools without passing the permission gate — this is the only path."""
    registry.load_all_tools()
    brain, provider = make_brain(store, config, [
        ProviderResponse(
            tool_calls=[ToolCall(id="1", name="get_current_time", arguments={})],
            stop_reason=STOP_TOOLS,
        ),
        ProviderResponse(text="It is six.", stop_reason=STOP_END),
    ])

    result = brain.ask("what time is it")

    assert result.tool_calls == ["get_current_time"]
    assert result.text == "It is six."
    # The real tool body ran and produced a real timestamp.
    assert provider.tool_results[0][0].content


def test_an_unknown_tool_is_reported_not_crashed(store, config):
    """A model can hallucinate a tool name. That must come back as a result."""
    registry.load_all_tools()
    brain, provider = make_brain(store, config, [
        ProviderResponse(
            tool_calls=[ToolCall(id="1", name="summon_a_dragon", arguments={})],
            stop_reason=STOP_TOOLS,
        ),
        ProviderResponse(text="I cannot do that.", stop_reason=STOP_END),
    ])

    result = brain.ask("summon a dragon")

    sent = provider.tool_results[0][0].content
    assert "no tool called" in sent
    assert result.text == "I cannot do that."


def test_every_registered_tool_is_offered_to_the_provider(store, config):
    registry.load_all_tools()
    brain, provider = make_brain(store, config)
    brain.ask("hello")

    offered = {t.name for t in provider.last_tools}
    assert "get_current_time" in offered
    assert len(offered) == len(registry.all_records())


# ------------------------------------------------------------ provider switching
def test_switching_provider_reports_both_ends(store, config, monkeypatch):
    brain, _ = make_brain(store, config)
    replacement = FakeProvider(model="other-model")
    replacement.name = "other"
    monkeypatch.setattr(
        "peter.llm.factory.build_provider", lambda *a, **k: replacement
    )

    message = brain.switch_provider("openai")

    assert "fake/fake-model" in message
    assert "other/other-model" in message
    assert brain.provider is replacement


def test_switching_preserves_memory(store, config, monkeypatch):
    store.set_fact("home_city", "Coimbatore")
    brain, _ = make_brain(store, config)
    monkeypatch.setattr(
        "peter.llm.factory.build_provider", lambda *a, **k: FakeProvider()
    )

    brain.switch_provider("gemini")
    # Query wording matters: memory lookup is a keyword search, so it must
    # share a token with the stored key.
    brain.ask("what city am I in")

    assert "Coimbatore" in brain.provider.user_messages[0]


def test_switching_carries_the_running_cost_forward(store, config, monkeypatch):
    """Comparing vendors on cost is the reason to switch. The total has to
    survive the switch or the comparison is impossible."""
    brain, provider = make_brain(store, config, [
        ProviderResponse(
            text="hi", stop_reason=STOP_END,
            usage=Usage(input_tokens=1000, output_tokens=100, cost_usd=0.05),
        )
    ])
    brain.ask("hello")

    monkeypatch.setattr(
        "peter.llm.factory.build_provider", lambda *a, **k: FakeProvider()
    )
    brain.switch_provider("openai")

    summary = brain.usage_summary()
    assert brain.usage.input_tokens == 1000
    assert "$0.05" in summary


def test_reset_clears_the_conversation(store, config):
    brain, provider = make_brain(store, config)
    brain.reset()
    assert provider.was_reset


def test_provider_name_and_model_are_exposed(store, config):
    brain, _ = make_brain(store, config)
    assert brain.provider_name == "fake"
    assert brain.model == "fake-model"


def test_usage_summary_names_the_provider(store, config):
    brain, _ = make_brain(store, config)
    brain.ask("hello")
    summary = brain.usage_summary()
    assert "fake/fake-model" in summary
    assert "turns" in summary


# ================================== bounding history without losing context
def test_old_turns_are_folded_into_memory_before_they_age_out(store, config):
    """History is the one part of a request that cannot be cached, so trim()
    bounds it by discarding the oldest turns. Discarding silently loses
    context — ask something early, refer back to it later, and Peter has no
    idea. Recording an episode keeps the gist reachable."""
    brain, _ = make_brain(store, config)

    for i in range(config.agent.max_history_messages + 2):
        brain.ask(f"question number {i} about my thesis")

    episodes = store.recent_episodes(limit=5)
    assert episodes, "nothing was carried forward when history was trimmed"
    assert "thesis" in " ".join(episodes)


def test_a_short_conversation_records_no_episodes(store, config):
    """Folding is for turns about to be dropped. A normal exchange must not
    litter memory with episodes."""
    brain, _ = make_brain(store, config)
    for i in range(3):
        brain.ask(f"question {i}")

    assert store.recent_episodes(limit=5) == []


def test_folding_keeps_the_most_recent_turns_in_play(store, config):
    brain, _ = make_brain(store, config)
    for i in range(config.agent.max_history_messages + 2):
        brain.ask(f"question number {i}")

    # The newest turn is still live context, not folded away.
    assert brain._session_turns[-1] == f"question number {config.agent.max_history_messages + 1}"
    assert len(brain._session_turns) <= config.agent.max_history_messages


def test_close_releases_the_provider(store, config):
    """A held Gemini prompt cache bills storage per hour — shutdown must let
    it go."""
    brain, provider = make_brain(store, config)
    closed = []
    provider.close = lambda: closed.append(True)

    brain.close()
    assert closed == [True]


def test_switching_provider_releases_the_old_one(store, config, monkeypatch):
    brain, provider = make_brain(store, config)
    closed = []
    provider.close = lambda: closed.append(True)
    monkeypatch.setattr(
        "peter.llm.factory.build_provider", lambda *a, **k: FakeProvider()
    )

    brain.switch_provider("openai")
    assert closed == [True], "the old provider's cache would keep billing"
