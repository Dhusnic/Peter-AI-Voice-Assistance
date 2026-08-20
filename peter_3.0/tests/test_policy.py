import json
import time
from contextlib import contextmanager

import pytest

from peter.policy.gate import (
    ALLOW,
    CONFIRM,
    DECLINED_MESSAGE,
    DENY,
    HANDOFF,
    HANDOFF_MESSAGE,
    AlwaysDeny,
    AutoApprove,
    ConsoleConfirmer,
    Policy,
    PolicyGate,
)


@pytest.fixture
def policy() -> Policy:
    return Policy(
        default_tiers={"read": ALLOW, "write": CONFIRM, "spend": HANDOFF},
        overrides={"set_reminder": ALLOW, "nuke": DENY},
        confirm_timeout_seconds=5,
    )


def test_tier_defaults(policy):
    assert policy.decide("read_file", "read") == ALLOW
    assert policy.decide("write_file", "write") == CONFIRM
    assert policy.decide("buy_item", "spend") == HANDOFF


def test_override_beats_tier_default(policy):
    assert policy.decide("set_reminder", "write") == ALLOW
    assert policy.decide("nuke", "read") == DENY


def test_read_runs_without_asking(policy, audit):
    approver = AutoApprove()
    gate = PolicyGate(policy, audit, approver)

    result = gate("get_time", "read", lambda: "six o'clock", {})

    assert result == "six o'clock"
    assert approver.asked == [], "read-tier tools must never prompt"


def test_write_runs_when_approved(policy, audit):
    calls = []
    gate = PolicyGate(policy, audit, AutoApprove(True))

    result = gate("write_file", "write", lambda path: calls.append(path) or "ok",
                  {"path": "x.txt"})

    assert result == "ok"
    assert calls == ["x.txt"]


def test_declining_returns_a_result_and_does_not_run(policy, audit):
    """A refusal must reach Claude as a tool result, not raise and kill the turn."""
    calls = []
    gate = PolicyGate(policy, audit, AutoApprove(False))

    result = gate("delete_file", "write", lambda path: calls.append(path),
                  {"path": "important.txt"})

    assert result == DECLINED_MESSAGE
    assert calls == [], "the tool body must not run when declined"


def test_spend_never_executes(policy, audit):
    calls = []
    gate = PolicyGate(policy, audit, AutoApprove(True))

    result = gate("place_order", "spend", lambda: calls.append("ordered"), {})

    assert result == HANDOFF_MESSAGE
    assert calls == [], "spend-tier tools must not run even with approval"


def test_deny_override_blocks(policy, audit):
    gate = PolicyGate(policy, audit, AutoApprove(True))
    result = gate("nuke", "read", lambda: "boom", {})
    assert "Blocked by policy" in result


def test_default_confirmer_fails_closed(policy, audit):
    """With no UI wired up, a write must be refused rather than silently run."""
    calls = []
    gate = PolicyGate(policy, audit, AlwaysDeny())

    result = gate("delete_file", "write", lambda: calls.append("ran"), {})

    assert result == DECLINED_MESSAGE
    assert calls == []


def test_tool_exception_is_returned_not_raised(policy, audit):
    def boom():
        raise ValueError("disk on fire")

    gate = PolicyGate(policy, audit, AutoApprove(True))
    result = gate("read_file", "read", boom, {})

    assert "Error running read_file" in result
    assert "disk on fire" in result


def test_unknown_tier_defaults_to_confirm(audit):
    gate = PolicyGate(Policy(), audit, AutoApprove(False))
    assert gate("mystery", "sideways", lambda: "ran", {}) == DECLINED_MESSAGE


# ------------------------------------------------------------------ audit log
def test_audit_records_every_outcome(policy, audit):
    gate = PolicyGate(policy, audit, AutoApprove(False))
    gate("get_time", "read", lambda: "six", {})
    gate("delete_file", "write", lambda: "gone", {})
    gate("place_order", "spend", lambda: "bought", {})

    entries = audit.tail(10)
    assert [e["decision"] for e in entries] == ["allow", "declined", "handoff"]
    assert all("ts" in e and "tool" in e for e in entries)


