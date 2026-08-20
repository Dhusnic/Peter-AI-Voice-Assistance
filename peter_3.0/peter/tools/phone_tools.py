"""Phone tools, over ADB.

Only registered when integrations.phone.enabled is true, which it is not by
default. Both facts are deliberate: this needs USB debugging on and the
machine authorised on the handset, which is something you switch on
knowingly.

Reading (SMS, calls, the screen) is `read` tier. Acting is narrow and stays
`write` tier: `open_link_on_phone` only ever opens a web page, and
`save_phone_screenshot` only ever copies a file that already exists on the
phone to this computer — nothing here can send a message or place a call as
you.

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
