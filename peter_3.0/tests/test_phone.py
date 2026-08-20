"""Reading the phone over ADB.

Almost all the risk is in parsing `content query` output, which is a format
with no escaping: a message body can contain the same ", " that separates
fields. These tests use real-shaped rows, including the awkward ones.
"""

import subprocess
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from peter.core.errors import IntegrationError
from peter.integrations.phone import adb


def phone_config(**kwargs):
    base = dict(
        enabled=True, adb_path="adb", device_serial="", sms_limit=10,
        otp_window_minutes=10, timeout_seconds=5.0,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def ms(minutes_ago=1):
    return int((datetime.now() - timedelta(minutes=minutes_ago)).timestamp() * 1000)


def fake_adb(monkeypatch, output="", returncode=0, stderr=""):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=returncode, stdout=output, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(adb, "available", lambda cfg: True)
    return calls


# ------------------------------------------------------------------ parsing
def test_a_message_is_parsed_into_sender_body_and_time(monkeypatch):
    fake_adb(monkeypatch, f"Row: 0 address=+919000000000, body=Your OTP is 123456, "
                          f"date={ms(2)}\n")

    found = adb.messages(phone_config())

    assert found[0].sender == "+919000000000"
    assert found[0].body == "Your OTP is 123456"
    assert found[0].when is not None


def test_a_body_containing_the_field_separator_is_not_truncated(monkeypatch):
    """Real messages are full of ", " — splitting on it loses half of them."""
    body = "Hi, your order is out for delivery, arriving by 6pm, track it here"
    fake_adb(monkeypatch, f"Row: 0 address=AX-BLINKIT, body={body}, date={ms(5)}\n")

    assert adb.messages(phone_config())[0].body == body


def test_messages_come_back_newest_first(monkeypatch):
    fake_adb(monkeypatch, (
        f"Row: 0 address=A, body=older, date={ms(60)}\n"
        f"Row: 1 address=B, body=newer, date={ms(1)}\n"
    ))

    assert [m.body for m in adb.messages(phone_config())] == ["newer", "older"]


def test_the_limit_is_respected(monkeypatch):
    rows = "".join(
        f"Row: {i} address=A, body=message {i}, date={ms(i + 1)}\n" for i in range(10)
    )
    fake_adb(monkeypatch, rows)

    assert len(adb.messages(phone_config(), limit=3)) == 3


def test_a_row_with_an_unparseable_date_is_still_returned(monkeypatch):
    fake_adb(monkeypatch, "Row: 0 address=A, body=hello, date=not-a-number\n")

    found = adb.messages(phone_config())

    assert found[0].body == "hello"
    assert found[0].when is None


def test_non_row_output_is_ignored(monkeypatch):
    fake_adb(monkeypatch, "No result found.\n")
    assert adb.messages(phone_config()) == []


def test_the_time_window_is_pushed_into_the_device_query(monkeypatch):
    """A phone with years of history must not have all of it transferred."""
    calls = fake_adb(monkeypatch, "")

    adb.messages(phone_config(), since_minutes=30)

    command = calls[0][-1]
    assert "--where" in command
    assert "date>" in command


def test_the_device_command_is_one_string_for_the_phones_shell(monkeypatch):
    """Split across argv it arrives on the phone as separate words and the
    provider query fails."""
    calls = fake_adb(monkeypatch, "")

    adb.messages(phone_config())

    assert calls[0][1] == "shell"
    assert len(calls[0]) == 3


def test_a_configured_serial_is_passed_through(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.messages(phone_config(device_serial="ABC123"))
    assert calls[0][:3] == ["adb", "-s", "ABC123"]


# ---------------------------------------------------------------- one-time codes
def test_a_verification_message_yields_its_code(monkeypatch):
    fake_adb(monkeypatch, f"Row: 0 address=VM-HDFC, body=123456 is your OTP for "
                          f"the transaction, date={ms(1)}\n")

    code, message = adb.latest_code(phone_config())

    assert code == "123456"
    assert message.sender == "VM-HDFC"


def test_a_message_that_says_it_is_a_code_beats_a_bare_number(monkeypatch):
    """The first number seen is very often an order id or an amount."""
    fake_adb(monkeypatch, (
        f"Row: 0 address=SHOP, body=Order 887711 is confirmed, date={ms(1)}\n"
        f"Row: 1 address=BANK, body=Your verification code is 4321, date={ms(2)}\n"
    ))

    code, _message = adb.latest_code(phone_config())

    assert code == "4321"


def test_a_bare_number_is_used_when_nothing_better_exists(monkeypatch):
    fake_adb(monkeypatch, f"Row: 0 address=SHOP, body=Reference 5566, date={ms(1)}\n")
    assert adb.latest_code(phone_config())[0] == "5566"


def test_a_long_number_is_not_mistaken_for_a_code(monkeypatch):
    fake_adb(monkeypatch,
             f"Row: 0 address=SHOP, body=Call 918000123456 to track, date={ms(1)}\n")
    assert adb.latest_code(phone_config()) is None


def test_no_recent_message_means_no_code(monkeypatch):
    fake_adb(monkeypatch, "")
    assert adb.latest_code(phone_config()) is None


# ------------------------------------------------------------------- devices
def test_attached_devices_are_listed(monkeypatch):
    fake_adb(monkeypatch, "List of devices attached\nABC123\tdevice\nDEF456\tunauthorized\n")

    assert adb.devices(phone_config()) == [("ABC123", "device"), ("DEF456", "unauthorized")]


def test_health_reports_a_ready_device(monkeypatch):
    fake_adb(monkeypatch, "List of devices attached\nABC123\tdevice\n")
    assert adb.health(phone_config()).startswith("ok")


def test_health_reports_an_unauthorised_device_distinctly(monkeypatch):
    fake_adb(monkeypatch, "List of devices attached\nABC123\tunauthorized\n")
    assert "not ready" in adb.health(phone_config())


def test_health_reports_nothing_attached(monkeypatch):
    fake_adb(monkeypatch, "List of devices attached\n")
    assert adb.health(phone_config()) == "no device attached"


def test_health_reports_adb_missing_without_raising(monkeypatch):
    monkeypatch.setattr(adb, "available", lambda cfg: False)
    assert adb.health(phone_config()) == "adb not installed"


def test_health_never_raises(monkeypatch):
    monkeypatch.setattr(adb, "available", lambda cfg: True)

    def boom(*a, **k):
        raise OSError("usb went away")

    monkeypatch.setattr(subprocess, "run", boom)

    assert adb.health(phone_config()).startswith("failed")


# ---------------------------------------------------------------- failures
def test_an_unauthorised_phone_says_to_accept_the_prompt(monkeypatch):
    fake_adb(monkeypatch, output="", returncode=1, stderr="error: device unauthorized")

    with pytest.raises(IntegrationError) as caught:
        adb.messages(phone_config())
    assert "Allow USB debugging" in caught.value.user_action


def test_no_device_says_to_connect_one(monkeypatch):
    fake_adb(monkeypatch, output="", returncode=1, stderr="error: no devices/emulators found")

    with pytest.raises(IntegrationError) as caught:
        adb.messages(phone_config())
    assert "Connect the phone" in caught.value.user_action


def test_several_devices_says_to_pick_one(monkeypatch):
    fake_adb(monkeypatch, output="", returncode=1,
             stderr="error: more than one device/emulator")

    with pytest.raises(IntegrationError) as caught:
        adb.messages(phone_config())
    assert "device_serial" in caught.value.user_action


def test_adb_missing_says_where_to_get_it(monkeypatch):
    monkeypatch.setattr(adb, "available", lambda cfg: False)

    with pytest.raises(IntegrationError) as caught:
        adb.messages(phone_config())
    assert "Platform Tools" in caught.value.user_action


def test_a_timeout_is_recoverable(monkeypatch):
    monkeypatch.setattr(adb, "available", lambda cfg: True)

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="adb", timeout=5)

    monkeypatch.setattr(subprocess, "run", timeout)

    with pytest.raises(IntegrationError) as caught:
        adb.messages(phone_config())
    assert caught.value.recoverable is True


# ------------------------------------------------------------------ battery
def test_battery_level_is_read_from_dumpsys(monkeypatch):
    fake_adb(monkeypatch, "Current Battery Service state:\n  level: 87\n  status: 2\n")

    assert adb.battery(phone_config()) == "Phone battery is at 87% (charging)."


def test_a_discharging_phone_is_not_called_charging(monkeypatch):
    fake_adb(monkeypatch, "  level: 41\n  status: 3\n")
    assert adb.battery(phone_config()) == "Phone battery is at 41%."


def test_no_battery_line_is_reported_honestly(monkeypatch):
    fake_adb(monkeypatch, "nothing useful here\n")
    assert "did not report" in adb.battery(phone_config())


# -------------------------------------------------------------------- tools
def test_the_code_is_read_out_digit_by_digit(container, monkeypatch):
    """"123456" read as a number is "one hundred and twenty-three thousand..."."""
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401

    message = adb.Sms(sender="VM-HDFC", body="123456 is your OTP", when=datetime.now())
    monkeypatch.setattr(adb, "latest_code", lambda cfg: ("123456", message))

    result = registry.get_record("latest_code").raw_fn()

    assert "1 2 3 4 5 6" in result
    assert "VM-HDFC" in result


def test_the_sms_tool_reports_a_disconnected_phone_speakably(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401

    def boom(*a, **k):
        raise IntegrationError(
            "no device", service="phone", user_action="Connect the phone by USB."
        )

    monkeypatch.setattr(adb, "messages", boom)

    result = registry.get_record("read_sms").raw_fn()

    assert "Connect the phone by USB" in result
