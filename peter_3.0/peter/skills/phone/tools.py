"""Phone tools, over ADB.

Only registered when integrations.phone.enabled is true, which it is not by
default. Both facts are deliberate: this needs USB debugging on and the
machine authorised on the handset, which is something you switch on
knowingly.

Reading (SMS, calls, the screen) is `read` tier. Everything else — calls,
media, alarms — is `write` tier and auto-executes like any other write tool,
with one deliberate exception: `make_phone_call` is pulled back to `confirm`
in config.yml's standing_rules, because it connects immediately with no
on-device confirmation of its own, unlike answering or hanging up, which are
either time-sensitive (a confirm step would let the call go to voicemail
first) or trivially reversible.

`call_contact` is the one split *out* of that rule on purpose: it only ever
dials a number already saved under a real name in the phone's own contacts —
a materially different risk from a number the model transcribed from speech,
which is what `make_phone_call` is for. Splitting these into two tools rather
than one tool with an optional contact_name argument is what lets the policy
gate apply a different tier to each; the gate decides per tool, not per
argument, so a name-resolved call and a raw-digit call could not have shared
one tool and two different confirmation behaviours.

The one that earns its keep the most: `latest_code`. Peter can walk a
checkout right up to the payment screen but cannot legally complete it — RBI
rules put the two-factor step in your hands. Reading the code aloud so you
can type it is exactly as far as automation should reach into that.
`open_link_on_phone` closes the other half of that hand-off: it can put the
checkout page itself on your screen, not just tell you a code.
"""

from __future__ import annotations

from datetime import datetime

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.errors import PeterError
from peter.core.services import services
from peter.integrations.phone import adb

register_skill(SkillManifest(
    name="phone", version="1.0.0",
    description="Reads and controls the phone over ADB: SMS, calls, "
                 "Spotify, alarms, screenshots, voice notes, apps, files, "
                 "contacts, device settings, notifications, location, and "
                 "a raw shell escape hatch.",
    module=__name__, permissions=("phone",),
    tools=("read_sms", "latest_code", "phone_status", "read_call_log",
           "read_phone_screen", "open_link_on_phone", "save_phone_screenshot",
           "transcribe_phone_voice_note", "call_contact", "make_phone_call",
           "answer_phone_call", "hang_up_phone_call", "play_music_on_phone",
           "pause_music_on_phone", "skip_track_on_phone", "set_phone_alarm",
           "stop_phone_alarm", "list_phone_apps", "launch_phone_app",
           "uninstall_phone_app", "push_file_to_phone", "list_phone_files",
           "delete_phone_file", "list_phone_contacts", "add_phone_contact",
           "set_phone_wifi", "set_phone_bluetooth", "set_phone_airplane_mode",
           "set_phone_volume", "set_phone_brightness", "reboot_phone",
           "read_phone_notifications", "phone_location",
           "run_phone_shell_command"),
))


def _cfg():
    return services().config.integrations.phone


@peter_tool(tier="read")
def read_sms(limit: int = 0, hours: int = 24) -> str:
    """Read recent text messages from the phone.

    Args:
        limit: How many messages to return. 0 uses the configured default.
        hours: How far back to look.
    """
    cfg = _cfg()
    try:
        found = adb.messages(
            cfg, since_minutes=max(1, hours) * 60, limit=limit or cfg.sms_limit
        )
    except PeterError as exc:
        return exc.spoken()

    if not found:
        return f"No messages on the phone in the last {hours} hour(s)."
    return "\n".join(message.spoken() for message in found)


@peter_tool(tier="read")
def latest_code() -> str:
    """Read the most recent one-time code or OTP that arrived on the phone.

    Use this when the user is part-way through a login or a payment and needs
    the code. It only looks at messages from the last few minutes.
    """
    cfg = _cfg()
    try:
        result = adb.latest_code(cfg)
    except PeterError as exc:
        return exc.spoken()

    if result is None:
        return (
            f"No code has come in over the last {cfg.otp_window_minutes} minutes."
        )
    code, message = result
    spaced = " ".join(code)  # read digit by digit, or TTS says "one hundred..."
    return f"The code is {spaced}. It came from {message.sender}."


