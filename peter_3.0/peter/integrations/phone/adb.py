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

**Why `--projection` is colon-separated, not comma-separated.** Found by
actually running this against a real phone, not by reading Android's docs:
`content query`'s comma-separated projection form (`--projection a,b,c`)
works for `content://sms/inbox` on this device but fails outright for
`content://call_log/calls` ("Invalid column a,b,c" — the whole string taken
as one literal column name) and for the contacts provider ("Non-token
detected in 'a,b'" — a stricter tokenizer rejects it even with a space after
the comma). A colon-separated projection (`a:b:c`) works identically across
all three. The contacts provider has a second, unrelated gotcha on top: its
friendly `number` column alias is not resolvable through the raw `content`
CLI at all (`Invalid column number`) even once the separator is fixed — the
real column underneath is `data1`, the generic key-value slot
`ContactsContract` uses for a phone-type row's number.

Reading (SMS, calls, contacts, the screen, notifications, location) is
unrestricted. Acting spans app management, file transfer, contacts,
device settings, calls, Spotify, alarms, and a raw `adb shell` escape
hatch (`run_shell`, opt-in via `integrations.phone.raw_shell_enabled`) —
but sending a text as you, and any form of tap/swipe/type UI automation,
are still out of scope; see `peter/skills/phone/tools.py` for what each
write-tier tool actually does and why.

Several of the newer read functions (`notifications`, `last_location`) are
explicitly best-effort: they parse unstructured `dumpsys` diagnostic text
that can drift across Android versions and OEM skins, not a documented,
stable API. `add_contact` (contact creation) is similarly best-effort for a
different reason — some OEM sync adapters reject or mangle an
account-less raw contact — and reads back what it wrote before declaring
success rather than trusting the insert blindly.
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
from peter.core.errors import IntegrationError, NotConfiguredError

log = logging.getLogger(__name__)

# DOTALL on both: a field's value can itself contain a literal newline (a
# multi-line bank SMS body, in practice — "Sent Rs.60.00\nFrom HDFC Bank
# A/C...\nRef 1234...", verified against a real device). Without DOTALL,
# `.` stops at that embedded newline, silently truncating the value and
# losing whatever field came after it (`date`, in the case that surfaced
# this — every affected message read back as 1 Jan 1970).
_ROW = re.compile(r"^Row:\s*\d+\s+(.*)$", re.DOTALL)
# key=value, ending at the next "key=" or at end of the row.
_FIELD = re.compile(r"(\w+)=(.*?)(?=,\s+\w+=|$)", re.DOTALL)
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


def _rows(raw: str) -> list[str]:
    """Split `content query` output into one chunk per row.

    Deliberately not `raw.splitlines()`: a row boundary is only a newline
    immediately followed by the next "Row: N " marker. A field's value can
    contain a literal newline of its own (a multi-line SMS body), and
    splitting on every newline would break one logical row into several that
    no longer match `_ROW` — silently truncating the value and dropping
    whatever came after it.
    """
    return re.split(r"\r?\n(?=Row:\s*\d+\s)", raw.strip())


def _normalize_number(number: str | None) -> str:
    """Last 10 digits, so "+91 90000 00000", "090000-00000" and a plain
    10-digit contacts entry all match each other regardless of formatting."""
    digits = re.sub(r"\D", "", number or "")
    return digits[-10:] if len(digits) >= 10 else digits


def available(cfg) -> bool:
    return shutil.which(_resolved_adb_path(cfg)) is not None


def _looks_disconnected(output: str) -> bool:
    """True for "nothing to talk to", not for "talking to it failed" —
    "unauthorized" deliberately doesn't match: the device is attached, just
    waiting on the USB-debugging prompt, which reconnecting cannot fix."""
    lowered = output.lower()
    return (
        "no devices" in lowered
        or "device not found" in lowered
        or "device offline" in lowered
    )


