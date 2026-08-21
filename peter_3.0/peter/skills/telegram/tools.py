"""Telegram tools — sending something to your own phone, deliberately.

Distinct from the automatic forwarding in `peter/core/notify.py`, which
mirrors proactive announcements. This is for when you ask: "send that address
to my phone", "message me the summary when you are done".

Only your own configured chats can be reached. There is no tool that takes an
arbitrary chat id, because there is no version of "message this stranger" that
is a good idea coming out of a voice assistant.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.agent.skills import SkillManifest, register_skill
from peter.core.errors import IntegrationError
from peter.core.services import services

register_skill(SkillManifest(
    name="telegram", version="1.0.0",
    description="Send something to your own phone via Telegram, deliberately.",
    module=__name__, permissions=("network",),
    tools=("send_to_phone", "telegram_status"),
))
from peter.integrations import telegram


@peter_tool(tier="write")
def send_to_phone(text: str) -> str:
    """Send a message to the user's own phone over Telegram.

    Use this when the user wants something *on their phone* rather than just
    spoken — an address, a summary, a list to take with them, a link.

    Args:
        text: The message to send. Plain text; keep it self-contained, since
            it arrives with no conversation around it.
    """
    config = services().config
    cfg = config.integrations.telegram
    if not telegram.configured(config):
        return (
            "Telegram is not set up. Set TELEGRAM_BOT_TOKEN in .env and add your "
            "chat id to integrations.telegram.allowed_chat_ids in config.yml — "
            "`python -m peter.main --telegram-setup` finds the id."
        )

    try:
        api = telegram.client(config)
    except IntegrationError as exc:
        return exc.spoken()

    sent = sum(1 for chat_id in cfg.allowed_chat_ids if api.send(chat_id, text))
    if not sent:
        return "Telegram would not take the message. It may be offline right now."
    return f"Sent to your phone ({sent} chat{'s' if sent != 1 else ''})."


@peter_tool(tier="read")
def telegram_status() -> str:
    """Report whether the Telegram bridge is connected, and to which chats."""
    config = services().config
    cfg = config.integrations.telegram
    if not cfg.enabled:
        return "Telegram is switched off in config.yml."
    if not config.secrets.has_telegram:
        return "No TELEGRAM_BOT_TOKEN is set in .env."
    if not cfg.allowed_chat_ids:
        return (
            "A bot token is set, but no chat is allowed yet, so nothing can "
            "reach it. Run `python -m peter.main --telegram-setup`."
        )

    try:
        username = telegram.client(config).me()
    except IntegrationError as exc:
        return f"Telegram is configured but unreachable: {exc}"

    forwarding = "on" if cfg.forward_notifications else "off"
    return (
        f"Connected as {username}, talking to {len(cfg.allowed_chat_ids)} chat(s). "
        f"Notification forwarding is {forwarding}."
    )