@peter_tool(tier="read")
def phone_status() -> str:
    """Report whether the phone is connected, and its battery level."""
    cfg = _cfg()
    state = adb.health(cfg)
    if not state.startswith("ok"):
        return f"Phone: {state}."
    try:
        return f"Phone connected. {adb.battery(cfg)}"
    except PeterError as exc:
        return f"Phone connected, but {exc}."


@peter_tool(tier="read")
def read_call_log(limit: int = 0, hours: int = 24) -> str:
    """Read recent calls from the phone: who, when, and how long.

    Args:
        limit: How many calls to return. 0 uses a default of 10.
        hours: How far back to look.
    """
    cfg = _cfg()
    try:
        found = adb.calls(cfg, since_minutes=max(1, hours) * 60, limit=limit or 10)
    except PeterError as exc:
        return exc.spoken()

    if not found:
        return f"No calls on the phone in the last {hours} hour(s)."
    return "\n".join(call.spoken() for call in found)


@peter_tool(tier="read")
def read_phone_screen(question: str = "") -> str:
    """Look at what is currently on the phone's screen and describe it, or
    answer a specific question about it.

    Takes a screenshot of the connected phone over ADB and hands it to the
    same vision model used for the desktop screen. Useful for reading a QR
    code, checking what an app is showing, or seeing an error the phone is
    displaying.

    Args:
        question: What to look for. Empty describes the screen generally.
    """
    from peter.llm import vision

    cfg = _cfg()
    try:
        data = adb.screenshot_bytes(cfg)
    except PeterError as exc:
        return exc.spoken()

    config = services().config
    path = config.screenshot_dir / f"phone-{datetime.now():%Y%m%d-%H%M%S}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    try:
        return vision.describe_image(path, question, config)
    except PeterError as exc:
        return exc.spoken()


@peter_tool(tier="write")
def open_link_on_phone(url: str) -> str:
    """Open a web link on the phone's screen.

    Use this to hand a checkout or login page to the phone for the OTP or UPI
    step Peter cannot legally complete itself — everything up to this can be
    prepared on the desktop, but the last tap is always yours.

    Args:
        url: The page to open. Must be http:// or https://.
    """
    cfg = _cfg()
    try:
        adb.open_url(cfg, url)
    except PeterError as exc:
        return exc.spoken()
    return "Opened on your phone."


@peter_tool(tier="write")
def save_phone_screenshot() -> str:
    """Copy the most recent screenshot from the phone onto this computer.

    Looks in the phone's screenshot folders (configurable as
    integrations.phone.pull_dirs) and copies the newest file found.
    """
    cfg = _cfg()
    config = services().config
    try:
        local_path = adb.pull_latest_file(cfg, cfg.pull_dirs, config.phone_pulls_dir)
    except PeterError as exc:
        return exc.spoken()
    return f"Saved {local_path.name} to the computer."


@peter_tool(tier="write")
def transcribe_phone_voice_note() -> str:
    """Pull the most recent voice note from the phone and transcribe it.

    Looks in WhatsApp's voice-note folder and common recorder-app folders
    (configurable as integrations.phone.voice_note_dirs), copies the newest
    file over ADB, and transcribes it with the same local faster-whisper
    pipeline already used for meeting recordings — the audio never leaves
    this machine for that step.
    """
    from peter.meeting_notes import transcribe

    cfg = _cfg()
    config = services().config
    try:
        local_path = adb.pull_latest_file(cfg, cfg.voice_note_dirs, config.phone_voice_notes_dir)
    except PeterError as exc:
        return exc.spoken()

    try:
        text = transcribe(local_path)
    except Exception as exc:
        return f"Saved {local_path.name}, but could not transcribe it: {exc}"

    if not text.strip():
        return f"Saved {local_path.name}, but transcription came back empty."
    return text


