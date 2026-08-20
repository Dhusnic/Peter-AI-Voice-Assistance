"""ADB: device discovery, SMS, call log, contacts, the screen, and files.

**Why the device-side command is one string.** `adb shell` hands whatever it is
given to the phone's own shell, which re-splits it. Passing
`--where`, `date>123` as separate argv entries therefore arrives on the phone
as two words and the provider query fails with a syntax error that mentions
neither. Building the command as a single quoted string and letting the device
shell parse it is the only form that behaves the same across Android versions.

**Why the output is parsed with a lookahead regex.** `content query` prints

    Row: 0 address=+919000000000, body=Your OTP is 123456, date=1723456789000

and a message body can contain ", " freely — splitting on it truncates half of
the real-world messages. Splitting on the *next field name* instead is what
survives contact with actual SMS. Call log rows and the contacts table parse
the same way.

Reading (SMS, calls, contacts, the screen) is unrestricted. Acting on the
phone is deliberately narrow: `open_url` only ever opens a web page — never a
message send, a call, or an arbitrary intent — and there is still no way for
Peter to send a text or place a call as you.
"""

from __future__ import annotations

import logging
import pathlib
import re
import shlex
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta

from peter.core.config import PROJECT_ROOT
from peter.core.errors import IntegrationError

log = logging.getLogger(__name__)

_ROW = re.compile(r"^Row:\s*\d+\s+(.*)$")
# key=value, ending at the next "key=" or at end of line.
_FIELD = re.compile(r"(\w+)=(.*?)(?=,\s+\w+=|$)")
# A one-time code: 4-8 digits standing alone, not part of a longer number.
_CODE = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")

_OTP_HINTS = ("otp", "code", "verification", "verify", "password", "pin", "login")

_CALL_TYPES = {
    "1": "incoming", "2": "outgoing", "3": "missed",
    "4": "voicemail", "5": "rejected", "6": "blocked",
}

# Only characters a real phone number can contain. Rejecting anything else
# outright, before it ever reaches a shell string, is the first of two
# defences against injection through `call_number` — see `_quote` below for
# the second, which every other free-text value embedded in a device-shell
# command goes through.
_PHONE_NUMBER = re.compile(r"^[0-9+#*() .-]{2,20}$")

# ISO 8601 weekday (Monday=1..Sunday=7) -> Calendar.DAY_OF_WEEK
# (Sunday=1..Saturday=7), which is what AlarmClock.EXTRA_DAYS expects.
_ISO_TO_ANDROID_WEEKDAY = {"1": "2", "2": "3", "3": "4", "4": "5", "5": "6", "6": "7", "7": "1"}

# Contact names for a device rarely change within one Peter session, and a
# lookup costs a real ADB round trip — cached per (adb path, serial), not
# persisted, cleared by restarting Peter.
_CONTACTS_TTL_SECONDS = 600
_contacts_cached: dict[str, tuple[float, dict[str, str]]] = {}


@dataclass(slots=True)
class Sms:
    sender: str
    body: str
    when: datetime | None
    # Contact name for `sender`, when it resolves to one in the phone's
    # contacts. None for anything else (a sender id like "AX-BLINKIT", or an
    # unsaved number) — never guessed.
    sender_name: str | None = None

    def spoken(self) -> str:
        stamp = self.when.strftime("%d %b %H:%M") if self.when else "unknown time"
        who = self.sender_name or self.sender
        return f"{who} ({stamp}): {self.body}"


@dataclass(slots=True)
class Call:
    number: str
    name: str | None
    call_type: str
    when: datetime | None
    duration_seconds: int

    def spoken(self) -> str:
        stamp = self.when.strftime("%d %b %H:%M") if self.when else "unknown time"
        who = self.name or self.number
        if self.call_type == "missed":
            return f"Missed call from {who} at {stamp}."
        minutes, seconds = divmod(max(0, self.duration_seconds), 60)
        return f"{self.call_type.capitalize()} call with {who} at {stamp}, {minutes}:{seconds:02d}."


def _resolved_adb_path(cfg) -> str:
    """`adb_path` as configured, with a relative path anchored to the project
    root rather than left to depend on whatever directory Peter happens to be
    launched from.

    A bare `"adb"` (no path separator) is left untouched — that means "find it
    on PATH", and resolving it against the project root would instead make
    Peter look for a literal `<project root>/adb`, which is not what an empty
    directory component in the config value means.
    """
    raw = (cfg.adb_path or "adb").strip()
    path = pathlib.Path(raw)
    if path.is_absolute() or path.parent == pathlib.Path("."):
        return raw
    return str((PROJECT_ROOT / path).resolve())


