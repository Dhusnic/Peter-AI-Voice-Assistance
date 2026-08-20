"""The Telegram bridge.

Most of what matters here is security and lifecycle rather than messaging: an
unknown chat must get nothing at all, a queued backlog must not execute on
startup, and a network failure must back off rather than spin.
"""

from types import SimpleNamespace

import pytest

from peter.core.errors import AuthError, IntegrationError
from peter.integrations import telegram
from peter.integrations.telegram.api import TelegramClient, Update
from peter.telegram_bridge import (
    RemoteConfirmer,
    TelegramBridge,
    _strip_command,
    find_chat_ids,
)


@pytest.fixture(autouse=True)
def _reset_client():
    telegram.reset_for_tests()
    yield
    telegram.reset_for_tests()


class FakeApi:
    """Stands in for TelegramClient."""

    def __init__(self, batches=None, error=None):
        self.batches = list(batches or [])
        self.error = error
        self.sent: list[tuple[int, str]] = []
        self.offsets: list[int] = []

    def me(self):
        return "@peterbot"

    def send(self, chat_id, text):
        self.sent.append((chat_id, text))
        return True

    def get_updates(self, offset, long_poll_seconds=25):
        self.offsets.append(offset)
        if self.error:
            raise self.error
        return self.batches.pop(0) if self.batches else []


def allow(config, chat_ids=(42,), token="test-token"):
    """Point the config at a working Telegram setup."""
    config.integrations.telegram.allowed_chat_ids = list(chat_ids)
    config.secrets.telegram_bot_token.get_secret_value = lambda: token  # type: ignore[method-assign]
    return config


# ---------------------------------------------------------------- the client
def test_a_client_without_a_token_refuses_to_be_built():
    with pytest.raises(AuthError):
        TelegramClient(token="")


def test_a_long_message_is_truncated_not_split(monkeypatch):
    client = TelegramClient(token="x", max_message_chars=100)
    sent = {}
    monkeypatch.setattr(
        client, "_call", lambda method, payload, timeout=None: sent.update(payload)
    )

    client.send(1, "a" * 500)

    assert len(sent["text"]) <= 100
    assert sent["text"].endswith("[…truncated]")


def test_an_empty_message_still_sends_something(monkeypatch):
    client = TelegramClient(token="x")
    sent = {}
    monkeypatch.setattr(
        client, "_call", lambda method, payload, timeout=None: sent.update(payload)
    )
    client.send(1, "   ")
    assert sent["text"] == "(nothing to say)"


def test_a_send_failure_is_reported_not_raised(monkeypatch):
    client = TelegramClient(token="x")

    def boom(*a, **k):
        raise IntegrationError("offline", service="telegram", recoverable=True)

    monkeypatch.setattr(client, "_call", boom)
    assert client.send(1, "hello") is False


def test_updates_are_parsed_into_messages(monkeypatch):
    client = TelegramClient(token="x")
    monkeypatch.setattr(client, "_call", lambda *a, **k: [
        {"update_id": 7, "message": {"text": "  what time is it  ",
                                     "chat": {"id": 42},
                                     "from": {"username": "dhusnic"}}},
    ])

    updates = client.get_updates(offset=0)

    assert updates == [Update(update_id=7, chat_id=42, text="what time is it",
                              sender="dhusnic")]


def test_a_message_with_no_text_is_still_acknowledged(monkeypatch):
    """A photo with no caption must not wedge the offset and be re-delivered
    forever."""
    client = TelegramClient(token="x")
    monkeypatch.setattr(client, "_call", lambda *a, **k: [
        {"update_id": 9, "message": {"chat": {"id": 42}, "photo": []}},
    ])

    updates = client.get_updates(offset=0)

    assert updates[0].update_id == 9
    assert updates[0].text == ""


# --------------------------------------------------------- the client() accessor
def test_client_stays_callable_across_repeated_calls(container):
    """Regression test for a real shipped bug: peter.integrations.telegram
    defines a function `client()`, and the package also has a submodule
    `api.py` (formerly named `client.py`, which collided). Importing that
    submodule from inside the function makes Python bind the submodule onto
    the *package's own namespace* under the submodule's name — if that name
    were still `client`, it would silently overwrite the function, and the
    second-ever call to `telegram.client(config)` would raise
    "'module' object is not callable" instead of building a client.

    This must go through the real, unpatched code path — monkeypatching
    `telegram.client` (as most other tests here do, for speed) replaces the
    exact attribute this bug corrupts, which is why those tests never caught
    it even though the collision shipped."""
    allow(container.config)

    first = telegram.client(container.config)
    second = telegram.client(container.config)

    assert callable(telegram.client)
    assert first is second  # also: built once, reused


# ------------------------------------------------------------------- push
def test_push_does_nothing_when_no_chat_is_allowed(container):
    container.config.integrations.telegram.allowed_chat_ids = []
    assert telegram.push("title", "message") == 0


def test_push_does_nothing_when_forwarding_is_off(container, monkeypatch):
    allow(container.config)
    monkeypatch.setattr(
        container.config.integrations.telegram, "forward_notifications", False
    )
    assert telegram.push("title", "message") == 0


def test_push_sends_to_every_allowed_chat(container, monkeypatch):
    allow(container.config, chat_ids=(1, 2))
    api = FakeApi()
    monkeypatch.setattr(telegram, "client", lambda config: api)

    assert telegram.push("Peter — reminder", "stretch") == 2
    assert api.sent[0][1] == "Peter — reminder\n\nstretch"


def test_push_survives_telegram_being_down(container, monkeypatch):
    """A failed notification must never fail the scheduled job behind it."""
    allow(container.config)

    def boom(config):
        raise IntegrationError("offline", service="telegram")

    monkeypatch.setattr(telegram, "client", boom)
    assert telegram.push("title", "message") == 0