@peter_tool(tier="write")
def call_contact(contact_name: str) -> str:
    """Call a saved contact by name.

    Connects immediately — a real call, not a dial screen. Unlike
    make_phone_call, this does not ask for confirmation first: it only ever
    dials a number that is already saved under a real name in the phone's
    own contacts, which is a materially different risk from a number the
    model transcribed from speech. If the name matches more than one saved
    number, nothing is dialled and the matches are listed so you can ask the
    user which they meant — it never guesses between them.

    Always call this again — with a name, not the number from its own
    earlier result — once the user confirms who they meant, even mid
    conversation. "call Ancy" -> this lists candidates -> user says "yes,
    Ancy" -> call this again with contact_name="Ancy", not make_phone_call
    with the number this already returned. Reaching for make_phone_call once
    a number is sitting in the conversation is exactly the shortcut that
    defeats the point of having two tools: it re-adds the confirmation step
    a name-resolved call is supposed to skip.

    Args:
        contact_name: A saved contact's name, e.g. "Ancy" or "Ancy Mom" — an
            exact phrase match is tried first, falling back to matching on
            individual words, so a contact saved as bare "Ancy" is still
            found by "Ancy Mom".
    """
    cfg = _cfg()
    contact_name = contact_name.strip()
    if not contact_name:
        return "Give a contact name."

    try:
        matches = adb.find_contact(cfg, contact_name)
    except PeterError as exc:
        return exc.spoken()
    if not matches:
        return f"No saved contact matching {contact_name!r}."
    if len(matches) > 1:
        listing = "\n".join(f"{name} — {number}" for name, number in matches)
        return f"That matches {len(matches)} contacts — ask which one:\n{listing}"

    label, number = matches[0]
    try:
        adb.call_number(cfg, number)
    except PeterError as exc:
        return exc.spoken()
    return f"Calling {label}."


@peter_tool(tier="write")
def make_phone_call(number: str) -> str:
    """Call a phone number directly. Connects immediately — a real call, not
    a dial screen the user still has to tap to send.

    Only use this for a number the user actually spoke as digits. If they
    named a saved contact at any point in the conversation — even several
    turns ago, even if their number is now sitting in an earlier tool
    result — call call_contact with that name instead, every time, not this
    tool with the number. This one always asks for confirmation; call_contact
    doesn't need to, precisely because it re-resolves the name against saved
    contacts rather than trusting a number that's merely been mentioned.
    Passing that number here defeats the reason the two tools are split.

    Args:
        number: The number to call, e.g. "+91 90000 00000". Digits and
            ordinary phone-number punctuation only.
    """
    cfg = _cfg()
    try:
        adb.call_number(cfg, number)
    except PeterError as exc:
        return exc.spoken()
    return f"Calling {number}."


@peter_tool(tier="write")
def answer_phone_call() -> str:
    """Answer the phone's currently ringing incoming call."""
    cfg = _cfg()
    try:
        adb.answer_call(cfg)
    except PeterError as exc:
        return exc.spoken()
    return "Answered."


@peter_tool(tier="write")
def hang_up_phone_call() -> str:
    """End the phone's active call, or reject one that is currently ringing."""
    cfg = _cfg()
    try:
        adb.hang_up_call(cfg)
    except PeterError as exc:
        return exc.spoken()
    return "Call ended."


@peter_tool(tier="write")
def play_music_on_phone(query: str = "") -> str:
    """Play music on the phone through Spotify.

    Args:
        query: What to play, e.g. "lofi beats" or an artist name. Empty
            resumes whatever Spotify last had queued.
    """
    cfg = _cfg()
    try:
        adb.launch_spotify(cfg, query)
    except PeterError as exc:
        return exc.spoken()
    return f"Playing {query} on Spotify." if query.strip() else "Resumed Spotify."


