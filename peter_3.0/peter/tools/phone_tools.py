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
from peter.core.errors import PeterError
from peter.core.services import services
from peter.integrations.phone import adb


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