def _connect_wireless(cfg) -> bool:
    """Best-effort `adb connect <wireless_address>`.

    Never raises: a failed reconnect attempt should fall through to the
    original error from whatever command triggered it, not replace it with a
    more confusing one about the reconnect itself.
    """
    address = (cfg.wireless_address or "").strip()
    if not address:
        return False
    try:
        result = subprocess.run(
            [_resolved_adb_path(cfg), "connect", address],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and "connected to" in result.stdout.lower()


def _contacts_cache(cfg) -> dict[str, tuple[str, str]]:
    """Normalized number -> (display name, number as the contacts app has it
    saved), for labelling SMS senders and call log entries, and for looking
    a contact up by name to call them.

    The raw number is kept alongside the normalized one deliberately: the
    normalized form (last 10 digits) is right for *matching* an incoming
    SMS or call to a name regardless of formatting, but is not always
    dialable on its own — an international number truncated to its last 10
    digits loses the country code. Calling a contact needs the real thing.

    Best-effort and never raises: a phone with contacts-read blocked, or none
    saved at all, just means names do not resolve — SMS and call log reading
    do not depend on this succeeding.
    """
    key = _cache_key(cfg)
    cached = _contacts_cached.get(key)
    now = datetime.now().timestamp()
    if cached and now - cached[0] < _CONTACTS_TTL_SECONDS:
        return cached[1]

    table: dict[str, tuple[str, str]] = {}
    try:
        raw = _run(
            ["shell", "content query --uri content://com.android.contacts/data/phones "
                      "--projection display_name:data1"],
            cfg,
        )
        for row in _rows(raw):
            match = _ROW.match(row.strip())
            if not match:
                continue
            fields = dict(_FIELD.findall(match.group(1)))
            name = _null(fields.get("display_name"))
            number_raw = _null(fields.get("data1"))
            number_norm = _normalize_number(number_raw)
            if name and number_norm and number_raw:
                table[number_norm] = (name, number_raw)
    except IntegrationError:
        log.debug("could not read phone contacts; names will not be resolved",
                   exc_info=True)

    _contacts_cached[key] = (now, table)
    return table


def _contact_name(contacts: dict[str, tuple[str, str]], number: str) -> str | None:
    entry = contacts.get(_normalize_number(number))
    return entry[0] if entry else None


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def find_contact(cfg, name: str) -> list[tuple[str, str]]:
    """Saved contacts matching `name`, as (display name, dialable number)
    pairs — for resolving "call Ancy Mom" to an actual number.

    Two passes. First, the query as a literal substring of the saved name —
    the common case when the caller passes exactly what's saved. If nothing
    matches that way, falls back to matching any individual *word* of the
    query against the contact's own words: natural speech adds relationship
    words ("mom", "dad's office") that are often not part of the saved name
    at all — a real example that motivated this: a contact saved as bare
    "Ancy", called "Ancy Mom" out loud, matches nothing on substring alone
    but does on the shared word "ancy".

    More than one match (two numbers for one person, several contacts
    sharing a word) is returned as-is; the caller decides whether that needs
    disambiguating rather than guessing here.
    """
    needle = name.strip().lower()
    if not needle:
        return []

    contacts = list(_contacts_cache(cfg).values())
    exact = [(n, num) for n, num in contacts if needle in n.lower()]
    if exact:
        return list(dict.fromkeys(exact))

    query_words = set(_words(needle))
    token_matches = [(n, num) for n, num in contacts if query_words & set(_words(n))]
    return list(dict.fromkeys(token_matches))


def list_contacts(cfg, query: str = "") -> list[tuple[str, str]]:
    """Saved contacts as (display name, number) pairs. Empty `query` returns
    every saved contact; non-empty delegates to `find_contact`'s
    substring/word matching."""
    query = query.strip()
    if query:
        return find_contact(cfg, query)
    return sorted(_contacts_cache(cfg).values())


def _cache_key(cfg) -> str:
    return f"{_resolved_adb_path(cfg)}|{cfg.device_serial}"


def _create_raw_contact(cfg) -> str:
    """Create an account-less ("local") raw contact and return its row id.

    The `content` CLI does not report back the id of a row it just inserted,
    so this queries for it immediately after — a race-prone heuristic (the
    newest `account_type IS NULL` row) that is fine for a personal,
    single-user phone with no concurrent contact edits, and not guaranteed
    correct in general.
    """
    _run(
        ["shell", "content insert --uri content://com.android.contacts/raw_contacts "
                  "--bind account_type:s:null --bind account_name:s:null"],
        cfg,
    )
    raw = _run(
        ["shell", "content query --uri content://com.android.contacts/raw_contacts "
                  '--projection _id --where "account_type IS NULL" --sort "_id DESC"'],
        cfg,
    )
    for row in _rows(raw):
        match = _ROW.match(row.strip())
        if not match:
            continue
        fields = dict(_FIELD.findall(match.group(1)))
        row_id = _null(fields.get("_id"))
        if row_id:
            return row_id
    raise IntegrationError("could not find the newly created contact", service="phone")


def _delete_raw_contact_best_effort(cfg, raw_id: str) -> bool:
    try:
        _run(
            ["shell", f"content delete --uri "
                      f"content://com.android.contacts/raw_contacts/{raw_id}"],
            cfg,
        )
        return True
    except IntegrationError:
        return False


def add_contact(cfg, name: str, number: str) -> None:
    """Create a new phone contact via the Contacts Provider's `content
    insert` — the only reliable no-root path, and account-less ("local")
    contacts are the most reliable variant of that.

    Genuinely fragile in a way worth being honest about: some OEM builds
    (Samsung, Xiaomi) run a sync adapter that can reject or silently mangle
    an account-less raw contact. This always reads back what was actually
    saved before declaring success, and best-effort cleans up a
    half-written contact (name with no number, or vice versa) if a step
    after the first one fails.
    """
    name = name.strip()
    number = number.strip()
    if not name:
        raise IntegrationError("no contact name given", service="phone")
    if not number:
        raise IntegrationError("no contact number given", service="phone")

    raw_id = _create_raw_contact(cfg)
    try:
        _run(
            ["shell", "content insert --uri content://com.android.contacts/data "
                      f"--bind raw_contact_id:i:{raw_id} "
                      "--bind mimetype:s:vnd.android.cursor.item/name "
                      f"--bind data1:s:{_quote(name)}"],
            cfg,
        )
        _run(
            ["shell", "content insert --uri content://com.android.contacts/data "
                      f"--bind raw_contact_id:i:{raw_id} "
                      "--bind mimetype:s:vnd.android.cursor.item/phone_v2 "
                      f"--bind data1:s:{_quote(number)} --bind data2:i:2"],
            cfg,
        )
    except IntegrationError:
        cleaned_up = _delete_raw_contact_best_effort(cfg, raw_id)
        state = (
            "the partial contact was removed" if cleaned_up
            else "could not clean up the partial contact — check the phone's Contacts app"
        )
        raise IntegrationError(
            f"could not finish creating the contact; {state}", service="phone",
        )

    _contacts_cached.pop(_cache_key(cfg), None)
    verify = find_contact(cfg, name)
    if not any(_normalize_number(num) == _normalize_number(number) for _, num in verify):
        raise IntegrationError(
            f"the contact was written but did not read back correctly — "
            f"check the phone's Contacts app for {name!r}",
            service="phone",
        )


def _exec(
    args: list[str], cfg, timeout: float | None, *, text: bool = True
) -> subprocess.CompletedProcess:
    kwargs: dict = dict(capture_output=True, timeout=timeout or cfg.timeout_seconds)
    if text:
        kwargs.update(text=True, encoding="utf-8", errors="replace")
    return subprocess.run(args, **kwargs)


def _output_text(result: subprocess.CompletedProcess) -> str:
    """stdout+stderr as text, whether `result` came from a text-mode or
    binary-mode run (`screenshot_bytes` is the one binary caller)."""
    out, err = result.stdout, result.stderr
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    return (out or "") + (err or "")


def _retry_if_disconnected(
    args: list[str], cfg, timeout: float | None,
    result: subprocess.CompletedProcess, *, text: bool = True,
) -> subprocess.CompletedProcess:
    """Given a just-completed attempt, retry once through `adb connect` if it
    looks like a dropped wireless session; otherwise return `result`
    unchanged.

    A wireless ADB session does not survive the phone leaving and rejoining
    Wi-Fi, a reboot on either side, or wireless debugging itself being
    toggled off and on — without this, every one of those would need `adb
    connect` re-run by hand before the next command, which defeats the point
    of not needing the cable at all. Shared by every function that talks to
    the device directly (`_run`, `screenshot_bytes`, `_list_remote`,
    `pull_latest_file`, and the new file/shell functions below) — previously
    each duplicated this block inline.
    """
    if not _looks_disconnected(_output_text(result)):
        return result
    if not _connect_wireless(cfg):
        return result
    try:
        return _exec(args, cfg, timeout, text=text)
    except (subprocess.TimeoutExpired, OSError):
        return result


def _run(device_command: list[str], cfg, timeout: float | None = None) -> str:
    """Run one adb command, with the configured device selected.

    See `_retry_if_disconnected` for the wireless reconnect-and-retry this
    goes through on a disconnect-shaped failure.
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
    args += device_command

    try:
        result = _exec(args, cfg, timeout)
    except subprocess.TimeoutExpired as exc:
        raise IntegrationError(
            "the phone did not answer in time", service="phone", recoverable=True
        ) from exc
    except OSError as exc:
        raise IntegrationError(f"could not run adb: {exc}", service="phone") from exc

    output = _output_text(result)
    if result.returncode != 0 or "error:" in output.lower():
        result = _retry_if_disconnected(args, cfg, timeout, result)
        output = _output_text(result)

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


# ------------------------------------------------------------ app management
# Package-id only, deliberately — there is no reliable no-root way to resolve
# a package id to the human-readable name the launcher shows (that needs
# `aapt dump badging` against the APK, a desktop SDK tool with nothing
# equivalent on-device). `list_apps` is how a caller finds the exact id to
# pass to `launch_app`/`uninstall_app` rather than guessing one from a name.
def list_apps(cfg, third_party_only: bool = True) -> list[str]:
    """Installed package ids, sorted. Third-party (user-installed) only by
    default — the full system package list is enormous and rarely what
    "what apps do I have" means."""
    command = "pm list packages -3" if third_party_only else "pm list packages"
    raw = _run(["shell", command], cfg)
    packages = [
        line.strip()[len("package:"):]
        for line in raw.splitlines()
        if line.strip().startswith("package:")
    ]
    return sorted(packages)


def launch_app(cfg, package: str) -> None:
    """Launch an app by its exact package id. Uses a monkey launch
    (`-c android.intent.category.LAUNCHER`), the same fallback
    `launch_spotify` already uses for its no-query case — the exact launcher
    activity name (needed for `am start -n pkg/.Activity`) isn't knowable
    without inspecting the APK."""
    package = package.strip()
    if not package:
        raise IntegrationError("no package id given", service="phone")
    _run(
        ["shell", f"monkey -p {_quote(package)} -c android.intent.category.LAUNCHER 1"],
        cfg,
    )


def uninstall_app(cfg, package: str, keep_data: bool = False) -> None:
    """Uninstall an app by package id. `--user 0` removes it from the
    current user profile without root — this also works for pre-installed
    apps a plain `pm uninstall` refuses to touch, though the APK itself
    stays on the read-only system partition (true removal needs root)."""
    package = package.strip()
    if not package:
        raise IntegrationError("no package id given", service="phone")
    flag = " -k" if keep_data else ""
    _run(["shell", f"pm uninstall --user 0{flag} {_quote(package)}"], cfg)


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
        "--projection address:body:date "
        f'--where "date>{cutoff_ms}"'
    )
    raw = _run(["shell", command], cfg)
    contacts = _contacts_cache(cfg)

    found: list[Sms] = []
    for row in _rows(raw):
        match = _ROW.match(row.strip())
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
                sender_name=_contact_name(contacts, sender),
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
        "--projection number:name:type:date:duration "
        f'--where "date>{cutoff_ms}"'
    )
    raw = _run(["shell", command], cfg)
    contacts = _contacts_cache(cfg)

    found: list[Call] = []
    for row in _rows(raw):
        match = _ROW.match(row.strip())
        if not match:
            continue
        fields = dict(_FIELD.findall(match.group(1)))
        number = _null(fields.get("number")) or "unknown"
        name = _null(fields.get("name")) or _contact_name(contacts, number)
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

    Runs in binary mode (`text=False`): text mode rewrites line endings on
    Windows and would corrupt binary PNG data. `exec-out` (not `shell`) is
    what keeps stdout as the raw command output instead of adb's own
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
        result = _exec(args, cfg, cfg.timeout_seconds, text=False)
    except subprocess.TimeoutExpired as exc:
        raise IntegrationError(
            "the phone did not answer in time", service="phone", recoverable=True
        ) from exc
    except OSError as exc:
        raise IntegrationError(f"could not run adb: {exc}", service="phone") from exc

    if result.returncode != 0 or not result.stdout:
        result = _retry_if_disconnected(args, cfg, cfg.timeout_seconds, result, text=False)

    if result.returncode != 0 or not result.stdout:
        stderr = _output_text(result)
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


# ------------------------------------------------------------- device settings
def set_wifi(cfg, enabled: bool) -> None:
    """Toggle WiFi. Uses `cmd -w wifi set-wifi-enabled`, not the older `svc
    wifi`, which is deprecated and unreliable from Android 10 onward."""
    state = "enabled" if enabled else "disabled"
    _run(["shell", f"cmd -w wifi set-wifi-enabled {state}"], cfg)


def set_bluetooth(cfg, enabled: bool) -> bool:
    """Toggle Bluetooth and report whether the phone actually confirmed the
    change, rather than assuming success. There is no reliable silent
    no-root way to toggle Bluetooth on Android 12+ — `svc bluetooth` is
    issued regardless, but the result is read back so a caller can say
    honestly when it did not take effect (commonly needs doing on-device on
    newer Android)."""
    _run(["shell", f"svc bluetooth {'enable' if enabled else 'disable'}"], cfg)
    raw = _run(["shell", "settings get global bluetooth_on"], cfg)
    return raw.strip() == ("1" if enabled else "0")


def set_airplane_mode(cfg, enabled: bool) -> None:
    """Toggle airplane mode. Two steps: the setting itself, then the
    broadcast that makes the system act on it — the setting alone does not
    apply it.

    Real risk if `integrations.phone.wireless_address` is set: enabling
    airplane mode can sever the wireless ADB session the instant it takes
    effect, at which point the broadcast call fails in a way indistinguishable
    from any other dropped connection. That specific case — enabling, over a
    configured wireless address, broadcast fails looking disconnected — is
    treated as a likely soft-success (the setting almost certainly did
    apply) rather than a hard failure, since there is no way from this side
    to tell "failed to apply" apart from "applied and cut the connection".
    """
    value = "1" if enabled else "0"
    _run(["shell", f"settings put global airplane_mode_on {value}"], cfg)
    state = "true" if enabled else "false"
    try:
        _run(
            ["shell", "am broadcast -a android.intent.action.AIRPLANE_MODE "
                      f"--ez state {state}"],
            cfg,
        )
    except IntegrationError:
        if enabled and (cfg.wireless_address or "").strip():
            log.info(
                "airplane mode broadcast failed while enabling over wireless "
                "ADB — likely means it worked and cut the connection"
            )
            return
        raise


_VOLUME_RANGE = re.compile(r"volume is (\d+) in range \[(\d+)\.\.(\d+)\]")


def _music_volume_range(cfg) -> tuple[int, int]:
    """(current, max) for the music stream (stream 3). Falls back to a
    conservative (0, 15) if the phone's reported format doesn't match what
    was verified during development — better to under-scale a volume change
    than raise on an unexpected format."""
    raw = _run(["shell", "cmd media_session volume --stream 3 --get"], cfg)
    match = _VOLUME_RANGE.search(raw)
    if match:
        return int(match.group(1)), int(match.group(3))
    return 0, 15


def set_volume_percent(cfg, percent: int) -> None:
    """Set the phone's media (music) volume, as a 0-100 percent."""
    if not (0 <= percent <= 100):
        raise IntegrationError("percent must be 0-100", service="phone")
    _, max_level = _music_volume_range(cfg)
    level = round(max_level * percent / 100)
    _run(["shell", f"cmd media_session volume --stream 3 --set {level}"], cfg)


def set_brightness_percent(cfg, percent: int) -> None:
    """Set screen brightness, as a 0-100 percent. Forces manual brightness
    mode first — with adaptive/auto brightness on, an explicit value here
    would otherwise be silently overridden a moment later."""
    if not (0 <= percent <= 100):
        raise IntegrationError("percent must be 0-100", service="phone")
    level = round(255 * percent / 100)
    _run(["shell", "settings put system screen_brightness_mode 0"], cfg)
    _run(["shell", f"settings put system screen_brightness {level}"], cfg)


def reboot(cfg) -> None:
    """Reboot the phone. Host-level `adb reboot`, not `adb shell reboot`."""
    _run(["reboot"], cfg, timeout=10)


def _list_remote(cfg, remote_dir: str) -> list[str]:
    """Files in a phone directory, newest first. Empty (not an error) for a
    directory that does not exist — the caller tries several in turn."""
    args = [_resolved_adb_path(cfg)]
    if cfg.device_serial:
        args += ["-s", cfg.device_serial]
    args += ["shell", f"ls -t {_quote(remote_dir)} 2>/dev/null"]
    try:
        result = _exec(args, cfg, cfg.timeout_seconds)
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        result = _retry_if_disconnected(args, cfg, cfg.timeout_seconds, result)
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
        result = _exec(args, cfg, cfg.timeout_seconds * 3)
    except subprocess.TimeoutExpired as exc:
        raise IntegrationError(
            "the transfer timed out", service="phone", recoverable=True
        ) from exc
    except OSError as exc:
        raise IntegrationError(f"could not run adb: {exc}", service="phone") from exc

    if result.returncode != 0:
        result = _retry_if_disconnected(args, cfg, cfg.timeout_seconds * 3, result)

    if result.returncode != 0:
        raise IntegrationError(
            f"could not pull the file: {(result.stderr or result.stdout).strip()[:200]}",
            service="phone",
        )
    return local_path


def push_file(cfg, local_path: pathlib.Path, remote_dir: str) -> str:
    """Copy a local file onto the phone. Returns the remote path written to.
    Public shared-storage directories (`/sdcard/Download`, `/sdcard/Pictures`,
    ...) work without root despite scoped storage — `adbd` runs as the
    `shell` UID, which keeps broad storage access; `/data/data/<pkg>/...`
    (app-private storage) does not, without root."""
    local_path = pathlib.Path(local_path)
    if not local_path.is_file():
        raise IntegrationError(f"no such local file: {local_path}", service="phone")
    if not available(cfg):
        raise IntegrationError(
            "adb is not installed, or not on PATH", service="phone",
            user_action=(
                "Install Android Platform Tools and add it to PATH, or set "
                "integrations.phone.adb_path in config.yml."
            ),
        )
    remote_path = f"{remote_dir.rstrip('/')}/{local_path.name}"

    args = [_resolved_adb_path(cfg)]
    if cfg.device_serial:
        args += ["-s", cfg.device_serial]
    args += ["push", str(local_path), remote_path]

    try:
        result = _exec(args, cfg, cfg.timeout_seconds * 3)
    except subprocess.TimeoutExpired as exc:
        raise IntegrationError(
            "the transfer timed out", service="phone", recoverable=True
        ) from exc
    except OSError as exc:
        raise IntegrationError(f"could not run adb: {exc}", service="phone") from exc

    if result.returncode != 0:
        result = _retry_if_disconnected(args, cfg, cfg.timeout_seconds * 3, result)

    if result.returncode != 0:
        raise IntegrationError(
            f"could not push the file: {(result.stderr or result.stdout).strip()[:200]}",
            service="phone",
        )
    return remote_path


def list_remote_dir(cfg, remote_dir: str) -> list[str]:
    """Files in a phone directory, for browsing. Unlike the private
    `_list_remote` (which swallows errors while trying several candidate
    directories in turn for a screenshot/voice-note pull), this surfaces a
    real error for a directory that does not exist or is not readable."""
    raw = _run(["shell", f"ls -la {_quote(remote_dir)}"], cfg)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def delete_remote_file(cfg, remote_path: str) -> None:
    """Delete one file on the phone. Never recursive (`rm`, not `rm -r`) —
    keeps a single call's blast radius to exactly one file."""
    remote_path = remote_path.strip()
    if not remote_path:
        raise IntegrationError("no remote path given", service="phone")
    _run(["shell", f"rm {_quote(remote_path)}"], cfg)


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


# ---------------------------------------------------------------- notifications
@dataclass(slots=True)
class Notification:
    package: str
    title: str | None
    text: str | None
    when: datetime | None

    def spoken(self) -> str:
        stamp = f" ({self.when.strftime('%d %b %H:%M')})" if self.when else ""
        heading = self.title or self.package
        body = f": {self.text}" if self.text else ""
        return f"{heading}{stamp}{body}"


_NOTIF_PKG = re.compile(r"pkg=(\S+)")
_NOTIF_TITLE = re.compile(r"android\.title=String\s*\((.*?)\)")
_NOTIF_TEXT = re.compile(r"android\.text=String\s*\((.*?)\)")
_NOTIF_WHEN = re.compile(r"postTime=(\d+)")


def notifications(cfg, limit: int = 20) -> list[Notification]:
    """Currently posted notifications, newest first — best-effort.
    `dumpsys notification --noredact` is a diagnostic text dump, not a
    stable schema; the exact field formatting can drift across Android
    versions and OEM skins, so a record that doesn't parse is skipped
    rather than failing the whole read. `--noredact` is required, or
    title/text come back as placeholder text instead of the real content.
    """
    raw = _run(["shell", "dumpsys notification --noredact"], cfg)
    found: list[Notification] = []
    # A zero-width lookahead split, not `\n\s*NotificationRecord\b` split-and-
    # drop-first: the very first record in the dump can sit at the start of
    # the string with no preceding newline (no preamble text before it),
    # which a newline-anchored split would silently drop.
    for block in re.split(r"(?=NotificationRecord\()", raw):
        if not block.startswith("NotificationRecord("):
            continue
        pkg_match = _NOTIF_PKG.search(block)
        if not pkg_match:
            continue
        title_match = _NOTIF_TITLE.search(block)
        text_match = _NOTIF_TEXT.search(block)
        when_match = _NOTIF_WHEN.search(block)
        when = None
        if when_match:
            try:
                when = datetime.fromtimestamp(int(when_match.group(1)) / 1000)
            except (ValueError, OSError, OverflowError):
                pass
        found.append(Notification(
            package=pkg_match.group(1),
            title=title_match.group(1) if title_match else None,
            text=text_match.group(1) if text_match else None,
            when=when,
        ))

    found.sort(key=lambda n: n.when or datetime.min, reverse=True)
    return found[:limit]


# -------------------------------------------------------------------- location
@dataclass(slots=True)
class Location:
    provider: str
    latitude: float
    longitude: float
    when: datetime | None = None
    accuracy_meters: float | None = None

    def spoken(self) -> str:
        stamp = f", {self.when.strftime('%d %b %H:%M')}" if self.when else ""
        return (
            f"Last known location ({self.provider}{stamp}): "
            f"{self.latitude:.5f}, {self.longitude:.5f}"
        )


_LOCATION_ROW = re.compile(r"Location\[(\w+)\s+([\-\d.]+),([\-\d.]+)[^\]]*\]")
_LOCATION_PROVIDER_PRIORITY = {"fused": 0, "gps": 1, "network": 2, "passive": 3}


def last_location(cfg) -> Location | None:
    """Best-available cached location — not a live fix. `dumpsys location`
    only reports whatever was last requested by some app; it can be stale by
    hours, or empty entirely (e.g. right after a reboot, or on a phone that
    has sat idle indoors). There is no non-root way to force a fresh GPS fix
    through plain `adb shell`. Returns `None` (not an error) when nothing is
    cached.
    """
    raw = _run(["shell", "dumpsys location"], cfg)
    candidates = []
    for match in _LOCATION_ROW.finditer(raw):
        provider, lat, lon = match.groups()
        try:
            candidates.append((provider.lower(), float(lat), float(lon)))
        except ValueError:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda c: _LOCATION_PROVIDER_PRIORITY.get(c[0], 99))
    provider, lat, lon = candidates[0]
    return Location(provider=provider, latitude=lat, longitude=lon)