def _null(value: str | None) -> str | None:
    """`content query` prints the literal string "NULL" for a null column."""
    if value is None:
        return None
    value = value.strip()
    return None if value in ("", "NULL") else value


def _quote(value: str) -> str:
    """POSIX-shell-safe quoting for free text embedded in a device-shell
    command string.

    Every function here that builds a command ultimately hands `adb` one
    joined string, which the *phone's* shell re-parses (see the module
    docstring) — so any free text folded into that string is a command
    injection point unless it is quoted correctly. Single-quoting (what this
    does) is the only form that is actually safe: double quotes still let a
    POSIX shell expand `$(...)`, backticks and `$VAR`, so naive
    double-quote-escaping — what this module's URL handling did at first —
    still lets a crafted value like `https://x/$(reboot)` execute on the
    phone. `shlex.quote` produces a single-quoted, embedded-quote-escaped
    string that survives that shell's parsing inertly.
    """
    return shlex.quote(value)


def _normalize_number(number: str | None) -> str:
    """Last 10 digits, so "+91 90000 00000", "090000-00000" and a plain
    10-digit contacts entry all match each other regardless of formatting."""
    digits = re.sub(r"\D", "", number or "")
    return digits[-10:] if len(digits) >= 10 else digits


def available(cfg) -> bool:
    return shutil.which(_resolved_adb_path(cfg)) is not None


def _contacts_cache(cfg) -> dict[str, str]:
    """Normalized number -> contact display name, for labelling SMS senders
    and call log entries.

    Best-effort and never raises: a phone with contacts-read blocked, or none
    saved at all, just means names do not resolve — SMS and call log reading
    do not depend on this succeeding.
    """
    key = f"{_resolved_adb_path(cfg)}|{cfg.device_serial}"
    cached = _contacts_cached.get(key)
    now = datetime.now().timestamp()
    if cached and now - cached[0] < _CONTACTS_TTL_SECONDS:
        return cached[1]

    table: dict[str, str] = {}
    try:
        raw = _run(
            ["shell", "content query --uri content://com.android.contacts/data/phones "
                      "--projection display_name,number"],
            cfg,
        )
        for line in raw.splitlines():
            match = _ROW.match(line.strip())
            if not match:
                continue
            fields = dict(_FIELD.findall(match.group(1)))
            name = _null(fields.get("display_name"))
            number = _normalize_number(_null(fields.get("number")))
            if name and number:
                table[number] = name
    except IntegrationError:
        log.debug("could not read phone contacts; names will not be resolved",
                   exc_info=True)

    _contacts_cached[key] = (now, table)
    return table


