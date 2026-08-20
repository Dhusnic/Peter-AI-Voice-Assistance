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
         "Row: 0 display_name=Mom, number=+91 90000 00000\n"),
    ])

    found = adb.messages(phone_config())

    assert found[0].sender_name == "Mom"
    assert found[0].spoken().startswith("Mom (")


def test_a_sender_with_no_matching_contact_falls_back_to_the_raw_address(monkeypatch):
    _routed_adb(monkeypatch, [
        ("content://sms", f"Row: 0 address=AX-BLINKIT, body=hi, date={ms(1)}\n"),
        ("content://com.android.contacts", "Row: 0 display_name=Mom, number=+919000000000\n"),
    ])

    found = adb.messages(phone_config())

    assert found[0].sender_name is None
    assert found[0].spoken().startswith("AX-BLINKIT (")


def test_call_log_falls_back_to_contacts_when_the_device_gives_no_name(monkeypatch):
    _routed_adb(monkeypatch, [
        ("content://call_log", f"Row: 0 number=+919000000000, name=NULL, type=1, "
                               f"date={ms(1)}, duration=30\n"),
        ("content://com.android.contacts", "Row: 0 display_name=Mom, number=9000000000\n"),
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
    from peter.tools import phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "call_number", lambda cfg, number: None)

    result = registry.get_record("make_phone_call").raw_fn(number="9000000000")

    assert "9000000000" in result


def test_make_phone_call_tool_reports_a_bad_number_speakably(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401

    def boom(cfg, number):
        raise IntegrationError(f"{number!r} does not look like a phone number",
                               service="phone")

    monkeypatch.setattr(adb, "call_number", boom)

    result = registry.get_record("make_phone_call").raw_fn(number="not a number")

    assert "Something went wrong" in result


def test_answer_phone_call_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "answer_call", lambda cfg: None)

    assert "Answered" in registry.get_record("answer_phone_call").raw_fn()


def test_hang_up_phone_call_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "hang_up_call", lambda cfg: None)

    assert "ended" in registry.get_record("hang_up_phone_call").raw_fn().lower()


def test_play_music_on_phone_tool_names_the_query(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "launch_spotify", lambda cfg, query: None)

    result = registry.get_record("play_music_on_phone").raw_fn(query="lofi beats")

    assert "lofi beats" in result


def test_pause_music_on_phone_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "pause_media", lambda cfg: None)

    assert "Paused" in registry.get_record("pause_music_on_phone").raw_fn()


def test_skip_track_on_phone_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "next_track", lambda cfg: None)

    assert "Skipped" in registry.get_record("skip_track_on_phone").raw_fn()


def test_set_phone_alarm_tool_reports_the_time_and_label(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401

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
    from peter.tools import phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "dismiss_phone_alarm", lambda cfg: None)

    assert "dismissed" in registry.get_record("stop_phone_alarm").raw_fn().lower()


def test_read_call_log_tool_reports_calls(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401

    call = adb.Call(number="+91900", name="Mom", call_type="missed",
                     when=datetime.now(), duration_seconds=0)
    monkeypatch.setattr(adb, "calls", lambda cfg, since_minutes, limit: [call])

    result = registry.get_record("read_call_log").raw_fn()

    assert "Mom" in result


def test_open_link_on_phone_tool_reports_success(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401

    monkeypatch.setattr(adb, "open_url", lambda cfg, url: None)

    result = registry.get_record("open_link_on_phone").raw_fn(url="https://example.com")

    assert "phone" in result.lower()


def test_open_link_on_phone_tool_reports_the_error_speakably(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401

    def boom(cfg, url):
        raise IntegrationError("bad url", service="phone",
                               user_action="Use an http(s) link.")

    monkeypatch.setattr(adb, "open_url", boom)

    result = registry.get_record("open_link_on_phone").raw_fn(url="intent://x")

    assert "http(s) link" in result


def test_save_phone_screenshot_tool_reports_the_saved_filename(container, monkeypatch, tmp_path):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401

    monkeypatch.setattr(
        adb, "pull_latest_file", lambda cfg, dirs, local_dir: tmp_path / "shot.png"
    )

    result = registry.get_record("save_phone_screenshot").raw_fn()

    assert "shot.png" in result


def test_read_phone_screen_tool_uses_the_vision_pipeline(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401
    from peter.llm import vision

    monkeypatch.setattr(adb, "screenshot_bytes", lambda cfg: b"\x89PNGdata")
    monkeypatch.setattr(vision, "describe_image", lambda path, question, config: "a lock screen")

    result = registry.get_record("read_phone_screen").raw_fn(question="what's on screen")

    assert result == "a lock screen"


def test_read_phone_screen_tool_reports_a_disconnected_phone_speakably(container, monkeypatch):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.tools import phone_tools  # noqa: F401

    def boom(cfg):
        raise IntegrationError("no device", service="phone",
                               user_action="Connect the phone by USB.")

    monkeypatch.setattr(adb, "screenshot_bytes", boom)

    result = registry.get_record("read_phone_screen").raw_fn()

    assert "Connect the phone by USB" in result
