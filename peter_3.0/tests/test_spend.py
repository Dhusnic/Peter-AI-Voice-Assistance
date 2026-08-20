"""The spend ledger and the daily cap.

Costs are stored in USD and converted for display only. That is the one design
decision worth guarding with tests: storing rupees would freeze each day's
exchange rate into history and make last month's numbers uncomparable.
"""

import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from peter.agent.brain import Brain
from peter.llm.base import ProviderResponse, ToolSpec, Usage
from peter.spend import SpendLog, budget_state, report


@pytest.fixture
def ledger(tmp_path):
    log = SpendLog(tmp_path / "spend.db")
    yield log
    log.close()


def days_ago(days):
    return (datetime.now() - timedelta(days=days)).timestamp()


# ------------------------------------------------------------------ the store
def test_a_recorded_turn_shows_up_in_todays_total(ledger):
    ledger.record("gemini", "gemini-3.7-flash", cost_usd=0.0042,
                  input_tokens=1200, output_tokens=300)

    assert ledger.today_usd() == pytest.approx(0.0042)
    assert ledger.turn_count(1) == 1


def test_a_free_turn_is_still_recorded(ledger):
    """A zero-cost turn is evidence the prompt cache is working — dropping it
    would make the turn count lie."""
    ledger.record("gemini", "gemini-3.7-flash", cost_usd=0.0)
    assert ledger.turn_count(1) == 1


def test_a_negative_cost_cannot_be_recorded(ledger):
    """Costs are derived by subtracting cumulative counters; a provider reset
    mid-session could otherwise write a negative row and hide real spend."""
    ledger.record("gemini", "m", cost_usd=-5.0)
    assert ledger.today_usd() == 0.0


def test_yesterdays_spend_is_not_counted_as_todays(ledger):
    ledger.record("gemini", "m", cost_usd=1.0, when=days_ago(1))
    ledger.record("gemini", "m", cost_usd=0.25)

    assert ledger.today_usd() == pytest.approx(0.25)
    assert ledger.since_usd(7) == pytest.approx(1.25)


def test_totals_are_grouped_by_day(ledger):
    ledger.record("gemini", "m", cost_usd=1.0, when=days_ago(1))
    ledger.record("gemini", "m", cost_usd=2.0)
    ledger.record("gemini", "m", cost_usd=3.0)

    daily = ledger.by_day(7)

    assert len(daily) == 2
    assert daily[0].cost_usd == pytest.approx(5.0)  # today, newest first
    assert daily[0].turns == 2


def test_totals_are_grouped_by_model_for_comparing_vendors(ledger):
    ledger.record("gemini", "flash", cost_usd=0.10)
    ledger.record("anthropic", "claude-opus-5", cost_usd=0.90)
    ledger.record("gemini", "flash", cost_usd=0.10)

    by_model = ledger.by_provider(7)

    assert by_model[0][:2] == ("anthropic", "claude-opus-5")  # most expensive first
    assert by_model[1][2] == 2  # two gemini turns


def test_pruning_drops_only_old_rows(ledger):
    ledger.record("gemini", "m", cost_usd=1.0, when=days_ago(500))
    ledger.record("gemini", "m", cost_usd=1.0)

    assert ledger.prune(keep_days=400) == 1
    assert ledger.turn_count(600) == 1


# ------------------------------------------------------------------- the cap
def budget(config, limit, action="warn"):
    config.agent.budget.daily_inr = limit
    config.agent.budget.action = action
    return config


def test_no_cap_configured_means_never_exceeded(config, ledger):
    state = budget_state(budget(config, 0), ledger)
    assert state.enabled is False
    assert state.exceeded is False


def test_a_cap_is_measured_in_rupees_not_dollars(config, ledger):
    config.agent.usd_to_inr_rate = 100.0
    ledger.record("gemini", "m", cost_usd=0.60)  # 60 rupees

    state = budget_state(budget(config, 50), ledger)

    assert state.spent_inr == pytest.approx(60.0)
    assert state.exceeded is True


def test_a_warn_cap_is_exceeded_but_never_blocks(config, ledger):
    config.agent.usd_to_inr_rate = 100.0
    ledger.record("gemini", "m", cost_usd=1.0)

    state = budget_state(budget(config, 50, "warn"), ledger)

    assert state.exceeded is True
    assert state.blocked is False


def test_a_block_cap_blocks(config, ledger):
    config.agent.usd_to_inr_rate = 100.0
    ledger.record("gemini", "m", cost_usd=1.0)

    assert budget_state(budget(config, 50, "block"), ledger).blocked is True


