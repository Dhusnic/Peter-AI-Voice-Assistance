"""Reading the phone over ADB.

Almost all the risk is in parsing `content query` output, which is a format
with no escaping: a message body can contain the same ", " that separates
fields. These tests use real-shaped rows, including the awkward ones.
"""

import shlex
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
        pull_dirs=["/sdcard/Pictures/Screenshots", "/sdcard/DCIM/Screenshots"],
        spotify_package="com.spotify.music",
        wireless_address="",
        raw_shell_enabled=False, push_default_dir="/sdcard/Download",
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


def test_a_body_containing_a_literal_newline_is_not_truncated(monkeypatch):
    """Found on a real device: bank SMS routinely embed a raw newline in the
    body ("Sent Rs.60.00\\nFrom HDFC Bank A/C...\\nRef 123..."). Splitting
    the whole `content query` output on every '\\n' — as if a newline always
    meant "next row" — broke one logical row into several that no longer
    matched `_ROW`, truncating the body *and* silently dropping the `date`
    field that came after it, which is why every affected message read back
    as 1 Jan 1970."""
    body = "Sent Rs.60.00\nFrom HDFC Bank A/C *3371\nRef 201234311186\nNot You? Call 18002586161"
    when_ms = ms(5)
    raw = (
        f"Row: 0 address=VM-HDFCBK-T, body={body}, date={when_ms}\n"
        f"Row: 1 address=OTHER, body=next message, date={ms(1)}\n"
    )
    fake_adb(monkeypatch, raw)

    found = adb.messages(phone_config())

    assert len(found) == 2
    bank_message = next(m for m in found if m.sender == "VM-HDFCBK-T")
    assert bank_message.body == body
    assert bank_message.when is not None
    assert bank_message.when.year > 1970


def test_rows_splits_only_on_a_real_row_boundary(monkeypatch):
    """Direct unit test of the splitting helper itself: a newline is only a
    row boundary when followed by the next "Row: N " marker."""
    raw = "Row: 0 a=1\nembedded\nnewline, b=2\nRow: 1 a=3, b=4\n"
    assert adb._rows(raw) == ["Row: 0 a=1\nembedded\nnewline, b=2", "Row: 1 a=3, b=4"]


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


# ------------------------------------------------------ wireless auto-reconnect
def test_looks_disconnected_matches_a_missing_device():
    assert adb._looks_disconnected("error: no devices/emulators found")
    assert adb._looks_disconnected("error: device offline")


def test_looks_disconnected_does_not_match_unauthorized():
    """The device is attached, just waiting on the USB-debugging prompt —
    reconnecting cannot fix that, so it must not trigger a retry."""
    assert not adb._looks_disconnected("error: device unauthorized")


def test_connect_wireless_does_nothing_with_no_address_configured(monkeypatch):
    """No subprocess call at all when wireless_address is empty — the USB-only
    path must not pay any cost for a feature it doesn't use."""
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(1))

    assert adb._connect_wireless(phone_config(wireless_address="")) is False
    assert calls == []


def test_connect_wireless_reports_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda args, **k: SimpleNamespace(
        returncode=0, stdout="connected to 192.168.1.5:5555", stderr=""))

    assert adb._connect_wireless(phone_config(wireless_address="192.168.1.5:5555")) is True


def test_connect_wireless_reports_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda args, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="failed to connect to 192.168.1.5:5555"))

    assert adb._connect_wireless(phone_config(wireless_address="192.168.1.5:5555")) is False


def test_connect_wireless_survives_a_timeout(monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="adb", timeout=10)

    monkeypatch.setattr(subprocess, "run", timeout)

    assert adb._connect_wireless(phone_config(wireless_address="192.168.1.5:5555")) is False


def test_run_retries_once_through_reconnect_and_succeeds(monkeypatch):
    """The actual point of the feature: a dropped session self-heals on the
    next command instead of failing until someone runs `adb connect` by
    hand."""
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["adb", "connect"]:
            return SimpleNamespace(returncode=0, stdout="connected to X", stderr="")
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr="error: no devices/emulators found")
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(adb, "available", lambda cfg: True)

    result = adb._run(["shell", "echo hi"], phone_config(wireless_address="192.168.1.5:5555"))

    assert result == "ok\n"
    assert calls[1][:2] == ["adb", "connect"]  # the reconnect actually happened


