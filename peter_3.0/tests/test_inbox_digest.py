"""Inbox triage digest: read-only, degrades to a bare count on any failure.

The classification call is the one genuinely new kind of thing here — a
tiny, tool-free, one-shot model call outside the live conversation. It must
never be allowed to turn a mail-reading feature into something that can
crash or, worse, draft/send anything.
"""

from types import SimpleNamespace

import pytest

from peter.core.errors import AuthError, IntegrationError, NotConfiguredError
from peter.inbox_digest import Digest, build_digest, check_inbox_digest, schedule_inbox_digest
from peter.llm.base import ProviderResponse


@pytest.fixture(autouse=True)
def _clear_last_announced():
    import peter.inbox_digest as mod

    mod._last_announced = None
    yield
    mod._last_announced = None


def message(sender, subject):
    return SimpleNamespace(sender=sender, subject=subject)


class FakeMail:
    def __init__(self, unread=0, messages=(), error=None):
        self.unread = unread
        self.messages = list(messages)
        self.error = error

    def count_unread(self):
        if self.error:
            raise self.error
        return self.unread

    def list_messages(self, criteria="UNSEEN", limit=25, folder=None):
        if self.error:
            raise self.error
        return self.messages[:limit]


class FakeClassifierProvider:
    """Stands in for whatever factory.build_provider() would return."""

    def __init__(self, reply: str):
        self.reply = reply
        self.closed = False
        self.sent = None

    def add_user(self, text):
        self.sent = text

    def complete(self, tools):
        assert tools == []
        return ProviderResponse(text=self.reply)

    def close(self):
        self.closed = True


# ------------------------------------------------------------------ Digest.spoken
def test_zero_unread_is_a_clear_inbox():
    assert Digest(unread=0).spoken() == "Inbox is clear."


def test_unread_with_nothing_flagged_just_states_the_count():
    text = Digest(unread=5, needs_response=[]).spoken()
    assert text == "You have 5 unread emails."


def test_one_unread_is_not_pluralised():
    assert "1 unread email." in Digest(unread=1).spoken()


def test_one_flagged_item_reads_as_singular():
    text = Digest(unread=4, needs_response=["Production issue"]).spoken()
    assert "One looks like it needs a response: Production issue." in text


def test_several_flagged_items_are_joined():
    text = Digest(
        unread=23, needs_response=["Azure feature discussion", "HR request", "Production issue"]
    ).spoken()
    assert text == (
        "You have 23 unread emails. 3 look like they need a response: "
        "Azure feature discussion, HR request, Production issue."
    )


# -------------------------------------------------------------------- build_digest
def test_no_unread_mail_skips_classification_entirely(container, monkeypatch):
    container.mail = lambda: FakeMail(unread=0)
    called = []
    monkeypatch.setattr(
        "peter.llm.factory.build_provider", lambda *a, **k: called.append(1)
    )

    digest = build_digest()

    assert digest == Digest(unread=0)
    assert called == []


def test_unread_mail_is_classified_and_labelled(container, monkeypatch):
    container.mail = lambda: FakeMail(unread=3, messages=[
        message("IT Ops", "Azure feature flag rollout — need approval"),
        message("Newsletter", "This week in tech"),
        message("PagerDuty", "PROD-1234 database latency spike"),
    ])
    provider = FakeClassifierProvider("1: Azure feature discussion\n3: Production issue")
    monkeypatch.setattr("peter.llm.factory.build_provider", lambda *a, **k: provider)

    digest = build_digest()

    assert digest.unread == 3
    assert digest.needs_response == ["Azure feature discussion", "Production issue"]
    assert "IT Ops: Azure feature flag rollout" in provider.sent
    assert "Newsletter" in provider.sent
    assert provider.closed is True


def test_a_none_reply_means_nothing_needs_a_response(container, monkeypatch):
    container.mail = lambda: FakeMail(unread=2, messages=[
        message("A", "x"), message("B", "y"),
    ])
    monkeypatch.setattr(
        "peter.llm.factory.build_provider",
        lambda *a, **k: FakeClassifierProvider("none"),
    )
    digest = build_digest()
    assert digest.needs_response == []