@peter_tool(tier="write")
def pause_music_on_phone() -> str:
    """Pause whatever is currently playing on the phone — Spotify, if that
    is what is playing."""
    cfg = _cfg()
    try:
        adb.pause_media(cfg)
    except PeterError as exc:
        return exc.spoken()
    return "Paused."


@peter_tool(tier="write")
def skip_track_on_phone() -> str:
    """Skip to the next track in whatever is currently playing on the phone."""
    cfg = _cfg()
    try:
        adb.next_track(cfg)
    except PeterError as exc:
        return exc.spoken()
    return "Skipped to the next track."


@peter_tool(tier="write")
def set_phone_alarm(hour: int, minute: int, label: str = "", days: str = "") -> str:
    """Set an alarm on the phone's own clock app.

    Also how to set a phone-side reminder: give it a label describing what
    it is for — Android has no separate "reminder" concept, only a labelled
    alarm.

    Args:
        hour: Hour on a 24-hour clock, 0-23.
        minute: Minute, 0-59.
        label: What the alarm is for, e.g. "leave for the station".
        days: Days to repeat on, comma-separated ISO weekday numbers
            (Monday=1 .. Sunday=7), e.g. "1,2,3,4,5" for weekdays. Empty is
            a one-off alarm.
    """
    cfg = _cfg()
    try:
        adb.set_alarm_on_phone(cfg, hour, minute, label=label, days=days)
    except PeterError as exc:
        return exc.spoken()
    when = f"{hour:02d}:{minute:02d}"
    suffix = f" ({label.strip()})" if label.strip() else ""
    return f"Alarm set for {when} on the phone.{suffix}"


@peter_tool(tier="write")
def stop_phone_alarm() -> str:
    """Dismiss the phone's currently ringing alarm.

    Works with the phone's default clock app; some heavily customised OEM
    clock apps do not support this, in which case it fails honestly rather
    than silently doing nothing.
    """
    cfg = _cfg()
    try:
        adb.dismiss_phone_alarm(cfg)
    except PeterError as exc:
        return exc.spoken()
    return "Alarm dismissed."


# ------------------------------------------------------------ app management
@peter_tool(tier="read")
def list_phone_apps(third_party_only: bool = True) -> str:
    """List installed app package ids on the phone.

    There is no reliable way to resolve a package id to a human-readable app
    name without root, so this returns exact package ids (e.g.
    "com.spotify.music") — call this first if you need the exact id for
    launch_phone_app or uninstall_phone_app rather than guessing one.

    Args:
        third_party_only: True (default) lists only user-installed apps.
            False includes every system package too, a much longer list.
    """
    cfg = _cfg()
    try:
        packages = adb.list_apps(cfg, third_party_only=third_party_only)
    except PeterError as exc:
        return exc.spoken()
    if not packages:
        return "No apps found."
    return "\n".join(packages)


@peter_tool(tier="write")
def launch_phone_app(package: str) -> str:
    """Launch an app on the phone by its exact package id.

    Args:
        package: The exact package id, e.g. "com.spotify.music". Call
            list_phone_apps first if you don't already know it.
    """
    cfg = _cfg()
    try:
        adb.launch_app(cfg, package)
    except PeterError as exc:
        return exc.spoken()
    return f"Launched {package}."


@peter_tool(tier="write")
def uninstall_phone_app(package: str, keep_data: bool = False) -> str:
    """Uninstall an app from the phone by its exact package id. Irreversible
    without reinstalling — asks for confirmation first.

    Args:
        package: The exact package id, e.g. "com.example.app".
        keep_data: True to keep the app's data/cache in case of reinstall.
    """
    cfg = _cfg()
    try:
        adb.uninstall_app(cfg, package, keep_data=keep_data)
    except PeterError as exc:
        return exc.spoken()
    return f"Uninstalled {package}."