def test_audit_redacts_secrets(audit):
    audit.record(
        tool="login", tier="write", decision="allow",
        args={"user": "dhusnic", "password": "hunter2", "otp": "123456"},
    )
    entry = audit.tail(1)[0]
    assert entry["args"]["user"] == "dhusnic"
    assert entry["args"]["password"] == "<redacted>"
    assert entry["args"]["otp"] == "<redacted>"


def test_audit_truncates_huge_values(audit):
    audit.record(tool="read_file", tier="read", decision="allow",
                 result_summary="x" * 5000)
    entry = audit.tail(1)[0]
    assert len(entry["result"]) < 600
    assert "truncated" in entry["result"]


def test_audit_lines_are_valid_json(audit):
    audit.record(tool="t", tier="read", decision="allow", args={"a": 1})
    for line in audit.path.read_text(encoding="utf-8").splitlines():
        json.loads(line)


def test_policy_is_built_from_config():
    """Policy now comes from the validated config object, not a separate file."""
    from peter.core.config import PolicyConfig

    loaded = Policy.from_config(
        PolicyConfig(
            default_tiers={"read": ALLOW, "write": CONFIRM, "spend": HANDOFF},
            standing_rules={"set_timer": ALLOW},
            confirm_timeout_seconds=12,
        )
    )
    assert loaded.decide("set_timer", "write") == ALLOW
    assert loaded.decide("write_file", "write") == CONFIRM
    assert loaded.confirm_timeout_seconds == 12


def test_config_rejects_an_unknown_decision():
    """A typo in config.yml must fail at startup, not at the first tool call."""
    import pydantic

    from peter.core.config import PolicyConfig

    with pytest.raises(pydantic.ValidationError):
        PolicyConfig(standing_rules={"delete_file": "allowe"})


def test_shipped_policy_keeps_destructive_tools_gated(config):
    """Guards the real config/config.yml against an accidental loosening."""
    shipped = Policy.from_config(config.policy)
    for tool in (
        "delete_file",
        "run_powershell",
        "lock_workstation",
        "send_email",
        "delete_email",
        "delete_calendar_event",
    ):
        assert shipped.decide(tool, "write") == CONFIRM, f"{tool} must stay gated"


def test_shipped_policy_still_hands_off_spending(config):
    shipped = Policy.from_config(config.policy)
    assert shipped.decide("some_future_purchase_tool", "spend") == HANDOFF


# ------------------------------------------------------------------ perf
def test_perf_is_recorded_on_a_successful_call(policy, audit, tmp_path):
    from peter.perf import PerfLog

    perf_log = PerfLog(tmp_path / "perf.db")
    gate = PolicyGate(policy, audit, AutoApprove(True), perf=perf_log)

    gate("get_time", "read", lambda: "six", {})

    stats = perf_log.summary(hours=1)
    assert len(stats) == 1
    assert stats[0].tool == "get_time"
    assert stats[0].calls == 1
    perf_log.close()


def test_perf_is_recorded_on_a_failed_call(policy, audit, tmp_path):
    from peter.perf import PerfLog

    perf_log = PerfLog(tmp_path / "perf.db")
    gate = PolicyGate(policy, audit, AutoApprove(True), perf=perf_log)

    def boom():
        raise ValueError("nope")

    gate("read_file", "read", boom, {})

    stats = perf_log.summary(hours=1, include_errors=True)
    assert stats[0].calls == 1
    assert stats[0].errors == 1
    perf_log.close()


def test_perf_is_not_recorded_for_a_declined_call(policy, audit, tmp_path):
    """No tool body ran, so there is nothing to time."""
    from peter.perf import PerfLog

    perf_log = PerfLog(tmp_path / "perf.db")
    gate = PolicyGate(policy, audit, AutoApprove(False), perf=perf_log)

    gate("delete_file", "write", lambda: "gone", {})

    assert perf_log.summary(hours=1, include_errors=True) == []
    perf_log.close()


