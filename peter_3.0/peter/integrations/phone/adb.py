"""ADB: device discovery and the SMS content provider.

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
survives contact with actual SMS.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta

from peter.core.errors import IntegrationError

log = logging.getLogger(__name__)

_ROW = re.compile(r"^Row:\s*\d+\s+(.*)$")
# key=value, ending at the next "key=" or at end of line.
_FIELD = re.compile(r"(\w+)=(.*?)(?=,\s+\w+=|$)")
# A one-time code: 4-8 digits standing alone, not part of a longer number.
_CODE = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")

_OTP_HINTS = ("otp", "code", "verification", "verify", "password", "pin", "login")


@dataclass(slots=True)
class Sms:
    sender: str
    body: str
    when: datetime | None

    def spoken(self) -> str:
        stamp = self.when.strftime("%d %b %H:%M") if self.when else "unknown time"
        return f"{self.sender} ({stamp}): {self.body}"


def available(cfg) -> bool:
    return shutil.which(cfg.adb_path) is not None


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

    args = [cfg.adb_path]
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
        found.append(
            Sms(
                sender=fields.get("address", "unknown"),
                body=(fields.get("body") or "").strip(),
                when=when,
            )
        )

    found.sort(key=lambda m: m.when or datetime.min, reverse=True)
    return found[:limit]


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
