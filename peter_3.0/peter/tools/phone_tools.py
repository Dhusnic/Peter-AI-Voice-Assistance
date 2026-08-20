"""Phone tools — reading SMS over ADB.

Read-only, and only registered when integrations.phone.enabled is true, which
it is not by default. Both facts are deliberate: this needs USB debugging on
and the machine authorised on the handset, which is something you switch on
knowingly.

The one that earns its keep: `latest_code`. Peter can walk a checkout right up
to the payment screen but cannot legally complete it — RBI rules put the
two-factor step in your hands. Reading the code aloud so you can type it is
exactly as far as automation should reach into that.
"""

from __future__ import annotations

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