def test_spending_under_the_cap_does_not_block(config, ledger):
    config.agent.usd_to_inr_rate = 100.0
    ledger.record("gemini", "m", cost_usd=0.10)  # 10 rupees of a 50 rupee cap

    assert budget_state(budget(config, 50, "block"), ledger).blocked is False


# ---------------------------------------------------------------- the report
def test_the_report_says_so_when_nothing_was_spent(container):
    assert "No usage recorded" in report(7)


def test_the_report_totals_in_rupees(container):
    container.config.agent.usd_to_inr_rate = 100.0
    container.spend().record("gemini", "flash", cost_usd=0.50)
    container.spend().record("gemini", "flash", cost_usd=0.50)

    text = report(7)

    assert "100.00 rupees over 2 turns" in text
    assert "gemini/flash" in text


def test_the_report_shows_what_is_left_of_the_cap(container):
    container.config.agent.usd_to_inr_rate = 100.0
    container.config.agent.budget.daily_inr = 200
    container.spend().record("gemini", "flash", cost_usd=0.50)

    assert "150.00 left today" in report(1)


# --------------------------------------------------------- the brain wiring
class FakeProvider:
    """Minimal provider: answers once, and bills for it."""

    name = "fake"

    def __init__(self, cost=0.01):
        self.model = "fake-1"
        self.usage = Usage()
        self.cost = cost
        self.system = ""

    def reset(self): ...
    def close(self): ...
    def trim(self, n): ...
    def add_user(self, text): ...
    def add_tool_results(self, results): ...

    def complete(self, tools: list[ToolSpec]) -> ProviderResponse:
        self.usage.cost_usd += self.cost
        self.usage.input_tokens += 100
        self.usage.output_tokens += 20
        return ProviderResponse(text="done")


def test_every_turn_is_written_to_the_ledger(container, store):
    brain = Brain(memory=store, config=container.config, provider=FakeProvider(0.02))

    brain.ask("hello")
    brain.ask("again")

    assert container.spend().turn_count(1) == 2
    assert container.spend().today_usd() == pytest.approx(0.04)


def test_the_ledger_records_one_turns_cost_not_the_running_total(container, store):
    """Providers accumulate usage across the session, so the per-turn figure
    only comes out right if it is derived by subtraction."""
    brain = Brain(memory=store, config=container.config, provider=FakeProvider(0.02))

    brain.ask("one")
    brain.ask("two")

    assert [round(d.cost_usd, 4) for d in container.spend().by_day(1)] == [0.04]
    rows = container.spend().by_provider(1)
    assert rows[0][2] == 2  # two rows, not one row of 0.02 and one of 0.04


def test_a_block_cap_stops_the_turn_before_the_model_is_called(container, store):
    container.config.agent.usd_to_inr_rate = 100.0
    container.config.agent.budget.daily_inr = 1
    container.config.agent.budget.action = "block"
    container.spend().record("fake", "fake-1", cost_usd=1.0)  # 100 rupees

    provider = FakeProvider()
    brain = Brain(memory=store, config=container.config, provider=provider)
    result = brain.ask("anything")

    assert "stopped for today" in result.text
    assert provider.usage.cost_usd == 0.0  # never called


def test_a_warn_cap_says_something_once_then_carries_on(container, store):
    container.config.agent.usd_to_inr_rate = 100.0
    container.config.agent.budget.daily_inr = 1
    container.config.agent.budget.action = "warn"
    container.spend().record("fake", "fake-1", cost_usd=1.0)

    said = []
    container.speaker = SimpleNamespace(say=lambda t: said.append(t))
    brain = Brain(memory=store, config=container.config, provider=FakeProvider())

    assert brain.ask("one").text == "done"
    assert brain.ask("two").text == "done"

    assert len(said) == 1
    assert "daily cap" in said[0]


def test_a_broken_ledger_never_stops_peter_answering(container, store, monkeypatch):
    container.config.agent.budget.daily_inr = 1

    def boom():
        raise RuntimeError("disk is on fire")

    monkeypatch.setattr(container, "spend", boom)
    brain = Brain(memory=store, config=container.config, provider=FakeProvider())

    assert brain.ask("hello").text == "done"


def test_recording_time_is_the_moment_of_the_turn(ledger):
    before = time.time()
    ledger.record("gemini", "m", cost_usd=0.1)
    row = ledger.db.one("SELECT ts, day FROM spend")
    assert row["ts"] >= before
    assert row["day"] == datetime.now().strftime("%Y-%m-%d")