# ------------------------------------------------------------------- raw shell
def run_shell(cfg, command: str, timeout: float | None = None) -> str:
    """Run any `adb shell` command verbatim and return its output — the
    escape hatch for anything the named phone functions above don't cover.

    Deliberately does not reuse `_run`'s failure heuristic: `_run` treats a
    nonzero exit code or the literal word "error:" in output as a command
    failure, which is right for adb's own known-shape commands but wrong
    for an arbitrary one — a command can legitimately return nonzero (a
    `grep` with no match) or print "error" in perfectly good output. This
    returns whatever happened, exit code included, and only raises for
    genuine infrastructure failures (adb missing, timeout, cannot exec) —
    transparency is the entire point of an unrestricted command.

    No shell-quoting is applied to `command` — it is passed to the phone's
    shell exactly as given. That is the explicit, consented-to nature of
    this tool; see `_quote()` for why every other function in this module
    quotes free text and this one deliberately does not.
    """
    if not cfg.raw_shell_enabled:
        raise NotConfiguredError(
            "phone",
            "Set integrations.phone.raw_shell_enabled to true in "
            "config.yml — this runs any shell command on the phone "
            "verbatim, understand the risk before turning it on.",
        )
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
    args += ["shell", command]

    try:
        result = _exec(args, cfg, timeout)
    except subprocess.TimeoutExpired as exc:
        raise IntegrationError(
            "the phone did not answer in time", service="phone", recoverable=True
        ) from exc
    except OSError as exc:
        raise IntegrationError(f"could not run adb: {exc}", service="phone") from exc

    output = _output_text(result)
    if _looks_disconnected(output):
        result = _retry_if_disconnected(args, cfg, timeout, result)
        output = _output_text(result)

    return f"[exit {result.returncode}]\n{output.strip()}"