# --------------------------------------------------------------- file transfer
@peter_tool(tier="write")
def push_file_to_phone(local_path: str, remote_dir: str = "") -> str:
    """Copy a file from this computer onto the phone.

    Args:
        local_path: Path to the local file to copy.
        remote_dir: Phone directory to copy it into. Empty uses
            integrations.phone.push_default_dir (Downloads by default).
    """
    from pathlib import Path

    cfg = _cfg()
    try:
        remote_path = adb.push_file(
            cfg, Path(local_path), remote_dir.strip() or cfg.push_default_dir
        )
    except PeterError as exc:
        return exc.spoken()
    return f"Copied to {remote_path} on the phone."


@peter_tool(tier="read")
def list_phone_files(remote_dir: str) -> str:
    """List files in a directory on the phone.

    Args:
        remote_dir: Phone directory to list, e.g. "/sdcard/Download".
    """
    cfg = _cfg()
    try:
        entries = adb.list_remote_dir(cfg, remote_dir)
    except PeterError as exc:
        return exc.spoken()
    if not entries:
        return f"{remote_dir} is empty."
    return "\n".join(entries)


@peter_tool(tier="write")
def delete_phone_file(remote_path: str) -> str:
    """Delete one file on the phone. Irreversible — asks for confirmation
    first. Never recursive: only removes the exact file named.

    Args:
        remote_path: Full path to the file on the phone, e.g.
            "/sdcard/Download/old-file.txt".
    """
    cfg = _cfg()
    try:
        adb.delete_remote_file(cfg, remote_path)
    except PeterError as exc:
        return exc.spoken()
    return f"Deleted {remote_path} from the phone."


# ----------------------------------------------------------------- contacts
@peter_tool(tier="read")
def list_phone_contacts(query: str = "") -> str:
    """List saved phone contacts, optionally filtered by name.

    Args:
        query: Part of a contact's name to filter by. Empty lists every
            saved contact.
    """
    cfg = _cfg()
    try:
        matches = adb.list_contacts(cfg, query=query)
    except PeterError as exc:
        return exc.spoken()
    if not matches:
        return f"No saved contact matching {query!r}." if query.strip() else "No saved contacts."
    return "\n".join(f"{name} — {number}" for name, number in matches)


@peter_tool(tier="write")
def add_phone_contact(name: str, number: str) -> str:
    """Add a new contact to the phone.

    This writes directly to the phone's Contacts Provider and reads back
    what was saved before reporting success — on some phones (notably
    Samsung/Xiaomi), a contact created this way can be rejected or
    partially saved by the phone's own account-sync system; if that
    happens, this reports it rather than claiming success.

    Args:
        name: The contact's name.
        number: The contact's phone number.
    """
    cfg = _cfg()
    try:
        adb.add_contact(cfg, name, number)
    except PeterError as exc:
        return exc.spoken()
    return f"Added {name} ({number}) to phone contacts."


# ------------------------------------------------------------ device settings
@peter_tool(tier="write")
def set_phone_wifi(enabled: bool) -> str:
    """Turn the phone's WiFi on or off.

    Args:
        enabled: True to turn WiFi on, False to turn it off.
    """
    cfg = _cfg()
    try:
        adb.set_wifi(cfg, enabled)
    except PeterError as exc:
        return exc.spoken()
    return f"WiFi turned {'on' if enabled else 'off'}."


@peter_tool(tier="write")
def set_phone_bluetooth(enabled: bool) -> str:
    """Turn the phone's Bluetooth on or off.

    Not always reliable on Android 12 and newer — some phones require this
    to be done on-device instead of over ADB. This reports honestly if the
    phone did not confirm the change.

    Args:
        enabled: True to turn Bluetooth on, False to turn it off.
    """
    cfg = _cfg()
    try:
        confirmed = adb.set_bluetooth(cfg, enabled)
    except PeterError as exc:
        return exc.spoken()
    state = "on" if enabled else "off"
    if confirmed:
        return f"Bluetooth turned {state}."
    return (
        f"Tried to turn Bluetooth {state}, but the phone did not confirm it changed — "
        "some Android 12+ devices need this done on the phone itself."
    )


