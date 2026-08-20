"""Routines: named chains of Peter's own tools, run as one voice command.

A routine step calls the target tool's raw_fn directly, bypassing the policy
gate — deliberately, since the routine itself was already a write-tier action
and the config.yml entry is the standing instruction. These tests exercise
that directly against the real registry rather than mocking it, since the
whole point under test is how routines interact with real tool records.
"""

from types import SimpleNamespace

import pytest

from peter import routines
from peter.agent import registry


def cfg_with(defs: dict):
    return SimpleNamespace(integrations=SimpleNamespace(routines=SimpleNamespace(defs=defs)))


@pytest.fixture(autouse=True)
def _fresh_registry():
    registry.reset_for_tests()
    yield
    registry.reset_for_tests()


def test_no_routines_configured_says_so():
    assert "No routines are set up" in routines.run("good night", cfg_with({}))


def test_unknown_routine_lists_what_is_configured():
    @registry.peter_tool(tier="write")
    def lock_workstation() -> str:
        """Lock the workstation."""
        return "locked"

    cfg = cfg_with({"good night": [{"tool": "lock_workstation", "args": {}}]})

    result = routines.run("start work", cfg)

    assert "no routine called" in result
    assert "good night" in result


def test_forgiving_name_lookup_matches_a_spoken_variant():
    @registry.peter_tool(tier="write")
    def lock_workstation() -> str:
        """Lock the workstation."""
        return "locked"

    cfg = cfg_with({"good night": [{"tool": "lock_workstation", "args": {}}]})

    result = routines.run("run my good night routine", cfg)

    assert "Ran: lock_workstation" in result


def test_runs_every_step_in_order():
    calls = []

    @registry.peter_tool(tier="write")
    def step_a() -> str:
        """Step A."""
        calls.append("a")
        return "a"

    @registry.peter_tool(tier="write")
    def step_b() -> str:
        """Step B."""
        calls.append("b")
        return "b"

    cfg = cfg_with({
        "good night": [
            {"tool": "step_a", "args": {}},
            {"tool": "step_b", "args": {}},
        ]
    })

    result = routines.run("good night", cfg)

    assert calls == ["a", "b"]
    assert "step_a" in result and "step_b" in result


def test_step_arguments_are_passed_through():
    seen = {}

    @registry.peter_tool(tier="write")
    def set_phone_alarm(hour: int, minute: int) -> str:
        """Set a phone alarm.

        Args:
            hour: The hour.
            minute: The minute.
        """
        seen["hour"] = hour
        seen["minute"] = minute
        return "set"

    cfg = cfg_with({
        "good night": [{"tool": "set_phone_alarm", "args": {"hour": 7, "minute": 0}}]
    })
    routines.run("good night", cfg)

    assert seen == {"hour": 7, "minute": 0}


def test_an_unknown_tool_name_is_reported_but_does_not_stop_the_routine():
    @registry.peter_tool(tier="write")
    def real_tool() -> str:
        """A real tool."""
        return "ran"

    cfg = cfg_with({
        "mixed": [
            {"tool": "not_a_real_tool", "args": {}},
            {"tool": "real_tool", "args": {}},
        ]
    })

    result = routines.run("mixed", cfg)

    assert "not_a_real_tool" in result and "not a known tool" in result
    assert "Ran: real_tool" in result


def test_a_failing_step_is_reported_but_does_not_stop_the_routine():
    @registry.peter_tool(tier="write")
    def first() -> str:
        """First step."""
        raise RuntimeError("boom")

    @registry.peter_tool(tier="write")
    def second() -> str:
        """Second step."""
        return "ok"

    cfg = cfg_with({
        "mixed": [
            {"tool": "first", "args": {}},
            {"tool": "second", "args": {}},
        ]
    })

    result = routines.run("mixed", cfg)

    assert "Could not run: first" in result
    assert "Ran: second" in result


def test_spend_tier_tools_are_refused_even_if_configured():
    @registry.peter_tool(tier="spend")
    def buy_thing() -> str:
        """Buy something."""
        return "bought"

    cfg = cfg_with({"shopping": [{"tool": "buy_thing", "args": {}}]})

    result = routines.run("shopping", cfg)

    assert "spend-tier tools cannot run" in result


def test_empty_step_list_says_so():
    cfg = cfg_with({"empty": []})
    assert "no steps configured" in routines.run("empty", cfg)


def test_describe_lists_every_routine_and_its_tools():
    cfg = cfg_with({
        "good night": [{"tool": "lock_workstation", "args": {}}],
        "start work": [{"tool": "focus_start", "args": {}}, {"tool": "open_app", "args": {}}],
    })

    text = routines.describe(cfg)

    assert "good night: lock_workstation" in text
    assert "start work: focus_start, open_app" in text


def test_describe_with_no_routines_says_so():
    assert "No routines are set up" in routines.describe(cfg_with({}))


# -------------------------------------------------------------------- tools
def test_run_routine_tool_delegates_to_routines_run(container, monkeypatch):
    registry.reset_for_tests()
    from peter.tools import routine_tools  # noqa: F401
    from peter import routines as routines_module

    monkeypatch.setattr(routines_module, "run", lambda name, cfg: f"ran {name}")

    result = registry.get_record("run_routine").raw_fn(name="good night")

    assert result == "ran good night"


def test_list_routines_tool_delegates_to_routines_describe(container, monkeypatch):
    registry.reset_for_tests()
    from peter.tools import routine_tools  # noqa: F401
    from peter import routines as routines_module

    monkeypatch.setattr(routines_module, "describe", lambda cfg: "described")

    assert registry.get_record("list_routines").raw_fn() == "described"