def test_run_gives_up_when_reconnect_also_fails(monkeypatch):
    def run(args, **kwargs):
        if args[:2] == ["adb", "connect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="failed to connect")
        return SimpleNamespace(returncode=1, stdout="", stderr="error: no devices/emulators found")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(adb, "available", lambda cfg: True)

    with pytest.raises(IntegrationError):
        adb._run(["shell", "echo hi"], phone_config(wireless_address="192.168.1.5:5555"))


def test_run_does_not_attempt_reconnect_without_a_configured_address(monkeypatch):
    """Regression guard: USB-only setups (the default) must behave exactly as
    before — one failed call, one error, no second subprocess call."""
    calls = fake_adb(monkeypatch, output="", returncode=1,
                      stderr="error: no devices/emulators found")

    with pytest.raises(IntegrationError):
        adb._run(["shell", "echo hi"], phone_config(wireless_address=""))

    assert len(calls) == 1  # no reconnect attempt made


def test_run_does_not_retry_a_non_disconnection_failure(monkeypatch):
    """An unauthorized device, or any other real failure, must not trigger a
    pointless reconnect attempt."""
    calls = fake_adb(monkeypatch, output="", returncode=1,
                      stderr="error: device unauthorized")

    with pytest.raises(IntegrationError):
        adb._run(["shell", "echo hi"], phone_config(wireless_address="192.168.1.5:5555"))

    assert len(calls) == 1


def test_screenshot_bytes_retries_through_reconnect(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["adb", "connect"]:
            return SimpleNamespace(returncode=0, stdout="connected to X", stderr="")
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout=b"",
                                    stderr=b"error: no devices/emulators found")
        return SimpleNamespace(returncode=0, stdout=b"\x89PNGdata", stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(adb, "available", lambda cfg: True)

    result = adb.screenshot_bytes(phone_config(wireless_address="192.168.1.5:5555"))

    assert result == b"\x89PNGdata"


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
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    message = adb.Sms(sender="VM-HDFC", body="123456 is your OTP", when=datetime.now())
    monkeypatch.setattr(adb, "latest_code", lambda cfg: ("123456", message))

    result = registry.get_record("latest_code").raw_fn()

    assert "1 2 3 4 5 6" in result
    assert "VM-HDFC" in result


def test_the_sms_tool_reports_a_disconnected_phone_speakably(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    def boom(*a, **k):
        raise IntegrationError(
            "no device", service="phone", user_action="Connect the phone by USB."
        )

    monkeypatch.setattr(adb, "messages", boom)

    result = registry.get_record("read_sms").raw_fn()

    assert "Connect the phone by USB" in result


# --------------------------------------------------------- adb_path resolution
# adb_path can be unzipped straight into the repo rather than added to PATH —
# a real setup this project actually uses. A relative path there must not
# silently depend on whatever directory Peter happens to be launched from.
def test_a_bare_command_name_is_left_alone_for_path_lookup():
    """"adb" (no directory component) means "find it on PATH" — resolving it
    against the project root would instead look for a literal file named
    "adb" sitting at the repo root, which is not what an empty path means."""
    resolved = adb._resolved_adb_path(phone_config(adb_path="adb"))
    assert resolved == "adb"


def test_a_relative_path_is_anchored_to_the_project_root_not_the_cwd():
    from peter.core.config import PROJECT_ROOT

    resolved = adb._resolved_adb_path(
        phone_config(adb_path="./mobile dev/platform-tools/adb.exe")
    )

    assert resolved == str((PROJECT_ROOT / "mobile dev/platform-tools/adb.exe").resolve())
    assert resolved.startswith(str(PROJECT_ROOT))


def test_an_absolute_path_is_passed_through_untouched():
    absolute = "C:/platform-tools/adb.exe"
    assert adb._resolved_adb_path(phone_config(adb_path=absolute)) == absolute


def test_the_resolved_path_is_what_actually_gets_run(monkeypatch):
    """The resolution has to reach the real subprocess call, not just exist
    as a helper nothing uses."""
    calls = fake_adb(monkeypatch, "")

    adb.messages(phone_config(adb_path="./mobile dev/platform-tools/adb.exe"))

    from peter.core.config import PROJECT_ROOT

    assert calls[0][0] == str(
        (PROJECT_ROOT / "mobile dev/platform-tools/adb.exe").resolve()
    )


def test_a_path_with_a_space_in_the_directory_name_resolves_correctly():
    """The actual folder this points at in practice — "mobile dev" — has a
    space in it; make sure that survives resolution intact."""
    resolved = adb._resolved_adb_path(
        phone_config(adb_path="./mobile dev/platform-tools/adb.exe")
    )
    assert "mobile dev" in resolved


# --------------------------------------------------------------- call log
@pytest.fixture(autouse=True)
def _reset_contacts_cache(monkeypatch):
    """The contacts cache is module-level and keyed on (adb_path, serial),
    which most tests share — without this, one test's fake ADB output leaks
    into the next test's contact lookups."""
    monkeypatch.setattr(adb, "_contacts_cached", {})


def test_call_log_parses_type_name_and_duration(monkeypatch):
    fake_adb(monkeypatch, (
        f"Row: 0 number=+919000000000, name=NULL, type=3, date={ms(5)}, duration=0\n"
        f"Row: 1 number=+919111111111, name=Office, type=2, date={ms(1)}, duration=125\n"
    ))

    found = adb.calls(phone_config())

    assert found[0].name == "Office"
    assert found[0].call_type == "outgoing"
    assert found[0].duration_seconds == 125
    assert found[1].call_type == "missed"
    assert found[1].name is None
    assert "Missed call" in found[1].spoken()


def test_a_null_call_log_column_is_treated_as_missing(monkeypatch):
    fake_adb(monkeypatch, f"Row: 0 number=NULL, name=NULL, type=1, date={ms(1)}, duration=10\n")
    found = adb.calls(phone_config())
    assert found[0].number == "unknown"
    assert found[0].name is None


def test_an_unrecognised_call_type_is_reported_generically(monkeypatch):
    fake_adb(monkeypatch, f"Row: 0 number=+9190, name=NULL, type=99, date={ms(1)}, duration=5\n")
    assert adb.calls(phone_config())[0].call_type == "call"


def test_calls_come_back_newest_first(monkeypatch):
    fake_adb(monkeypatch, (
        f"Row: 0 number=A, name=NULL, type=1, date={ms(60)}, duration=1\n"
        f"Row: 1 number=B, name=NULL, type=1, date={ms(1)}, duration=1\n"
    ))
    assert [c.number for c in adb.calls(phone_config())] == ["B", "A"]


# -------------------------------------------------------- contact resolution
def _routed_adb(monkeypatch, routes):
    """Like fake_adb, but the canned response depends on the device command,
    since a test needs the SMS/call-log query and the contacts query to
    return different rows in the same run."""
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        command = args[-1]
        for needle, output in routes:
            if needle in command:
                return SimpleNamespace(returncode=0, stdout=output, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(adb, "available", lambda cfg: True)
    return calls


def test_sms_sender_is_labelled_with_a_contact_name(monkeypatch):
    _routed_adb(monkeypatch, [
        ("content://sms", f"Row: 0 address=+919000000000, body=hi, date={ms(1)}\n"),
        ("content://com.android.contacts",
         "Row: 0 display_name=Mom, data1=+91 90000 00000\n"),
    ])

    found = adb.messages(phone_config())

    assert found[0].sender_name == "Mom"
    assert found[0].spoken().startswith("Mom (")


def test_a_sender_with_no_matching_contact_falls_back_to_the_raw_address(monkeypatch):
    _routed_adb(monkeypatch, [
        ("content://sms", f"Row: 0 address=AX-BLINKIT, body=hi, date={ms(1)}\n"),
        ("content://com.android.contacts", "Row: 0 display_name=Mom, data1=+919000000000\n"),
    ])

    found = adb.messages(phone_config())

    assert found[0].sender_name is None
    assert found[0].spoken().startswith("AX-BLINKIT (")


def test_call_log_falls_back_to_contacts_when_the_device_gives_no_name(monkeypatch):
    _routed_adb(monkeypatch, [
        ("content://call_log", f"Row: 0 number=+919000000000, name=NULL, type=1, "
                               f"date={ms(1)}, duration=30\n"),
        ("content://com.android.contacts", "Row: 0 display_name=Mom, data1=9000000000\n"),
    ])

    assert adb.calls(phone_config())[0].name == "Mom"


def test_a_broken_contacts_read_does_not_break_sms_reading(monkeypatch):
    """Best-effort: contacts access can be blocked while SMS still works."""
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "content://sms" in args[-1]:
            return SimpleNamespace(
                returncode=0, stdout=f"Row: 0 address=A, body=hi, date={ms(1)}\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="error: permission denied")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(adb, "available", lambda cfg: True)

    found = adb.messages(phone_config())  # must not raise

    assert found[0].body == "hi"
    assert found[0].sender_name is None


# --------------------------------------------------- calling a contact by name
def test_find_contact_matches_case_insensitively_and_keeps_the_raw_number(monkeypatch):
    _routed_adb(monkeypatch, [
        ("content://com.android.contacts",
         "Row: 0 display_name=Ancy Mom, data1=+91 90000 00000\n"),
    ])

    found = adb.find_contact(phone_config(), "ancy mom")

    assert found == [("Ancy Mom", "+91 90000 00000")]


def test_find_contact_returns_every_number_for_a_two_number_contact(monkeypatch):
    _routed_adb(monkeypatch, [
        ("content://com.android.contacts",
         "Row: 0 display_name=Ancy Mom, data1=+91 90000 00000\n"
         "Row: 1 display_name=Ancy Mom, data1=044 2345 6789\n"),
    ])

    found = adb.find_contact(phone_config(), "Ancy Mom")

    assert len(found) == 2
    assert {number for _name, number in found} == {"+91 90000 00000", "044 2345 6789"}


def test_find_contact_matches_on_a_partial_name(monkeypatch):
    _routed_adb(monkeypatch, [
        ("content://com.android.contacts", "Row: 0 display_name=Ancy Mom, data1=9000000000\n"),
    ])
    assert adb.find_contact(phone_config(), "ancy") == [("Ancy Mom", "9000000000")]


def test_find_contact_falls_back_to_word_matching_when_the_phrase_is_not_a_substring(monkeypatch):
    """The real-world case that motivated the fallback: a contact saved as
    bare "Ancy", called "Ancy Mom" out loud on a live phone. "Ancy Mom" is
    not a substring of "Ancy", but the two share the word "ancy"."""
    _routed_adb(monkeypatch, [
        ("content://com.android.contacts", "Row: 0 display_name=Ancy, data1=+919976197907\n"),
    ])

    found = adb.find_contact(phone_config(), "Ancy Mom")

    assert ("Ancy", "+919976197907") in found


def test_find_contact_prefers_an_exact_phrase_match_over_the_word_fallback(monkeypatch):
    """When the phrase does match directly, don't also drag in every other
    contact that merely shares one word with it."""
    _routed_adb(monkeypatch, [
        ("content://com.android.contacts",
         "Row: 0 display_name=Ancy Mom, data1=1111111111\n"
         "Row: 1 display_name=Ancy Other, data1=2222222222\n"),
    ])

    found = adb.find_contact(phone_config(), "Ancy Mom")

    assert found == [("Ancy Mom", "1111111111")]


def test_find_contact_returns_nothing_for_no_match(monkeypatch):
    _routed_adb(monkeypatch, [
        ("content://com.android.contacts", "Row: 0 display_name=Ancy Mom, data1=9000000000\n"),
    ])
    assert adb.find_contact(phone_config(), "nobody") == []


def test_find_contact_treats_empty_text_as_no_search(monkeypatch):
    _routed_adb(monkeypatch, [
        ("content://com.android.contacts", "Row: 0 display_name=Ancy Mom, data1=9000000000\n"),
    ])
    assert adb.find_contact(phone_config(), "   ") == []


def test_the_where_clause_query_stays_the_first_adb_call(monkeypatch):
    """Regression guard: contacts resolution is a second call made *after* the
    main query, so existing assertions elsewhere about "the first call" keep
    inspecting the right command."""
    calls_seen = fake_adb(monkeypatch, "")
    adb.messages(phone_config())
    assert "content://sms" in calls_seen[0][-1]


# ------------------------------------------------------------ phone screen
def test_screenshot_bytes_returns_the_raw_png(monkeypatch):
    monkeypatch.setattr(adb, "available", lambda cfg: True)
    monkeypatch.setattr(subprocess, "run", lambda args, **k: SimpleNamespace(
        returncode=0, stdout=b"\x89PNGrestofimage", stderr=b""))

    assert adb.screenshot_bytes(phone_config()) == b"\x89PNGrestofimage"


def test_screenshot_bytes_uses_exec_out_not_shell(monkeypatch):
    monkeypatch.setattr(adb, "available", lambda cfg: True)
    seen = {}

    def run(args, **k):
        seen["args"] = args
        return SimpleNamespace(returncode=0, stdout=b"data", stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    adb.screenshot_bytes(phone_config())

    assert seen["args"][1:] == ["exec-out", "screencap", "-p"]


def test_screenshot_bytes_raises_on_an_empty_capture(monkeypatch):
    monkeypatch.setattr(adb, "available", lambda cfg: True)
    monkeypatch.setattr(subprocess, "run", lambda args, **k: SimpleNamespace(
        returncode=0, stdout=b"", stderr=b"no display attached"))

    with pytest.raises(IntegrationError):
        adb.screenshot_bytes(phone_config())


def test_screenshot_bytes_says_adb_is_missing(monkeypatch):
    monkeypatch.setattr(adb, "available", lambda cfg: False)
    with pytest.raises(IntegrationError) as caught:
        adb.screenshot_bytes(phone_config())
    assert "Platform Tools" in caught.value.user_action


# --------------------------------------------------------- opening a link
def test_open_url_rejects_a_non_http_scheme(monkeypatch):
    monkeypatch.setattr(adb, "available", lambda cfg: True)
    with pytest.raises(IntegrationError):
        adb.open_url(phone_config(), "intent://evil#Intent;end")


def test_open_url_sends_a_view_intent(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.open_url(phone_config(), "https://example.com/checkout?x=1")

    command = calls[0][-1]
    assert "am start" in command
    assert "android.intent.action.VIEW" in command
    assert "https://example.com/checkout?x=1" in command


def test_open_url_survives_shell_metacharacters(monkeypatch):
    """A URL containing `$(...)` or a backtick must not let the phone's shell
    execute anything. This is the actual fix for a real bug: the original
    implementation escaped embedded double quotes but still wrapped the URL
    in a double-quoted string, which does not stop a POSIX shell from
    expanding `$(...)` or backticks inside it — a crafted URL could run
    arbitrary commands on the phone."""
    calls = fake_adb(monkeypatch, "")
    url = 'https://example.com/"x$(reboot)`id`'

    adb.open_url(phone_config(), url)

    command = calls[0][-1]
    assert shlex.split(command)[-1] == url


# -------------------------------------------------------- pulling a file
def test_pull_latest_file_skips_an_empty_directory(monkeypatch, tmp_path):
    def run(args, **k):
        if args[-2:] == ["shell", "ls -t /sdcard/empty 2>/dev/null"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="No such file")
        if args[-2:] == ["shell", "ls -t /sdcard/real 2>/dev/null"]:
            return SimpleNamespace(returncode=0, stdout="shot.png\nolder.png\n", stderr="")
        if len(args) >= 2 and args[1] == "pull":
            return SimpleNamespace(returncode=0, stdout="1 file pulled", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected call")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(adb, "available", lambda cfg: True)

    local = adb.pull_latest_file(phone_config(), ["/sdcard/empty", "/sdcard/real"], tmp_path)

    assert local == tmp_path / "shot.png"


def test_pull_latest_file_raises_when_nothing_is_found(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda args, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="No such file"))
    monkeypatch.setattr(adb, "available", lambda cfg: True)

    with pytest.raises(IntegrationError) as caught:
        adb.pull_latest_file(phone_config(), ["/sdcard/a", "/sdcard/b"], tmp_path)
    assert caught.value.user_action


def test_pull_latest_file_says_adb_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(adb, "available", lambda cfg: False)
    with pytest.raises(IntegrationError) as caught:
        adb.pull_latest_file(phone_config(), ["/sdcard/a"], tmp_path)
    assert "Platform Tools" in caught.value.user_action


# --------------------------------------------------------- placing calls
def test_call_number_rejects_anything_that_is_not_a_phone_number(monkeypatch):
    monkeypatch.setattr(adb, "available", lambda cfg: True)
    with pytest.raises(IntegrationError):
        adb.call_number(phone_config(), "9000; reboot")


def test_call_number_dials_via_the_call_intent(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.call_number(phone_config(), "+91 90000 00000")

    command = calls[0][-1]
    assert "android.intent.action.CALL" in command
    assert shlex.split(command)[-1] == "tel:+91 90000 00000"


def test_call_number_strips_surrounding_whitespace(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.call_number(phone_config(), "  9000000000  ")
    assert shlex.split(calls[0][-1])[-1] == "tel:9000000000"


def test_answer_call_sends_the_call_keyevent(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.answer_call(phone_config())
    assert calls[0][-1] == "input keyevent KEYCODE_CALL"


def test_hang_up_call_sends_the_endcall_keyevent(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.hang_up_call(phone_config())
    assert calls[0][-1] == "input keyevent KEYCODE_ENDCALL"


# ------------------------------------------------------------------- media
def test_launch_spotify_with_no_query_opens_the_app_and_plays(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.launch_spotify(phone_config())

    assert "monkey -p com.spotify.music" in calls[0][-1]
    assert calls[1][-1] == "input keyevent KEYCODE_MEDIA_PLAY"


def test_launch_spotify_with_a_query_opens_a_search(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.launch_spotify(phone_config(), query="lofi beats")

    command = calls[0][-1]
    assert "android.intent.action.VIEW" in command
    assert "spotify:search:lofi%20beats" in command
    assert len(calls) == 1  # no extra media-play key when opening a search


def test_pause_media_sends_the_pause_keyevent(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.pause_media(phone_config())
    assert calls[0][-1] == "input keyevent KEYCODE_MEDIA_PAUSE"


def test_next_track_sends_the_media_next_keyevent(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.next_track(phone_config())
    assert calls[0][-1] == "input keyevent KEYCODE_MEDIA_NEXT"


# ------------------------------------------------------------------ alarms
def test_set_alarm_on_phone_sends_hour_and_minute(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.set_alarm_on_phone(phone_config(), 7, 30)

    command = calls[0][-1]
    assert "android.intent.action.SET_ALARM" in command
    assert "--ei android.intent.extra.alarm.HOUR 7" in command
    assert "--ei android.intent.extra.alarm.MINUTES 30" in command
    assert "SKIP_UI true" in command


def test_set_alarm_on_phone_rejects_an_out_of_range_time(monkeypatch):
    monkeypatch.setattr(adb, "available", lambda cfg: True)
    with pytest.raises(IntegrationError):
        adb.set_alarm_on_phone(phone_config(), 24, 0)
    with pytest.raises(IntegrationError):
        adb.set_alarm_on_phone(phone_config(), 7, 60)


def test_set_alarm_on_phone_includes_a_label_when_given(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.set_alarm_on_phone(phone_config(), 6, 0, label="leave for the station")

    command = calls[0][-1]
    assert "android.intent.extra.alarm.MESSAGE" in command
    assert "leave for the station" in command


def test_set_alarm_on_phone_omits_message_extra_with_no_label(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.set_alarm_on_phone(phone_config(), 6, 0)
    assert "alarm.MESSAGE" not in calls[0][-1]


def test_set_alarm_on_phone_translates_iso_weekdays_to_android(monkeypatch):
    """ISO Monday=1 must become Calendar.DAY_OF_WEEK's Monday=2 — Sunday=1
    there is the classic off-by-one this mapping exists to avoid."""
    calls = fake_adb(monkeypatch, "")
    adb.set_alarm_on_phone(phone_config(), 6, 0, days="1,7")  # Mon, Sun

    command = calls[0][-1]
    assert "--eia android.intent.extra.alarm.DAYS 2,1" in command


def test_set_alarm_on_phone_omits_days_extra_for_a_one_off(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.set_alarm_on_phone(phone_config(), 6, 0)
    assert "alarm.DAYS" not in calls[0][-1]


def test_dismiss_phone_alarm_sends_the_dismiss_intent(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.dismiss_phone_alarm(phone_config())
    assert "android.intent.action.DISMISS_ALARM" in calls[0][-1]


def test_dismiss_phone_alarm_raises_when_no_clock_app_handles_it(monkeypatch):
    fake_adb(monkeypatch, output="Error: Activity not started, unable to "
                                 "resolve Intent", returncode=1)
    with pytest.raises(IntegrationError):
        adb.dismiss_phone_alarm(phone_config())


# ------------------------------------------------------------------ tools
def test_make_phone_call_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "call_number", lambda cfg, number: None)

    result = registry.get_record("make_phone_call").raw_fn(number="9000000000")

    assert "9000000000" in result


def test_make_phone_call_tool_reports_a_bad_number_speakably(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    def boom(cfg, number):
        raise IntegrationError(f"{number!r} does not look like a phone number",
                               service="phone")

    monkeypatch.setattr(adb, "call_number", boom)

    result = registry.get_record("make_phone_call").raw_fn(number="not a number")

    assert "Something went wrong" in result


def test_make_phone_call_is_not_confirm_exempt(container):
    """Regression guard for the actual reason call_contact exists: a raw,
    possibly-mis-transcribed number must keep needing confirmation. If this
    ever starts passing with make_phone_call missing from standing_rules,
    someone relaxed the wrong tool."""
    assert container.config.policy.standing_rules.get("make_phone_call") == "confirm"


def test_call_contact_tool_resolves_a_single_matching_contact(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "find_contact", lambda cfg, name: [("Ancy", "9976197907")])
    dialled = {}
    monkeypatch.setattr(adb, "call_number", lambda cfg, number: dialled.setdefault("number", number))

    result = registry.get_record("call_contact").raw_fn(contact_name="Ancy Mom")

    assert dialled["number"] == "9976197907"
    assert "Ancy" in result


def test_call_contact_tool_is_not_in_the_confirm_standing_rules(container):
    """The whole point: an unambiguous, name-resolved call does not need the
    on-device-equivalent confirmation step make_phone_call requires."""
    assert container.config.policy.standing_rules.get("call_contact") != "confirm"


def test_call_contact_tool_asks_which_contact_when_ambiguous(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "find_contact", lambda cfg, name: [
        ("Ancy Mom", "9000000000"), ("Ancy Mom", "0442345678"),
    ])
    called = []
    monkeypatch.setattr(adb, "call_number", lambda cfg, number: called.append(number))

    result = registry.get_record("call_contact").raw_fn(contact_name="Ancy Mom")

    assert called == []  # nothing dialled while ambiguous
    assert "2 contacts" in result
    assert "9000000000" in result and "0442345678" in result


def test_call_contact_tool_reports_no_matching_contact(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "find_contact", lambda cfg, name: [])

    result = registry.get_record("call_contact").raw_fn(contact_name="Nobody")

    assert "No saved contact" in result


def test_call_contact_tool_requires_a_name(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    result = registry.get_record("call_contact").raw_fn(contact_name="")

    assert "Give a contact name" in result


def test_call_contact_tool_reports_a_lookup_failure_speakably(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    def boom(cfg, name):
        raise IntegrationError("adb is not installed", service="phone",
                               user_action="Install Android Platform Tools.")

    monkeypatch.setattr(adb, "find_contact", boom)

    result = registry.get_record("call_contact").raw_fn(contact_name="Ancy")

    assert "Install Android Platform Tools" in result


def test_answer_phone_call_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "answer_call", lambda cfg: None)

    assert "Answered" in registry.get_record("answer_phone_call").raw_fn()


def test_hang_up_phone_call_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "hang_up_call", lambda cfg: None)

    assert "ended" in registry.get_record("hang_up_phone_call").raw_fn().lower()


def test_play_music_on_phone_tool_names_the_query(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "launch_spotify", lambda cfg, query: None)

    result = registry.get_record("play_music_on_phone").raw_fn(query="lofi beats")

    assert "lofi beats" in result


def test_pause_music_on_phone_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "pause_media", lambda cfg: None)

    assert "Paused" in registry.get_record("pause_music_on_phone").raw_fn()


def test_skip_track_on_phone_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "next_track", lambda cfg: None)

    assert "Skipped" in registry.get_record("skip_track_on_phone").raw_fn()


def test_set_phone_alarm_tool_reports_the_time_and_label(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(
        adb, "set_alarm_on_phone", lambda cfg, hour, minute, label, days: None
    )

    result = registry.get_record("set_phone_alarm").raw_fn(
        hour=6, minute=30, label="leave for the station"
    )

    assert "06:30" in result
    assert "leave for the station" in result


def test_stop_phone_alarm_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "dismiss_phone_alarm", lambda cfg: None)

    assert "dismissed" in registry.get_record("stop_phone_alarm").raw_fn().lower()


def test_read_call_log_tool_reports_calls(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    call = adb.Call(number="+91900", name="Mom", call_type="missed",
                     when=datetime.now(), duration_seconds=0)
    monkeypatch.setattr(adb, "calls", lambda cfg, since_minutes, limit: [call])

    result = registry.get_record("read_call_log").raw_fn()

    assert "Mom" in result


def test_open_link_on_phone_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "open_url", lambda cfg, url: None)

    result = registry.get_record("open_link_on_phone").raw_fn(url="https://example.com")

    assert "phone" in result.lower()


def test_open_link_on_phone_tool_reports_the_error_speakably(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    def boom(cfg, url):
        raise IntegrationError("bad url", service="phone",
                               user_action="Use an http(s) link.")

    monkeypatch.setattr(adb, "open_url", boom)

    result = registry.get_record("open_link_on_phone").raw_fn(url="intent://x")

    assert "http(s) link" in result


def test_save_phone_screenshot_tool_reports_the_saved_filename(container, monkeypatch, tmp_path):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(
        adb, "pull_latest_file", lambda cfg, dirs, local_dir: tmp_path / "shot.png"
    )

    result = registry.get_record("save_phone_screenshot").raw_fn()

    assert "shot.png" in result


def test_transcribe_phone_voice_note_tool_returns_the_transcript(container, monkeypatch, tmp_path):
    from peter.agent import registry
    import peter.meeting_notes as meeting_notes

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    audio_path = tmp_path / "note.opus"
    monkeypatch.setattr(
        adb, "pull_latest_file", lambda cfg, dirs, local_dir: audio_path
    )
    monkeypatch.setattr(meeting_notes, "transcribe", lambda path: "call me back")

    result = registry.get_record("transcribe_phone_voice_note").raw_fn()

    assert result == "call me back"


def test_transcribe_phone_voice_note_tool_reports_a_pull_failure_speakably(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    def boom(cfg, dirs, local_dir):
        raise IntegrationError(
            "no files found", service="phone",
            user_action="Record a voice note on the phone first.",
        )

    monkeypatch.setattr(adb, "pull_latest_file", boom)

    result = registry.get_record("transcribe_phone_voice_note").raw_fn()

    assert "Record a voice note on the phone first" in result


def test_transcribe_phone_voice_note_tool_reports_a_transcription_failure(
    container, monkeypatch, tmp_path
):
    from peter.agent import registry
    import peter.meeting_notes as meeting_notes

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    audio_path = tmp_path / "note.opus"
    monkeypatch.setattr(adb, "pull_latest_file", lambda cfg, dirs, local_dir: audio_path)

    def boom(path):
        raise RuntimeError("model failed to load")

    monkeypatch.setattr(meeting_notes, "transcribe", boom)

    result = registry.get_record("transcribe_phone_voice_note").raw_fn()

    assert "could not transcribe" in result
    assert "note.opus" in result


def test_transcribe_phone_voice_note_tool_reports_an_empty_transcript(
    container, monkeypatch, tmp_path
):
    from peter.agent import registry
    import peter.meeting_notes as meeting_notes

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    audio_path = tmp_path / "note.opus"
    monkeypatch.setattr(adb, "pull_latest_file", lambda cfg, dirs, local_dir: audio_path)
    monkeypatch.setattr(meeting_notes, "transcribe", lambda path: "   ")

    result = registry.get_record("transcribe_phone_voice_note").raw_fn()

    assert "transcription came back empty" in result


def test_read_phone_screen_tool_uses_the_vision_pipeline(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401
    from peter.llm import vision

    monkeypatch.setattr(adb, "screenshot_bytes", lambda cfg: b"\x89PNGdata")
    monkeypatch.setattr(vision, "describe_image", lambda path, question, config: "a lock screen")

    result = registry.get_record("read_phone_screen").raw_fn(question="what's on screen")

    assert result == "a lock screen"


def test_read_phone_screen_tool_reports_a_disconnected_phone_speakably(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    def boom(cfg):
        raise IntegrationError("no device", service="phone",
                               user_action="Connect the phone by USB.")

    monkeypatch.setattr(adb, "screenshot_bytes", boom)

    result = registry.get_record("read_phone_screen").raw_fn()

    assert "Connect the phone by USB" in result


# =================================================================
# Full-access expansion: app management, files, contacts, device
# settings, notifications, location, raw shell.
# =================================================================

# ------------------------------------------------------------ app management
def test_list_apps_strips_package_prefix(monkeypatch):
    fake_adb(monkeypatch, "package:com.spotify.music\npackage:com.example.app\n")
    assert adb.list_apps(phone_config()) == ["com.example.app", "com.spotify.music"]


def test_list_apps_third_party_only_by_default(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.list_apps(phone_config())
    assert "pm list packages -3" in calls[0][-1]


def test_list_apps_can_list_everything(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.list_apps(phone_config(), third_party_only=False)
    assert calls[0][-1].strip() == "pm list packages"


def test_launch_app_builds_a_monkey_launch_command(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.launch_app(phone_config(), "com.spotify.music")
    assert "monkey -p com.spotify.music -c android.intent.category.LAUNCHER 1" in calls[0][-1]


def test_launch_app_rejects_an_empty_package_id():
    with pytest.raises(IntegrationError):
        adb.launch_app(phone_config(), "   ")


def test_uninstall_app_builds_expected_command(monkeypatch):
    calls = fake_adb(monkeypatch, "Success\n")
    adb.uninstall_app(phone_config(), "com.example.app")
    assert "pm uninstall --user 0 com.example.app" in calls[0][-1]


def test_uninstall_app_keep_data_adds_the_k_flag(monkeypatch):
    calls = fake_adb(monkeypatch, "Success\n")
    adb.uninstall_app(phone_config(), "com.example.app", keep_data=True)
    assert "pm uninstall --user 0 -k com.example.app" in calls[0][-1]


def test_uninstall_app_rejects_an_empty_package_id():
    with pytest.raises(IntegrationError):
        adb.uninstall_app(phone_config(), "")


# --------------------------------------------------------------- file transfer
def test_push_file_rejects_a_missing_local_file(tmp_path):
    with pytest.raises(IntegrationError):
        adb.push_file(phone_config(), tmp_path / "nope.txt", "/sdcard/Download")


def test_push_file_builds_expected_argv_and_returns_remote_path(monkeypatch, tmp_path):
    local = tmp_path / "hello.txt"
    local.write_text("hi")
    calls = fake_adb(monkeypatch, "")

    remote = adb.push_file(phone_config(), local, "/sdcard/Download")

    assert calls[0][-3:] == ["push", str(local), "/sdcard/Download/hello.txt"]
    assert remote == "/sdcard/Download/hello.txt"


def test_push_file_raises_speakably_on_failure(monkeypatch, tmp_path):
    local = tmp_path / "hello.txt"
    local.write_text("hi")
    fake_adb(monkeypatch, "", returncode=1, stderr="remote object does not exist")
    with pytest.raises(IntegrationError):
        adb.push_file(phone_config(), local, "/sdcard/Nonexistent")


def test_list_remote_dir_returns_the_lines(monkeypatch):
    fake_adb(monkeypatch, "file1.txt\nfile2.txt\n")
    assert adb.list_remote_dir(phone_config(), "/sdcard/Download") == ["file1.txt", "file2.txt"]


def test_list_remote_dir_surfaces_a_real_error_unlike_list_remote(monkeypatch):
    """Distinct from the private `_list_remote`, which swallows errors."""
    fake_adb(monkeypatch, "", returncode=1, stderr="No such file or directory")
    with pytest.raises(IntegrationError):
        adb.list_remote_dir(phone_config(), "/sdcard/Nonexistent")


def test_delete_remote_file_quotes_a_path_with_a_space(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.delete_remote_file(phone_config(), "/sdcard/Download/a file.txt")
    assert "rm '/sdcard/Download/a file.txt'" in calls[0][-1]


def test_delete_remote_file_rejects_an_empty_path():
    with pytest.raises(IntegrationError):
        adb.delete_remote_file(phone_config(), "   ")


# ----------------------------------------------------------------- contacts
def test_list_contacts_with_no_query_returns_everything(monkeypatch):
    fake_adb(monkeypatch, (
        "Row: 0 display_name=Alice, data1=9000000000\n"
        "Row: 1 display_name=Bob, data1=9111111111\n"
    ))
    result = adb.list_contacts(phone_config())
    assert ("Alice", "9000000000") in result
    assert ("Bob", "9111111111") in result


def test_list_contacts_with_a_query_delegates_to_find_contact(monkeypatch):
    fake_adb(monkeypatch, "Row: 0 display_name=Alice, data1=9000000000\n")
    assert adb.list_contacts(phone_config(), query="ali") == [("Alice", "9000000000")]


def _contact_write_routes(insert_phone_output="\n", delete_output="\n", verify_row=None):
    return [
        ("content insert --uri content://com.android.contacts/raw_contacts", "\n"),
        ("content query --uri content://com.android.contacts/raw_contacts", "Row: 0 _id=42\n"),
        ("mimetype:s:vnd.android.cursor.item/name", "\n"),
        ("mimetype:s:vnd.android.cursor.item/phone_v2", insert_phone_output),
        ("content delete --uri content://com.android.contacts/raw_contacts/42", delete_output),
        ("content://com.android.contacts/data/phones",
         verify_row if verify_row is not None else "Row: 0 display_name=Alice, data1=9000000000\n"),
    ]


def test_add_contact_happy_path_does_not_raise(monkeypatch):
    _routed_adb(monkeypatch, _contact_write_routes())
    adb.add_contact(phone_config(), "Alice", "9000000000")


def test_add_contact_rejects_an_empty_name():
    with pytest.raises(IntegrationError):
        adb.add_contact(phone_config(), "   ", "9000000000")


def test_add_contact_rejects_an_empty_number():
    with pytest.raises(IntegrationError):
        adb.add_contact(phone_config(), "Alice", "   ")


def test_add_contact_raises_when_number_does_not_read_back_correctly(monkeypatch):
    _routed_adb(monkeypatch, _contact_write_routes(
        verify_row="Row: 0 display_name=Alice, data1=1111111111\n"
    ))
    with pytest.raises(IntegrationError) as excinfo:
        adb.add_contact(phone_config(), "Alice", "9000000000")
    assert "did not read back correctly" in str(excinfo.value)


def test_add_contact_cleans_up_on_partial_failure(monkeypatch):
    calls = _routed_adb(monkeypatch, _contact_write_routes(
        insert_phone_output="error: insert rejected"
    ))
    with pytest.raises(IntegrationError) as excinfo:
        adb.add_contact(phone_config(), "Alice", "9000000000")
    assert "the partial contact was removed" in str(excinfo.value)
    assert any("content delete" in c[-1] for c in calls)


def test_add_contact_reports_honestly_when_cleanup_also_fails(monkeypatch):
    _routed_adb(monkeypatch, _contact_write_routes(
        insert_phone_output="error: insert rejected",
        delete_output="error: delete failed",
    ))
    with pytest.raises(IntegrationError) as excinfo:
        adb.add_contact(phone_config(), "Alice", "9000000000")
    assert "check the phone's Contacts app" in str(excinfo.value)


def test_add_contact_invalidates_the_contacts_cache(monkeypatch):
    """A contact just added must be visible to find_contact/call_contact
    immediately, not after the 10-minute cache TTL."""
    adb._contacts_cached["adb|"] = (
        __import__("time").time(), {"9000000000": ("Someone Stale", "9000000000")}
    )
    _routed_adb(monkeypatch, _contact_write_routes())
    adb.add_contact(phone_config(), "Alice", "9000000000")
    # The cache was invalidated and re-primed by the verification read —
    # it now reflects the freshly queried contacts, not the stale entry.
    assert adb._contacts_cached["adb|"][1]["9000000000"] == ("Alice", "9000000000")


# ------------------------------------------------------------- device settings
def test_set_wifi_uses_cmd_not_the_deprecated_svc(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.set_wifi(phone_config(), True)
    assert "cmd -w wifi set-wifi-enabled enabled" in calls[0][-1]


def test_set_wifi_disabled(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.set_wifi(phone_config(), False)
    assert "cmd -w wifi set-wifi-enabled disabled" in calls[0][-1]


def test_set_bluetooth_reports_confirmed_state(monkeypatch):
    _routed_adb(monkeypatch, [
        ("svc bluetooth enable", "\n"),
        ("settings get global bluetooth_on", "1\n"),
    ])
    assert adb.set_bluetooth(phone_config(), True) is True


def test_set_bluetooth_reports_unconfirmed_state_honestly(monkeypatch):
    """The Android 12+ case: the command is issued, but the phone never
    actually changed state."""
    _routed_adb(monkeypatch, [
        ("svc bluetooth enable", "\n"),
        ("settings get global bluetooth_on", "0\n"),
    ])
    assert adb.set_bluetooth(phone_config(), True) is False


def test_set_airplane_mode_sends_setting_then_broadcast(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.set_airplane_mode(phone_config(), True)
    assert "settings put global airplane_mode_on 1" in calls[0][-1]
    assert "AIRPLANE_MODE" in calls[1][-1]
    assert "--ez state true" in calls[1][-1]


def _disconnected_after_setting(monkeypatch):
    def run(args, **kwargs):
        command = args[-1]
        if "airplane_mode_on" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="device offline")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(adb, "available", lambda cfg: True)


def test_set_airplane_mode_treats_disconnect_as_soft_success_over_wireless(monkeypatch):
    _disconnected_after_setting(monkeypatch)
    # Should not raise: enabling airplane mode, wireless configured, the
    # broadcast call looking disconnected is the expected shape of "it worked".
    adb.set_airplane_mode(phone_config(wireless_address="1.2.3.4:5555"), True)


def test_set_airplane_mode_still_raises_when_disabling(monkeypatch):
    _disconnected_after_setting(monkeypatch)
    with pytest.raises(IntegrationError):
        adb.set_airplane_mode(phone_config(wireless_address="1.2.3.4:5555"), False)


def test_set_airplane_mode_still_raises_with_no_wireless_address(monkeypatch):
    _disconnected_after_setting(monkeypatch)
    with pytest.raises(IntegrationError):
        adb.set_airplane_mode(phone_config(wireless_address=""), True)


def test_music_volume_range_parses_the_reported_range(monkeypatch):
    fake_adb(monkeypatch, "volume is 8 in range [0..15]\n")
    assert adb._music_volume_range(phone_config()) == (8, 15)


def test_music_volume_range_falls_back_on_an_unexpected_format(monkeypatch):
    fake_adb(monkeypatch, "something unexpected\n")
    assert adb._music_volume_range(phone_config()) == (0, 15)


def test_set_volume_percent_scales_to_the_devices_range(monkeypatch):
    def run(args, **kwargs):
        command = args[-1]
        if "--get" in command:
            return SimpleNamespace(returncode=0, stdout="volume is 5 in range [0..20]\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    calls = []

    def recording_run(args, **kwargs):
        calls.append(args)
        return run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_run)
    monkeypatch.setattr(adb, "available", lambda cfg: True)

    adb.set_volume_percent(phone_config(), 50)

    assert "--set 10" in calls[-1][-1]  # 50% of max 20


def test_set_volume_percent_rejects_out_of_range_values():
    with pytest.raises(IntegrationError):
        adb.set_volume_percent(phone_config(), 150)


def test_set_brightness_percent_forces_manual_mode_before_setting_the_level(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.set_brightness_percent(phone_config(), 50)
    assert "screen_brightness_mode 0" in calls[0][-1]
    assert "screen_brightness 128" in calls[1][-1]


def test_set_brightness_percent_rejects_out_of_range_values():
    with pytest.raises(IntegrationError):
        adb.set_brightness_percent(phone_config(), -1)


def test_reboot_uses_the_host_level_command_not_shell(monkeypatch):
    calls = fake_adb(monkeypatch, "")
    adb.reboot(phone_config())
    assert calls[0][-1] == "reboot"
    assert "shell" not in calls[0]


# --------------------------------------------------------------- notifications
def test_notifications_parses_multiple_records(monkeypatch):
    raw = (
        "NotificationRecord(key=0|com.whatsapp|0|null|10062|...)\n"
        "  pkg=com.whatsapp\n"
        "  android.title=String (John Doe)\n"
        "  android.text=String (Hey, are you free?)\n"
        "  postTime=1700000000000\n"
        "NotificationRecord(key=1|com.android.systemui|...)\n"
        "  pkg=com.android.systemui\n"
        "  android.title=String (Low battery)\n"
        "  android.text=String (15% remaining)\n"
        "  postTime=1700000001000\n"
    )
    fake_adb(monkeypatch, raw)
    found = adb.notifications(phone_config())
    assert {n.package for n in found} == {"com.whatsapp", "com.android.systemui"}
    assert any(n.title == "John Doe" and n.text == "Hey, are you free?" for n in found)


def test_notifications_skips_a_malformed_record(monkeypatch):
    raw = (
        "NotificationRecord(key=0|weird)\n"
        "  no pkg field here at all\n"
        "NotificationRecord(key=1|com.example.app)\n"
        "  pkg=com.example.app\n"
        "  android.title=String (Hello)\n"
        "  postTime=1700000000000\n"
    )
    fake_adb(monkeypatch, raw)
    found = adb.notifications(phone_config())
    assert len(found) == 1
    assert found[0].package == "com.example.app"


def test_notifications_empty_output_returns_no_records(monkeypatch):
    fake_adb(monkeypatch, "")
    assert adb.notifications(phone_config()) == []


def test_notification_spoken_form_includes_title_and_text():
    note = adb.Notification(package="com.whatsapp", title="Alice", text="hi", when=None)
    assert "Alice" in note.spoken() and "hi" in note.spoken()


# -------------------------------------------------------------------- location
def test_last_location_prefers_fused_over_other_providers(monkeypatch):
    raw = (
        "Last Known Locations:\n"
        "  gps: Location[gps 12.900000,77.590000 hAcc=10.0]\n"
        "  network: Location[network 12.950000,77.600000 hAcc=50.0]\n"
        "  fused: Location[fused 12.910000,77.595000 hAcc=15.0]\n"
    )
    fake_adb(monkeypatch, raw)
    location = adb.last_location(phone_config())
    assert location.provider == "fused"
    assert location.latitude == 12.91
    assert location.longitude == 77.595


def test_last_location_returns_none_when_nothing_is_cached(monkeypatch):
    fake_adb(monkeypatch, "Last Known Locations:\n")
    assert adb.last_location(phone_config()) is None


def test_location_spoken_form_reports_coordinates():
    location = adb.Location(provider="gps", latitude=12.9, longitude=77.6)
    spoken = location.spoken()
    assert "12.90000" in spoken and "77.60000" in spoken


# ------------------------------------------------------------------- raw shell
def test_run_shell_raises_not_configured_when_disabled():
    from peter.core.errors import NotConfiguredError

    with pytest.raises(NotConfiguredError):
        adb.run_shell(phone_config(raw_shell_enabled=False), "ls")


def test_run_shell_does_not_raise_on_a_nonzero_exit_code(monkeypatch):
    fake_adb(monkeypatch, output="", returncode=1, stderr="no matches")
    result = adb.run_shell(phone_config(raw_shell_enabled=True), "grep foo bar.txt")
    assert "[exit 1]" in result
    assert "no matches" in result


def test_run_shell_does_not_treat_the_word_error_as_a_failure(monkeypatch):
    fake_adb(monkeypatch, output="error: this is actually fine output\n", returncode=0)
    result = adb.run_shell(
        phone_config(raw_shell_enabled=True), "echo 'error: this is actually fine output'"
    )
    assert "[exit 0]" in result
    assert "error: this is actually fine output" in result


def test_run_shell_retries_once_on_a_disconnected_looking_failure(monkeypatch):
    responses = [
        SimpleNamespace(returncode=1, stdout="", stderr="device offline"),
        SimpleNamespace(returncode=0, stdout="connected to 1.2.3.4:5555", stderr=""),
        SimpleNamespace(returncode=0, stdout="ok output", stderr=""),
    ]
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return responses.pop(0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(adb, "available", lambda cfg: True)

    cfg = phone_config(raw_shell_enabled=True, wireless_address="1.2.3.4:5555")
    result = adb.run_shell(cfg, "echo hi")

    assert "ok output" in result
    assert len(calls) == 3


def test_run_shell_does_not_quote_the_command(monkeypatch):
    """The explicit, consented-to nature of this tool — see run_shell's
    docstring for why every other function in this module quotes free text
    and this one deliberately does not."""
    calls = fake_adb(monkeypatch, "")
    adb.run_shell(phone_config(raw_shell_enabled=True), "echo 'still has quotes'")
    assert calls[0][-1] == "echo 'still has quotes'"


# --------------------------------------------------------- PhoneConfig default
def test_phone_config_raw_shell_defaults_to_false():
    from peter.core.config import PhoneConfig

    assert PhoneConfig().raw_shell_enabled is False


# ================================================================= tool layer
# ------------------------------------------------------- app management tools
def test_list_phone_apps_tool_reports_packages(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "list_apps", lambda cfg, third_party_only=True: ["com.example.app"])
    assert "com.example.app" in registry.get_record("list_phone_apps").raw_fn()


def test_list_phone_apps_tool_reports_none_found(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "list_apps", lambda cfg, third_party_only=True: [])
    assert "No apps found" in registry.get_record("list_phone_apps").raw_fn()


def test_launch_phone_app_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "launch_app", lambda cfg, package: None)
    result = registry.get_record("launch_phone_app").raw_fn(package="com.spotify.music")
    assert "com.spotify.music" in result


def test_uninstall_phone_app_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "uninstall_app", lambda cfg, package, keep_data=False: None)
    result = registry.get_record("uninstall_phone_app").raw_fn(package="com.example.app")
    assert "com.example.app" in result


def test_uninstall_phone_app_is_in_the_confirm_standing_rules(container):
    assert container.config.policy.standing_rules.get("uninstall_phone_app") == "confirm"


# ------------------------------------------------------------ file transfer tools
def test_push_file_to_phone_tool_reports_the_remote_path(container, monkeypatch, tmp_path):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(
        adb, "push_file", lambda cfg, local_path, remote_dir: f"{remote_dir}/x.txt"
    )
    result = registry.get_record("push_file_to_phone").raw_fn(local_path=str(tmp_path / "x.txt"))
    assert "/sdcard/Download/x.txt" in result


def test_list_phone_files_tool_reports_an_empty_directory(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "list_remote_dir", lambda cfg, remote_dir: [])
    result = registry.get_record("list_phone_files").raw_fn(remote_dir="/sdcard/Download")
    assert "empty" in result


def test_delete_phone_file_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "delete_remote_file", lambda cfg, remote_path: None)
    result = registry.get_record("delete_phone_file").raw_fn(remote_path="/sdcard/Download/x.txt")
    assert "Deleted" in result


def test_delete_phone_file_is_in_the_confirm_standing_rules(container):
    assert container.config.policy.standing_rules.get("delete_phone_file") == "confirm"


# ------------------------------------------------------------------ contacts tools
def test_list_phone_contacts_tool_reports_matches(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "list_contacts", lambda cfg, query="": [("Alice", "9000000000")])
    result = registry.get_record("list_phone_contacts").raw_fn()
    assert "Alice" in result and "9000000000" in result


def test_add_phone_contact_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "add_contact", lambda cfg, name, number: None)
    result = registry.get_record("add_phone_contact").raw_fn(name="Alice", number="9000000000")
    assert "Alice" in result


def test_add_phone_contact_tool_reports_error_speakably(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    def boom(cfg, name, number):
        raise IntegrationError(
            "could not finish creating the contact; the partial contact was removed",
            service="phone",
        )

    monkeypatch.setattr(adb, "add_contact", boom)
    result = registry.get_record("add_phone_contact").raw_fn(name="Alice", number="9000000000")
    assert "Something went wrong" in result


# ------------------------------------------------------------- device settings tools
def test_set_phone_wifi_tool_reports_the_new_state(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "set_wifi", lambda cfg, enabled: None)
    assert "on" in registry.get_record("set_phone_wifi").raw_fn(enabled=True)


def test_set_phone_bluetooth_tool_reports_confirmed_change(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "set_bluetooth", lambda cfg, enabled: True)
    result = registry.get_record("set_phone_bluetooth").raw_fn(enabled=True)
    assert "turned on" in result


def test_set_phone_bluetooth_tool_reports_an_unconfirmed_change_honestly(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "set_bluetooth", lambda cfg, enabled: False)
    result = registry.get_record("set_phone_bluetooth").raw_fn(enabled=True)
    assert "did not confirm" in result


def test_set_phone_airplane_mode_tool_flags_the_wireless_disconnect_risk(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "set_airplane_mode", lambda cfg, enabled: None)
    monkeypatch.setattr(container.config.integrations.phone, "wireless_address", "1.2.3.4:5555")
    result = registry.get_record("set_phone_airplane_mode").raw_fn(enabled=True)
    assert "disconnect" in result.lower()


def test_set_phone_airplane_mode_is_in_the_confirm_standing_rules(container):
    assert container.config.policy.standing_rules.get("set_phone_airplane_mode") == "confirm"


def test_set_phone_volume_tool_reports_the_percent(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "set_volume_percent", lambda cfg, percent: None)
    assert "50%" in registry.get_record("set_phone_volume").raw_fn(percent=50)


def test_set_phone_brightness_tool_reports_the_percent(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "set_brightness_percent", lambda cfg, percent: None)
    assert "70%" in registry.get_record("set_phone_brightness").raw_fn(percent=70)


def test_reboot_phone_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "reboot", lambda cfg: None)
    assert "Rebooting" in registry.get_record("reboot_phone").raw_fn()


def test_reboot_phone_is_in_the_confirm_standing_rules(container):
    assert container.config.policy.standing_rules.get("reboot_phone") == "confirm"


# --------------------------------------------------- notifications/location tools
def test_read_phone_notifications_tool_reports_none_found(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "notifications", lambda cfg, limit=20: [])
    assert "No notifications" in registry.get_record("read_phone_notifications").raw_fn()


def test_read_phone_notifications_tool_reports_found_notifications(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    note = adb.Notification(package="com.whatsapp", title="Alice", text="hi", when=None)
    monkeypatch.setattr(adb, "notifications", lambda cfg, limit=20: [note])
    result = registry.get_record("read_phone_notifications").raw_fn()
    assert "Alice" in result and "hi" in result


def test_phone_location_tool_reports_no_cached_location(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "last_location", lambda cfg: None)
    assert "No location" in registry.get_record("phone_location").raw_fn()


def test_phone_location_tool_reports_a_found_location(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    loc = adb.Location(provider="fused", latitude=12.9, longitude=77.6)
    monkeypatch.setattr(adb, "last_location", lambda cfg: loc)
    result = registry.get_record("phone_location").raw_fn()
    assert "12.90000" in result


# ------------------------------------------------------------------- raw shell tool
def test_run_phone_shell_command_tool_rejects_an_empty_command(container):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    assert "Give a shell command" in registry.get_record("run_phone_shell_command").raw_fn(command="  ")


def test_run_phone_shell_command_tool_reports_the_output(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "run_shell", lambda cfg, command, timeout=None: "[exit 0]\nok")
    result = registry.get_record("run_phone_shell_command").raw_fn(command="echo ok")
    assert "[exit 0]" in result


def test_run_phone_shell_command_tool_reports_disabled_speakably(container, monkeypatch):
    from peter.agent import registry
    from peter.core.errors import NotConfiguredError

    registry.reset_for_tests()
    from peter.skills.phone import tools as phone_tools  # noqa: F401

    def boom(cfg, command, timeout=None):
        raise NotConfiguredError(
            "phone", "Set integrations.phone.raw_shell_enabled to true in config.yml."
        )

    monkeypatch.setattr(adb, "run_shell", boom)
    result = registry.get_record("run_phone_shell_command").raw_fn(command="ls")
    assert "raw_shell_enabled" in result


def test_run_phone_shell_command_is_in_the_confirm_standing_rules(container):
    assert container.config.policy.standing_rules.get("run_phone_shell_command") == "confirm"