@peter_tool(tier="write")
def set_phone_airplane_mode(enabled: bool) -> str:
    """Turn the phone's airplane mode on or off. Asks for confirmation
    first: if the phone is connected over WiFi (wireless ADB), turning
    airplane mode on will likely disconnect it, and Peter cannot undo that
    from here.

    Args:
        enabled: True to turn airplane mode on, False to turn it off.
    """
    cfg = _cfg()
    try:
        adb.set_airplane_mode(cfg, enabled)
    except PeterError as exc:
        return exc.spoken()
    state = "on" if enabled else "off"
    if enabled and (cfg.wireless_address or "").strip():
        return (
            f"Airplane mode turned {state} — this may have disconnected the "
            "wireless ADB link; reconnect by USB or toggle airplane mode "
            "back off on the phone if so."
        )
    return f"Airplane mode turned {state}."


@peter_tool(tier="write")
def set_phone_volume(percent: int) -> str:
    """Set the phone's media (music) volume.

    Args:
        percent: Volume level, 0-100.
    """
    cfg = _cfg()
    try:
        adb.set_volume_percent(cfg, percent)
    except PeterError as exc:
        return exc.spoken()
    return f"Phone volume set to {percent}%."


@peter_tool(tier="write")
def set_phone_brightness(percent: int) -> str:
    """Set the phone's screen brightness. Turns off adaptive brightness so
    the value sticks.

    Args:
        percent: Brightness level, 0-100.
    """
    cfg = _cfg()
    try:
        adb.set_brightness_percent(cfg, percent)
    except PeterError as exc:
        return exc.spoken()
    return f"Phone brightness set to {percent}%."


@peter_tool(tier="write")
def reboot_phone() -> str:
    """Reboot the phone. Disruptive — interrupts whatever is running on it
    and drops the ADB connection (especially a wireless one) until it comes
    back up. Asks for confirmation first.
    """
    cfg = _cfg()
    try:
        adb.reboot(cfg)
    except PeterError as exc:
        return exc.spoken()
    return "Rebooting the phone."


# --------------------------------------------------------------- notifications
@peter_tool(tier="read")
def read_phone_notifications(limit: int = 20) -> str:
    """Read the phone's currently posted notifications — every app, not
    just SMS. Best-effort: notification content is read from a diagnostic
    system dump whose exact format can vary across Android versions.

    Args:
        limit: Maximum notifications to return, most recent first.
    """
    cfg = _cfg()
    try:
        found = adb.notifications(cfg, limit=limit)
    except PeterError as exc:
        return exc.spoken()
    if not found:
        return "No notifications found (or none could be read)."
    return "\n".join(n.spoken() for n in found)


# -------------------------------------------------------------------- location
@peter_tool(tier="read")
def phone_location() -> str:
    """Report the phone's last known location. This is a cached value, not
    a live GPS fix — it can be stale by minutes or hours, or unavailable
    entirely (e.g. right after a reboot)."""
    cfg = _cfg()
    try:
        location = adb.last_location(cfg)
    except PeterError as exc:
        return exc.spoken()
    if location is None:
        return "No location is currently cached on the phone."
    return location.spoken()


# ------------------------------------------------------------------- raw shell
@peter_tool(tier="write")
def run_phone_shell_command(command: str) -> str:
    """Run an arbitrary `adb shell` command on the phone and return its
    output verbatim. The escape hatch for anything none of the other phone
    tools cover — prefer a named tool above when one exists. Asks for
    confirmation first, every time: this is unrestricted, with no quoting
    or validation applied to `command`.

    Args:
        command: The shell command to run on the phone, e.g.
            "dumpsys battery" or "pm list packages -3".
    """
    cfg = _cfg()
    if not command.strip():
        return "Give a shell command to run."
    try:
        return adb.run_shell(cfg, command)
    except PeterError as exc:
        return exc.spoken()