def test_notify_forwards_to_telegram(container, monkeypatch):
    """The single wiring that makes every proactive feature reach the phone."""
    from peter.core import notify as notify_module

    allow(container.config)
    pushed = []
    monkeypatch.setattr(notify_module, "_toast", lambda *a: None)
    monkeypatch.setattr(telegram, "push", lambda t, m: pushed.append((t, m)))

    notify_module.notify("Peter — price watch", "the monitor dropped")

    assert pushed == [("Peter — price watch", "the monitor dropped")]


def test_notify_survives_a_broken_telegram(container, monkeypatch):
    from peter.core import notify as notify_module

    monkeypatch.setattr(notify_module, "_toast", lambda *a: None)

    def boom(title, message):
        raise RuntimeError("nope")

    monkeypatch.setattr(telegram, "push", boom)
    notify_module.notify("title", "message")  # must not raise


# ------------------------------------------------------------------ bridge
def bridge_for(container, api, batches=None):
    allow(container.config)
    replies = []
    bridge = TelegramBridge(container.config, handler=lambda t: _record(replies, t))
    bridge._client = api
    if batches is not None:
        api.batches = list(batches)
    return bridge, replies


def _record(replies, text):
    replies.append(text)
    return f"answer to {text}"


def test_an_allowed_chat_gets_its_answer(container):
    api = FakeApi()
    bridge, replies = bridge_for(container, api)

    bridge._handle(Update(1, 42, "what time is it", "dhusnic"))

    assert replies == ["what time is it"]
    assert api.sent == [(42, "answer to what time is it")]


def test_an_unknown_chat_gets_complete_silence(container):
    """Not even a refusal: replying confirms the bot exists to a stranger."""
    api = FakeApi()
    bridge, replies = bridge_for(container, api)

    bridge._handle(Update(1, 999, "delete everything", "someone"))

    assert replies == []
    assert api.sent == []


def test_a_repeated_stranger_is_only_logged_once(container, caplog):
    api = FakeApi()
    bridge, _replies = bridge_for(container, api)

    with caplog.at_level("WARNING"):
        bridge._handle(Update(1, 999, "hello", "x"))
        bridge._handle(Update(2, 999, "hello again", "x"))

    assert sum("ignoring messages" in r.message for r in caplog.records) == 1


def test_slash_start_gets_a_greeting_not_a_model_call(container):
    api = FakeApi()
    bridge, replies = bridge_for(container, api)

    bridge._handle(Update(1, 42, "/start", "dhusnic"))

    assert replies == []
    assert "Peter here" in api.sent[0][1]


def test_the_backlog_is_acknowledged_but_not_executed(container):
    """Messages sent while Peter was off must not all fire at startup."""
    api = FakeApi(batches=[[Update(10, 42, "old message", "x"),
                           Update(11, 42, "older still", "x")]])
    bridge, replies = bridge_for(container, api)

    bridge._drop_backlog()

    assert bridge._offset == 12
    assert replies == []


def test_dropping_a_backlog_survives_telegram_being_unreachable(container):
    api = FakeApi(error=IntegrationError("offline", service="telegram"))
    bridge, _replies = bridge_for(container, api)

    bridge._drop_backlog()  # must not raise

    assert bridge._offset == 0


def test_a_handler_crash_does_not_re_deliver_the_message(container):
    """Advancing the offset first is what stops one bad message looping."""
    api = FakeApi(batches=[[Update(5, 42, "boom", "x")]])
    allow(container.config)
    bridge = TelegramBridge(container.config, handler=lambda t: t)
    bridge._client = api

    def explode(text):
        bridge.stopping.set()  # end the loop after this one message
        raise RuntimeError("handler blew up")

    bridge.handler = explode
    bridge._run()

    assert bridge._offset == 6


def test_the_bridge_refuses_to_start_with_no_allowed_chats(container, caplog):
    allow(container.config, chat_ids=())
    bridge = TelegramBridge(container.config, handler=lambda t: t)

    with caplog.at_level("WARNING"):
        assert bridge.start() is False
    assert "no allowed_chat_ids" in caplog.text


def test_the_bridge_refuses_to_start_with_no_token(container):
    container.config.integrations.telegram.allowed_chat_ids = [42]
    container.config.secrets.telegram_bot_token.get_secret_value = lambda: ""  # type: ignore[method-assign]
    bridge = TelegramBridge(container.config, handler=lambda t: t)
    assert bridge.start() is False


# --------------------------------------------------------------- confirming
def test_a_remote_turn_declines_a_confirm_tier_tool_immediately():
    confirmer = RemoteConfirmer()
    assert confirmer.ask("delete_file(path=x)", timeout=45) is False


def test_the_gate_uses_the_remote_confirmers_own_explanation(audit):
    from peter.policy.gate import Policy, PolicyGate

    gate = PolicyGate(
        Policy(default_tiers={"write": "confirm"}), audit, RemoteConfirmer()
    )
    result = gate("delete_file", "write", lambda **k: "deleted", {"path": "x"})

    assert "at the machine itself" in result
    assert "user declined" not in result


# ------------------------------------------------------------------ setup
def test_finding_chat_ids_reports_who_messaged(container, monkeypatch):
    api = FakeApi(batches=[[Update(1, 42, "hi", "dhusnic")]])
    monkeypatch.setattr(telegram, "client", lambda config: api)

    assert find_chat_ids(container.config, seconds=5) == [(42, "dhusnic")]


# ---------------------------------------------------------------- commands
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/start", ""),
        ("/help", ""),
        ("/ask what time is it", "what time is it"),
        ("  what time is it  ", "what time is it"),
    ],
)
def test_slash_commands_become_plain_text(raw, expected):
    assert _strip_command(raw) == expected
