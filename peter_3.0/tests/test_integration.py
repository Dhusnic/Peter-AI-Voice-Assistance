"""End-to-end wiring, with only the network stubbed.

Everything else here is the real thing: the real registry, the real policy file,
the real gate, real tool bodies, a real SQLite store. These are the tests that
would have caught peter_1.0's failure mode — each piece working while the whole
did nothing useful.
"""

from types import SimpleNamespace

import pytest

from peter.agent import registry
from peter.agent.brain import Brain
from peter.llm.base import (
    STOP_END,
    STOP_TOOLS,
    LLMProvider,
    ProviderResponse,
    ToolCall,
)
from peter.memory.store import MemoryStore
from peter.policy.audit import AuditLog
from peter.core.config import load_config
from peter.core.services import ServiceContainer, set_container
from peter.policy.gate import DECLINED_MESSAGE, AutoApprove, Policy, PolicyGate
from peter.scheduler.jobs import Scheduler


@pytest.fixture
def wired(tmp_path):
    """A fully wired Peter, minus audio and the network."""
    registry.load_all_tools()

    config = load_config()
    memory = MemoryStore(tmp_path / "peter.db")
    audit = AuditLog(tmp_path / "audit.jsonl")
    scheduler = Scheduler(tmp_path / "jobs.db")

    approver = AutoApprove(True)
    gate = PolicyGate(Policy.from_config(config.policy), audit, approver)
    registry.set_interceptor(gate)

    container = ServiceContainer(config)
    container.memory = memory
    container.audit = audit
    container.scheduler = scheduler
    set_container(container)

    yield SimpleNamespace(
        memory=memory, audit=audit, gate=gate, approver=approver,
        scheduler=scheduler, tmp=tmp_path, config=config, container=container,
    )

    registry.set_interceptor(None)
    set_container(None)
    memory.close()


def call(name: str, **kwargs):
    """Invoke a registered tool the way the SDK runner would."""
    return registry.get_record(name).sdk_tool.call(kwargs)


# ------------------------------------------------------------- read-tier path
def test_read_tool_runs_without_confirmation(wired):
    result = call("get_current_time")

    assert result
    assert wired.approver.asked == []
    assert wired.audit.tail(1)[0]["decision"] == "allow"


def test_read_tool_reaches_the_real_filesystem(wired):
    target = wired.tmp / "note.txt"
    target.write_text("filter coffee at 6am", encoding="utf-8")

    assert "filter coffee" in call("read_file", path=str(target))


# ------------------------------------------------------------ write-tier path
def test_write_tool_asks_then_acts(wired):
    target = wired.tmp / "written.txt"

    result = call("write_file", path=str(target), content="hello")

    assert wired.approver.asked, "a write must prompt"
    assert target.read_text(encoding="utf-8") == "hello"
    assert "Wrote" in result


def test_declining_a_delete_leaves_the_file_alone(wired):
    """The single most important behaviour in the whole permission layer."""
    target = wired.tmp / "precious.txt"
    target.write_text("do not lose this", encoding="utf-8")

    wired.approver.answer = False
    result = call("delete_file", path=str(target))

    assert result == DECLINED_MESSAGE
    assert target.exists(), "declining must not delete the file"
    assert target.read_text(encoding="utf-8") == "do not lose this"
    assert wired.audit.tail(1)[0]["decision"] == "declined"


def test_approving_a_delete_removes_the_file(wired):
    target = wired.tmp / "junk.txt"
    target.write_text("x", encoding="utf-8")

    call("delete_file", path=str(target))

    assert not target.exists()


def test_protected_system_paths_are_refused_even_when_approved(wired):
    """Approval is not permission to delete C:\\Windows."""
    result = call("delete_file", path="C:/Windows/System32/kernel32.dll")

    assert "protected system location" in result
    assert __import__("pathlib").Path("C:/Windows/System32/kernel32.dll").exists()


def test_tool_errors_come_back_as_text_not_exceptions(wired):
    result = call("read_file", path=str(wired.tmp / "nope.txt"))
    assert "not a file" in result


# ------------------------------------------------------------- memory round trip
def test_remembering_a_fact_makes_it_show_up_in_the_next_turn(wired):
    call("remember_fact", key="bus_route", value="route 70 to Gandhipuram")

    block = wired.memory.context_block("which bus do I take home")

    assert "bus_route" in block
    assert "route 70" in block


def test_recall_finds_a_stored_fact(wired):
    call("remember_fact", key="home_city", value="Coimbatore")
    assert "Coimbatore" in call("recall", query="city")


# --------------------------------------------------------- scheduler round trip
def test_reminder_persists_to_disk_and_survives_a_restart(wired):
    """A reminder set at 11pm must still fire at 7am after a crash."""
    wired.scheduler.start()
    try:
        result = call("set_reminder", text="stretch",
                      at_iso="2030-01-01T09:00:00")
        assert "Reminder set" in result
        assert "stretch" in call("list_reminders")
    finally:
        wired.scheduler.shutdown()

    # A brand-new Scheduler over the same file is what a restart looks like.
    revived = Scheduler(wired.tmp / "jobs.db")
    revived.start()
    try:
        assert any(j["text"] == "stretch" for j in revived.list_jobs())
    finally:
        revived.shutdown()


def test_reminder_in_the_past_is_rejected(wired):
    assert "in the past" in call("set_reminder", text="x",
                                 at_iso="2020-01-01T09:00:00")


def test_todo_lifecycle(wired):
    call("add_todo", text="submit DBMS assignment")
    assert "DBMS" in call("list_todos")

    call("complete_todo", matching_text="dbms")
    assert "empty" in call("list_todos")


# ----------------------------------------------------- full turn through Brain
class _ScriptedProvider(LLMProvider):
    """A provider that asks for one real tool, then answers."""

    name = "scripted"

    def __init__(self, tool_name):
        super().__init__("scripted-model", "SYSTEM")
        self.tool_name = tool_name
        self.calls = 0
        self.results = []

    def reset(self): ...
    def add_user(self, text): ...
    def trim(self, max_messages): ...

    def add_tool_results(self, results):
        self.results.extend(results)

    def complete(self, tools):
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                tool_calls=[ToolCall(id="1", name=self.tool_name, arguments={})],
                stop_reason=STOP_TOOLS,
            )
        return ProviderResponse(text="Your CPU is fine.", stop_reason=STOP_END)


def test_brain_drives_a_real_tool_through_the_real_gate(wired):
    """One turn end to end: the model asks for a tool, the gate runs it,
    the audit log records it. Only the network is faked."""
    provider = _ScriptedProvider("system_stats")
    brain = Brain(memory=wired.memory, config=wired.config, provider=provider)

    reply = brain.ask("how is my machine doing")

    assert reply.text == "Your CPU is fine."
    assert reply.tool_calls == ["system_stats"]
    assert "CPU:" in provider.results[0].content, "the real tool body ran"
    assert wired.audit.tail(1)[0]["tool"] == "system_stats"


def test_a_declined_tool_reaches_the_model_as_a_result(wired):
    """The permission gate's refusal must arrive as a tool result, so the
    model can adapt rather than the turn dying."""
    wired.approver.answer = False
    provider = _ScriptedProvider("lock_workstation")
    brain = Brain(memory=wired.memory, config=wired.config, provider=provider)

    brain.ask("lock my machine")

    assert "declined" in provider.results[0].content.lower()
    assert wired.audit.tail(1)[0]["decision"] == "declined"