def _run(device_command: list[str], cfg, timeout: float | None = None) -> str:
    """Run one adb command, with the configured device selected."""
    if not available(cfg):
        raise IntegrationError(
            "adb is not installed, or not on PATH", service="phone",
            user_action=(
                "Install Android Platform Tools and add it to PATH, or set "
                "integrations.phone.adb_path in config.yml."
            ),
        )

    args = [_resolved_adb_path(cfg)]
    if cfg.device_serial:
        args += ["-s", cfg.device_serial]
    args += device_command

    try:
        result = subprocess.run(
            args, capture_output=True, text=True,
            timeout=timeout or cfg.timeout_seconds,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise IntegrationError(
            "the phone did not answer in time", service="phone", recoverable=True
        ) from exc
    except OSError as exc:
        raise IntegrationError(f"could not run adb: {exc}", service="phone") from exc

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or "error:" in output.lower():
        raise IntegrationError(
            f"adb failed: {output.strip()[:200]}", service="phone",
            user_action=_hint(output),
        )
    return result.stdout


def _hint(output: str) -> str:
    lowered = output.lower()
    if "unauthorized" in lowered:
        return "Unlock the phone and accept the 'Allow USB debugging' prompt."
    if "no devices" in lowered or "device not found" in lowered:
        return "Connect the phone by USB with USB debugging switched on."
    if "more than one" in lowered:
        return "Several devices are attached — set integrations.phone.device_serial."
    return ""


def devices(cfg) -> list[tuple[str, str]]:
    """Attached devices as (serial, state)."""
    found = []
    for line in _run(["devices"], cfg, timeout=10).splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            found.append((parts[0], parts[1]))
    return found


def health(cfg) -> str:
    """One line for `--health`. Never raises."""
    if not available(cfg):
        return "adb not installed"
    try:
        attached = devices(cfg)
    except IntegrationError as exc:
        return f"failed: {exc}"
    if not attached:
        return "no device attached"
    ready = [serial for serial, state in attached if state == "device"]
    if not ready:
        states = ", ".join(f"{s}: {st}" for s, st in attached)
        return f"device attached but not ready ({states})"
    return f"ok - {len(ready)} device(s)"


def messages(cfg, since_minutes: int = 10080, limit: int = 20) -> list[Sms]:
    """Recent inbox SMS, newest first.

    Args:
        since_minutes: How far back to read. Default is a week — this is a
            `WHERE` clause on the device, so a wide window does not mean a big
            transfer, but an unbounded one on a phone with years of history
            would.
    """
    cutoff_ms = int((datetime.now() - timedelta(minutes=since_minutes)).timestamp() * 1000)
    command = (
        "content query --uri content://sms/inbox "
        "--projection address,body,date "
        f'--where "date>{cutoff_ms}"'
    )
    raw = _run(["shell", command], cfg)
    contacts = _contacts_cache(cfg)

    found: list[Sms] = []
    for line in raw.splitlines():
        match = _ROW.match(line.strip())
        if not match:
            continue
        fields = dict(_FIELD.findall(match.group(1)))
        when = None
        try:
            when = datetime.fromtimestamp(int(fields.get("date", 0)) / 1000)
        except (ValueError, OSError, OverflowError):
            pass
        sender = fields.get("address") or "unknown"
        found.append(
            Sms(
                sender=sender,
                sender_name=contacts.get(_normalize_number(sender)),
                body=(fields.get("body") or "").strip(),
                when=when,
            )
        )

    found.sort(key=lambda m: m.when or datetime.min, reverse=True)
    return found[:limit]


def calls(cfg, since_minutes: int = 10080, limit: int = 20) -> list[Call]:
    """Recent calls, newest first."""
    cutoff_ms = int((datetime.now() - timedelta(minutes=since_minutes)).timestamp() * 1000)
    command = (
        "content query --uri content://call_log/calls "
        "--projection number,name,type,date,duration "
        f'--where "date>{cutoff_ms}"'
    )
    raw = _run(["shell", command], cfg)
    contacts = _contacts_cache(cfg)

    found: list[Call] = []
    for line in raw.splitlines():
        match = _ROW.match(line.strip())
        if not match:
            continue
        fields = dict(_FIELD.findall(match.group(1)))
        number = _null(fields.get("number")) or "unknown"
        name = _null(fields.get("name")) or contacts.get(_normalize_number(number))
        when = None
        try:
            when = datetime.fromtimestamp(int(fields.get("date", 0)) / 1000)
        except (ValueError, OSError, OverflowError):
            pass
        try:
            duration = int(fields.get("duration", 0))
        except ValueError:
            duration = 0
        found.append(Call(
            number=number,
            name=name,
            call_type=_CALL_TYPES.get(fields.get("type", ""), "call"),
            when=when,
            duration_seconds=duration,
        ))

    found.sort(key=lambda c: c.when or datetime.min, reverse=True)
    return found[:limit]


def screenshot_bytes(cfg) -> bytes:
    """Raw PNG bytes of the phone's current screen.

    Bypasses `_run`: that helper runs in text mode, which on Windows rewrites
    line endings and would corrupt binary PNG data. `exec-out` (not `shell`)
    is what keeps stdout as the raw command output instead of adb's own
    text-oriented framing.
    """
    if not available(cfg):
        raise IntegrationError(
            "adb is not installed, or not on PATH", service="phone",
            user_action=(
                "Install Android Platform Tools and add it to PATH, or set "
                "integrations.phone.adb_path in config.yml."
            ),
        )
    args = [_resolved_adb_path(cfg)]
    if cfg.device_serial:
        args += ["-s", cfg.device_serial]
    args += ["exec-out", "screencap", "-p"]

    try:
        result = subprocess.run(args, capture_output=True, timeout=cfg.timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise IntegrationError(
            "the phone did not answer in time", service="phone", recoverable=True
        ) from exc
    except OSError as exc:
        raise IntegrationError(f"could not run adb: {exc}", service="phone") from exc

    if result.returncode != 0 or not result.stdout:
        stderr = result.stderr.decode("utf-8", "replace")
        raise IntegrationError(
            f"could not capture the phone screen: {stderr.strip()[:200]}",
            service="phone", user_action=_hint(stderr),
        )
    return result.stdout


def open_url(cfg, url: str) -> None:
    """Open a web page on the phone's screen — the hand-off point for a
    checkout or login that needs the phone for an OTP or a UPI PIN.

    Restricted to http(s): `am start -d` also accepts intent-scheme URIs
    (`intent://`, `market://`, ...) that can launch arbitrary app components,
    which is not what "open this link" should ever be able to do.
    """
    if not (url.startswith("http://") or url.startswith("https://")):
        raise IntegrationError(
            f"refusing to open a non-http(s) link on the phone: {url!r}",
            service="phone",
        )
    command = f"am start -a android.intent.action.VIEW -d {_quote(url)}"
    _run(["shell", command], cfg)


def call_number(cfg, number: str) -> None:
    """Place a call. Connects immediately (`ACTION_CALL`), with no on-device
    confirmation step — unlike `ACTION_DIAL`, which only opens the dialer.
    That is why the number is validated as looking like a phone number before
    it ever reaches a shell string, on top of the usual quoting."""
    number = number.strip()
    if not _PHONE_NUMBER.match(number):
        raise IntegrationError(
            f"{number!r} does not look like a phone number", service="phone",
        )
    command = f"am start -a android.intent.action.CALL -d {_quote(f'tel:{number}')}"
    _run(["shell", command], cfg)


def answer_call(cfg) -> None:
    """Answer an incoming, currently ringing call."""
    _run(["shell", "input keyevent KEYCODE_CALL"], cfg)


def hang_up_call(cfg) -> None:
    """End an active call, or reject one that is currently ringing — the same
    key press does both, matching a physical end-call button."""
    _run(["shell", "input keyevent KEYCODE_ENDCALL"], cfg)


def launch_spotify(cfg, query: str = "") -> None:
    """Open Spotify, optionally straight to a search.

    A query goes through Spotify's own `spotify:search:<text>` URI, which
    Spotify registers as a VIEW target. With no query, this launches the app
    by package name via `monkey -p ... -c LAUNCHER` — deliberately not a
    hardcoded activity class name (`am start -n com.spotify.music/.Something`),
    which is exactly the kind of internal detail that breaks across app
    updates — then sends a media-play key so playback resumes if Spotify was
    already open and paused.
    """
    package = _quote((cfg.spotify_package or "com.spotify.music").strip())
    query = query.strip()
    if query:
        uri = f"spotify:search:{urllib.parse.quote(query)}"
        command = f"am start -a android.intent.action.VIEW -d {_quote(uri)}"
        _run(["shell", command], cfg)
    else:
        _run(["shell", f"monkey -p {package} -c android.intent.category.LAUNCHER 1"], cfg)
        _run(["shell", "input keyevent KEYCODE_MEDIA_PLAY"], cfg)


def pause_media(cfg) -> None:
    """Pause whatever is playing through the phone's currently active media
    session. A system-level media key, not Spotify-specific — it pauses
    Spotify if Spotify is what is playing, the same way a headset button
    would."""
    _run(["shell", "input keyevent KEYCODE_MEDIA_PAUSE"], cfg)


def next_track(cfg) -> None:
    """Skip to the next track in the active media session."""
    _run(["shell", "input keyevent KEYCODE_MEDIA_NEXT"], cfg)


def _android_weekdays(days: str) -> list[str]:
    result = []
    for token in days.split(","):
        token = token.strip()
        if token in _ISO_TO_ANDROID_WEEKDAY:
            result.append(_ISO_TO_ANDROID_WEEKDAY[token])
    return result


def set_alarm_on_phone(cfg, hour: int, minute: int, label: str = "", days: str = "") -> None:
    """Set an alarm through the standard `android.intent.action.SET_ALARM`
    intent (part of the public `android.provider.AlarmClock` API), so this
    works with whatever the phone's default clock app is instead of
    depending on one OEM's internals. Also how a phone-side "reminder" is
    implemented — Android has no separate public intent for that, so a
    reminder is just an alarm with a label.

    Args:
        days: Comma-separated ISO weekdays to repeat on (Monday=1..Sunday=7).
            Empty is a one-off alarm.
    """
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise IntegrationError("hour must be 0-23 and minute 0-59", service="phone")

    parts = [
        "am start -a android.intent.action.SET_ALARM",
        f"--ei android.intent.extra.alarm.HOUR {hour}",
        f"--ei android.intent.extra.alarm.MINUTES {minute}",
        "--ez android.intent.extra.alarm.SKIP_UI true",
    ]
    if label.strip():
        parts.append(f"--es android.intent.extra.alarm.MESSAGE {_quote(label.strip())}")
    android_days = _android_weekdays(days) if days.strip() else []
    if android_days:
        parts.append(f"--eia android.intent.extra.alarm.DAYS {','.join(android_days)}")

    _run(["shell", " ".join(parts)], cfg)


def dismiss_phone_alarm(cfg) -> None:
    """Dismiss the alarm currently ringing (or the next one due), through
    `android.intent.action.DISMISS_ALARM` — the counterpart public intent to
    `SET_ALARM`. Support varies by the phone's default clock app; Google
    Clock and most AOSP-based ones honour it. If none of the phone's
    installed apps handle the intent, `am start` fails the normal way and
    `_run` surfaces it as an IntegrationError rather than pretending it
    worked."""
    _run(["shell", "am start -a android.intent.action.DISMISS_ALARM"], cfg)


def _list_remote(cfg, remote_dir: str) -> list[str]:
    """Files in a phone directory, newest first. Empty (not an error) for a
    directory that does not exist — the caller tries several in turn."""
    args = [_resolved_adb_path(cfg)]
    if cfg.device_serial:
        args += ["-s", cfg.device_serial]
    args += ["shell", f"ls -t {_quote(remote_dir)} 2>/dev/null"]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=cfg.timeout_seconds,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    return [name.strip() for name in result.stdout.splitlines() if name.strip()]


def pull_latest_file(cfg, remote_dirs: list[str], local_dir: pathlib.Path) -> pathlib.Path:
    """Copy the newest file in the first non-empty remote directory to
    local_dir. Used for "grab that screenshot off my phone"."""
    if not available(cfg):
        raise IntegrationError(
            "adb is not installed, or not on PATH", service="phone",
            user_action=(
                "Install Android Platform Tools and add it to PATH, or set "
                "integrations.phone.adb_path in config.yml."
            ),
        )

    chosen_remote = None
    for remote_dir in remote_dirs:
        names = _list_remote(cfg, remote_dir)
        if names:
            chosen_remote = f"{remote_dir.rstrip('/')}/{names[0]}"
            break
    if chosen_remote is None:
        raise IntegrationError(
            "no files found in the phone's screenshot folders", service="phone",
            user_action="Take a screenshot on the phone first, or check "
                        "integrations.phone.pull_dirs in config.yml.",
        )

    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / pathlib.Path(chosen_remote).name

    args = [_resolved_adb_path(cfg)]
    if cfg.device_serial:
        args += ["-s", cfg.device_serial]
    args += ["pull", chosen_remote, str(local_path)]

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=cfg.timeout_seconds * 3,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise IntegrationError(
            "the transfer timed out", service="phone", recoverable=True
        ) from exc

    if result.returncode != 0:
        raise IntegrationError(
            f"could not pull the file: {(result.stderr or result.stdout).strip()[:200]}",
            service="phone",
        )
    return local_path


def latest_code(cfg) -> tuple[str, Sms] | None:
    """The most recent one-time code, if one arrived inside the window.

    Prefers a message that says it is a code. A bare number in a message with
    no verification wording is far more likely to be an amount, an order id or
    a phone number, so those only count when nothing better is there.
    """
    recent = messages(cfg, since_minutes=cfg.otp_window_minutes, limit=20)

    fallback: tuple[str, Sms] | None = None
    for message in recent:
        codes = _CODE.findall(message.body)
        if not codes:
            continue
        lowered = message.body.lower()
        if any(hint in lowered for hint in _OTP_HINTS):
            return codes[0], message
        fallback = fallback or (codes[0], message)
    return fallback


def battery(cfg) -> str:
    """Battery level, as `dumpsys battery` reports it."""
    raw = _run(["shell", "dumpsys battery"], cfg)
    level = plugged = ""
    for line in raw.splitlines():
        key, _, value = line.strip().partition(":")
        if key.strip() == "level":
            level = value.strip()
        elif key.strip() == "status":
            plugged = value.strip()
    if not level:
        return "The phone did not report a battery level."
    charging = " (charging)" if plugged == "2" else ""
    return f"Phone battery is at {level}%{charging}."