def test_malformed_or_out_of_range_lines_are_ignored_not_crashed(container, monkeypatch):
    container.mail = lambda: FakeMail(unread=1, messages=[message("A", "x")])
    provider = FakeClassifierProvider("not even the right format\n99: out of range\n1: This one's fine")
    monkeypatch.setattr("peter.llm.factory.build_provider", lambda *a, **k: provider)

    digest = build_digest()

    assert digest.needs_response == ["This one's fine"]


def test_classification_failure_degrades_to_count_only(container, monkeypatch):
    container.mail = lambda: FakeMail(unread=5, messages=[message("A", "x")])

    def boom(*a, **k):
        raise RuntimeError("network is down")

    monkeypatch.setattr("peter.llm.factory.build_provider", boom)

    digest = build_digest()

    assert digest.unread == 5
    assert digest.needs_response == []


def test_build_digest_lets_a_mail_error_propagate():
    """Unlike the scheduled watcher, the on-demand tool should behave like
    every other mail tool — a real failure surfaces, it is not swallowed."""
    from peter.core.services import ServiceContainer, set_container

    c = ServiceContainer()
    c.mail = lambda: FakeMail(error=IntegrationError("down", service="mail"))
    set_container(c)
    try:
        with pytest.raises(IntegrationError):
            build_digest()
    finally:
        set_container(None)


# ------------------------------------------------------------- check_inbox_digest
@pytest.fixture
def spoken(container):
    said = []
    container.speaker = SimpleNamespace(say=lambda t: said.append(t))
    return said


def test_disabled_digest_does_nothing(container, spoken, monkeypatch):
    monkeypatch.setattr(container.config.integrations.inbox_digest, "enabled", False)
    container.mail = lambda: FakeMail(unread=5)
    check_inbox_digest()
    assert spoken == []


def test_an_unchanged_count_is_not_re_announced(container, spoken, monkeypatch):
    container.mail = lambda: FakeMail(unread=3, messages=[message("A", "x")])
    monkeypatch.setattr("peter.llm.factory.build_provider",
                        lambda *a, **k: FakeClassifierProvider("none"))

    check_inbox_digest()
    check_inbox_digest()

    assert len(spoken) == 1


def test_a_changed_count_is_announced_again(container, spoken, monkeypatch):
    container.mail = lambda: FakeMail(unread=3, messages=[message("A", "x")])
    monkeypatch.setattr("peter.llm.factory.build_provider",
                        lambda *a, **k: FakeClassifierProvider("none"))
    check_inbox_digest()

    container.mail = lambda: FakeMail(unread=4, messages=[message("A", "x")])
    check_inbox_digest()

    assert len(spoken) == 2


def test_mail_not_configured_is_swallowed_quietly(container, spoken):
    def boom():
        raise NotConfiguredError("mail")

    container.mail = boom
    check_inbox_digest()
    assert spoken == []


def test_mail_unreachable_is_swallowed_not_raised(container, spoken):
    container.mail = lambda: FakeMail(error=AuthError("bad password", service="mail"))
    check_inbox_digest()  # must not raise
    assert spoken == []


# ---------------------------------------------------------------- scheduling
def test_disabled_digest_is_not_scheduled(config, monkeypatch):
    calls = []
    scheduler = SimpleNamespace(add_interval_job=lambda **kw: calls.append(kw))
    monkeypatch.setattr(config.integrations.inbox_digest, "enabled", False)

    schedule_inbox_digest(scheduler, config)

    assert calls == []


def test_enabled_digest_uses_a_stable_job_id(config):
    calls = []
    scheduler = SimpleNamespace(add_interval_job=lambda **kw: calls.append(kw))

    schedule_inbox_digest(scheduler, config)
    schedule_inbox_digest(scheduler, config)

    assert len(calls) == 2
    assert calls[0]["job_id"] == calls[1]["job_id"] == "inbox-digest-poll"