def test_gate_works_with_no_perf_log_at_all(policy, audit):
    """perf=None must never break a tool call — it's the default."""
    gate = PolicyGate(policy, audit, AutoApprove(True))
    assert gate("get_time", "read", lambda: "six", {}) == "six"


def test_perf_captures_cpu_and_wait_split(policy, audit, tmp_path):
    from peter.perf import PerfLog

    perf_log = PerfLog(tmp_path / "perf.db")
    gate = PolicyGate(policy, audit, AutoApprove(True), perf=perf_log)

    def sleepy():
        time.sleep(0.02)
        return "done"

    gate("slow_tool", "read", sleepy, {})

    s = perf_log.summary(hours=1)[0]
    assert s.avg_wall_ms >= 15  # allow scheduler slack under a 20ms sleep
    assert s.avg_wait_ms > s.avg_cpu_ms  # mostly waiting, not computing
    perf_log.close()


def test_perf_records_phases_a_tool_opts_into(policy, audit, tmp_path):
    from peter import perf as perf_module
    from peter.perf import PerfLog

    perf_log = PerfLog(tmp_path / "perf.db")
    gate = PolicyGate(policy, audit, AutoApprove(True), perf=perf_log)

    def instrumented():
        with perf_module.phase("step_a"):
            time.sleep(0.005)
        return "ok"

    gate("instrumented_tool", "read", instrumented, {})

    breakdown = perf_log.phase_breakdown("instrumented_tool", hours=1)
    assert breakdown[0][0] == "step_a"
    perf_log.close()


# ==================================================== ConsoleConfirmer + spinner
def test_console_confirmer_works_with_no_suspend_hook(monkeypatch):
    """Backwards compatible: nothing wired up a spinner, so there is nothing
    to pause. Must not raise."""
    monkeypatch.setattr("builtins.input", lambda _p: "y")
    assert ConsoleConfirmer().ask("delete a file", timeout=5) is True


def test_console_confirmer_suspends_around_the_prompt(monkeypatch):
    """The actual bug this exists to prevent: a spinner still animating while
    input() reads stdin mangles the '[y/N]' prompt and the typed answer.
    The suspend hook must wrap exactly the input() call, not more, not less."""
    events = []

    @contextmanager
    def suspend():
        events.append("stop")
        yield
        events.append("start")

    monkeypatch.setattr("builtins.input", lambda _p: events.append("input") or "y")

    result = ConsoleConfirmer(suspend=suspend).ask("run a command", timeout=5)
    assert result is True
    assert events == ["stop", "input", "start"]


def test_console_confirmer_still_resumes_after_a_decline(monkeypatch):
    """A session with several confirmations in one turn (real case: two
    run_powershell calls back to back) needs the spinner back for each one,
    not just the first."""
    events = []

    @contextmanager
    def suspend():
        events.append("stop")
        yield
        events.append("start")

    monkeypatch.setattr("builtins.input", lambda _p: "n")

    ConsoleConfirmer(suspend=suspend).ask("first", timeout=5)
    ConsoleConfirmer(suspend=suspend).ask("second", timeout=5)
    assert events == ["stop", "start", "stop", "start"]


def test_console_confirmer_resumes_even_if_input_is_interrupted(monkeypatch):
    """Ctrl-C or EOF mid-prompt must not leave the spinner permanently
    stopped for the rest of the session."""
    events = []

    @contextmanager
    def suspend():
        events.append("stop")
        yield
        events.append("start")

    def boom(_p):
        raise EOFError

    monkeypatch.setattr("builtins.input", boom)
    result = ConsoleConfirmer(suspend=suspend).ask("anything", timeout=5)

    assert result is False
    assert events == ["stop", "start"]
