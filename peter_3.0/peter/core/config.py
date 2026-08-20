"""Configuration.

Two sources, with a hard rule about which is which:

    config/config.yml   everything that is not a secret. Committed to git.
    .env                secrets only. Never committed.

Keeping them apart means the whole configuration of the system is reviewable in
a diff, while nothing sensitive is. A settings block that mixes the two ends up
either leaking keys into the repo or hiding real behaviour in a file nobody
reads.

Every value is validated by a pydantic model, so a typo in config.yml fails at
startup with a message naming the field — not three hours later inside a tool.

Any value can be overridden per-run by an environment variable that spells out
its path with double underscores:

    PETER__AGENT__MODEL=claude-sonnet-5
    PETER__VOICE__STT__DEVICE=cuda
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator

from peter.core.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yml"
ENV_PATH = PROJECT_ROOT / ".env"

ENV_PREFIX = "PETER__"


# ============================================================== config.yml
class AppConfig(BaseModel):
    user_name: str = "Dhusnic"
    assistant_name: str = "Peter"
    data_dir: str = "./data"
    log_level: str = "INFO"
    log_format: Literal["rich", "json"] = "rich"

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        v = v.upper()
        if v not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError(f"invalid log_level {v!r}")
        return v


class RetryConfig(BaseModel):
    # How many times a provider call is attempted in total (1 = no retries).
    max_attempts: int = Field(default=5, ge=1, le=10)
    # Delay before the first retry, in seconds. Doubles each attempt after
    # that (capped at max_delay_seconds), with random jitter so it is never
    # exactly this number.
    base_delay_seconds: float = Field(default=10.0, gt=0)
    max_delay_seconds: float = Field(default=60.0, gt=0)


class GeminiAutoConfig(BaseModel):
    """Tuning for `agent.models.gemini: auto` — see peter/llm/router.py.

    Gemini-only for now: it is the one provider where a cheap and a strong
    model both exist in the same family with a large price gap, which is what
    makes per-turn routing worth the complexity.
    """

    light_model: str = Field(default="gemini-3.7-flash", min_length=1)
    heavy_model: str = Field(default="gemini-3.1-pro-preview", min_length=1)
    # A request longer than this many words escalates to the heavy model on
    # length alone, even with no complexity/high-stakes keyword match.
    heavy_word_threshold: int = Field(default=40, gt=0)


class CacheConfig(BaseModel):
    """Explicit server-side caching of the prompt prefix (Gemini).

    The system prompt and every tool schema are re-sent on every API call and
    are ~98% of Peter's token spend. Cached, they bill at a tenth of list
    price. Storage is charged per hour, so the cache is created on first use
    and allowed to lapse when idle rather than being held around the clock.
    """

    enabled: bool = True
    # Cache lifetime, refreshed on each use. Long enough to span a pause in
    # conversation, short enough that an idle Peter stops paying storage
    # promptly.
    ttl_seconds: int = Field(default=900, ge=60)
    # Gemini refuses to cache a prefix below 4,096 tokens. Attempting it just
    # wastes a round-trip, so check first and stay inline when under.
    min_tokens: int = Field(default=4096, ge=1)


class VisionConfig(BaseModel):
    """Looking at an image (the screen, a browser page, a file).

    A separate one-shot call rather than part of the conversation: an image is
    expensive and there is no reason to keep re-sending it on every later turn
    of the same conversation. See peter/llm/vision.py.
    """

    enabled: bool = True
    # Empty means "whatever agent.provider is". Every current model from all
    # three vendors reads images, so there is rarely a reason to pin one.
    provider: str = ""
    model: str = ""
    # Screens are wide; a 3840px-wide grab costs several times what a 1600px
    # one does and reads no better. Downscaled before sending.
    max_width: int = Field(default=1600, ge=320, le=4096)
    jpeg_quality: int = Field(default=80, ge=30, le=100)
    max_tokens: int = Field(default=1200, gt=0)


class SubagentConfig(BaseModel):
    """Parallel fan-out for reading several pages at once.

    Earns its place only where the alternative is flooding the main
    conversation with several thousand tokens of page text. See
    peter/agent/subagents.py.
    """

    enabled: bool = True
    # Empty means the configured provider's own model. A cheaper model is
    # usually right here: the subagent's job is extraction, not reasoning.
    provider: str = ""
    model: str = ""
    max_sites: int = Field(default=5, ge=2, le=10)
    max_chars_per_site: int = Field(default=4000, gt=200)
    # Page reads are serial (one browser, one page); only the model calls fan
    # out. This caps how many of those run at once.
    max_workers: int = Field(default=4, ge=1, le=8)


class BudgetConfig(BaseModel):
    """A daily spend ceiling, in rupees. See peter/spend.py.

    Zero disables it entirely, which is the default — a budget that trips
    unexpectedly mid-sentence is worse than no budget until you have looked at
    a week of real numbers and picked one deliberately.
    """

    daily_inr: float = Field(default=0.0, ge=0)
    # warn   say something once, then carry on
    # block  refuse further turns until midnight
    #
    # There is deliberately no "drop to the cheap model" option. Gemini's
    # `auto` routing already picks the model per turn and overwrites it on
    # every call, so a budget-imposed downgrade would silently not apply on
    # the very setup most likely to want it. A switch that works on two
    # vendors out of three is worse than not offering it.
    action: Literal["warn", "block"] = "warn"


class ToolFilterConfig(BaseModel):
    """Per-turn tool-list filtering by keyword relevance. See
    peter/agent/skills.py's relevant_tool_names().

    Off by default, deliberately — this trades the prompt cache's stable
    prefix for a smaller per-turn tool list, and at Peter's current tool
    count that trade is not obviously a win: every provider bills the
    cached-write more than the cached-read, and a per-turn-varying tool
    list means the system prefix (system prompt + every tool schema) can
    never hit that cache at all. Worth revisiting once the tool count grows
    enough, or the numbers from spend_report/performance_report say
    otherwise — not something to switch on speculatively.
    """

    enabled: bool = False
    # How many skills' worth of tools to send, on top of always_include.
    max_skills: int = Field(default=8, ge=1)
    # Skill names always sent regardless of match — the general-purpose
    # ones a turn is likely to need no matter what it's about.
    always_include: list[str] = Field(
        default_factory=lambda: ["system", "time", "memory"]
    )


class AgentConfig(BaseModel):
    # Which LLM vendor answers. Switchable at runtime with the
    # switch_llm_provider tool, or per-run with PETER__AGENT__PROVIDER.
    provider: Literal["anthropic", "openai", "gemini"] = "anthropic"
    # One model per vendor, so switching does not need a second edit. Gemini
    # accepts the literal string "auto" instead of a fixed model name — see
    # gemini_auto below.
    models: dict[str, str] = Field(
        default_factory=lambda: {
            "anthropic": "claude-opus-5",
            "openai": "gpt-5.6-terra",
            "gemini": "gemini-3.5-flash",
        }
    )
    gemini_auto: GeminiAutoConfig = Field(default_factory=GeminiAutoConfig)
    # model -> ordered list of same-tier substitutes to try when it returns a
    # recoverable error (rate limit, momentary outage). Gemini-only, and
    # deliberately same-price-tier: a fallback is a hedge against one model
    # being briefly overloaded, not licence to jump to a pricier one.
    gemini_fallbacks: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "gemini-3.7-flash": ["gemini-3.6-flash", "gemini-3.5-flash"],
        }
    )
    cache: CacheConfig = Field(default_factory=CacheConfig)
    fast_model: str = "claude-haiku-4-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    max_tokens: int = Field(default=8000, gt=0)
    max_history_messages: int = Field(default=40, gt=4)
    max_pause_restarts: int = Field(default=5, ge=0)
    cache_ttl: Literal["5m", "1h"] = "1h"
    enable_web_tools: bool = True
    retry: RetryConfig = Field(default_factory=RetryConfig)
    # Every provider bills in USD; usage summaries display in INR instead.
    # There is no live FX feed, so this is a manually maintained rate — update
    # it here occasionally rather than trusting it to stay exact forever.
    usd_to_inr_rate: float = Field(default=88.0, gt=0)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    subagent: SubagentConfig = Field(default_factory=SubagentConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    tool_filter: ToolFilterConfig = Field(default_factory=ToolFilterConfig)


class WakeConfig(BaseModel):
    model: str = "hey_jarvis"
    threshold: float = Field(default=0.5, gt=0, le=1)
    refractory_frames: int = Field(default=25, ge=0)


class SttConfig(BaseModel):
    model: str = "small.en"
    device: Literal["cpu", "cuda"] = "cpu"
    compute_type: str = "int8"
    silence_to_end: float = Field(default=0.8, gt=0)
    max_utterance: float = Field(default=20.0, gt=1)
    max_lead_in: float = Field(default=6.0, gt=0)
    min_speech: float = Field(default=0.3, gt=0)
    noise_margin: float = Field(default=3.0, gt=1)
    min_threshold: float = Field(default=0.006, gt=0)
    adaptive_noise: bool = True
    adaptive_rate: float = Field(default=0.1, gt=0, le=1)


class TtsConfig(BaseModel):
    engine: Literal["piper", "edge", "sapi"] = "piper"
    piper_voice: str = ""
    edge_voice: str = "en-IN-PrabhatNeural"
    sapi_rate: int = Field(default=185, gt=50)


class AudioConfig(BaseModel):
    input_device: int | None = None
    output_device: int | None = None


class VoiceConfig(BaseModel):
    wake: WakeConfig = Field(default_factory=WakeConfig)
    stt: SttConfig = Field(default_factory=SttConfig)
    tts: TtsConfig = Field(default_factory=TtsConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)


class PolicyConfig(BaseModel):
    default_tiers: dict[str, str] = Field(
        default_factory=lambda: {"read": "allow", "write": "allow",
                                 "spend": "handoff"}
    )
    confirm_timeout_seconds: float = Field(default=45.0, gt=0)
    standing_rules: dict[str, str] = Field(default_factory=dict)

    @field_validator("default_tiers", "standing_rules")
    @classmethod
    def _known_decisions(cls, v: dict[str, str]) -> dict[str, str]:
        allowed = {"allow", "confirm", "handoff", "deny"}
        for key, decision in v.items():
            if decision not in allowed:
                raise ValueError(
                    f"{key!r}: {decision!r} is not one of {sorted(allowed)}"
                )
        return v


class MailConfig(BaseModel):
    enabled: bool = True
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    inbox_folder: str = "INBOX"
    archive_folder: str = "[Gmail]/All Mail"
    trash_folder: str = "[Gmail]/Trash"
    # Where your own sent mail lands. Read by the waiting-on tracker to work
    # out which of your messages never got a reply.
    sent_folder: str = "[Gmail]/Sent Mail"
    fetch_limit: int = Field(default=25, gt=0, le=200)
    body_chars: int = Field(default=4000, gt=100)
    timeout_seconds: float = Field(default=30.0, gt=0)


class GoogleConfig(BaseModel):
    enabled: bool = True
    scopes: list[str] = Field(
        default_factory=lambda: [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/tasks",
        ]
    )
    calendar_id: str = "primary"
    tasklist_id: str = "@default"
    oauth_port: int = 0
    timeout_seconds: float = Field(default=30.0, gt=0)


class BrowserConfig(BaseModel):
    enabled: bool = True
    # Which browser engine Playwright drives for the scripted browser tools
    # (browse_page, check_price, browser_login, ...). This is always a
    # separate, Peter-owned browser instance with its own profile — never
    # your actual installed Chrome/Firefox/Edge, and unrelated to
    # desktop.preferred_browser below, which only affects plain links opened
    # for you to look at.
    #   chromium   default. Best-tested against anti-bot detection.
    #   firefox    Playwright's own bundled Firefox build, not your system one.
    # Switching this after the profile directory already has data in it will
    # fail to launch — the two engines' profile formats are incompatible.
    # Delete profile_dir (or point it elsewhere) after changing this.
    engine: Literal["chromium", "firefox"] = "chromium"
    # Headed on purpose: you can see what it is doing and stop it, and a
    # headless browser is the loudest automation signal there is.
    headless: bool = False
    profile_dir: str = "./data/browser_profile"
    default_timeout_seconds: float = Field(default=30.0, gt=0)
    # Minimum gap between requests to one domain. The most effective single
    # measure against getting an account flagged. Do not lower this.
    min_interval_seconds: float = Field(default=20.0, ge=0)
    max_page_chars: int = Field(default=6000, gt=200)
    # Sites Peter is allowed to open at all. Empty list means no restriction.
    allowed_domains: list[str] = Field(default_factory=list)


class BriefingConfig(BaseModel):
    enabled: bool = True
    time: str = "07:30"
    include: list[str] = Field(
        default_factory=lambda: ["calendar", "mail", "reminders", "todos"]
    )
    max_emails: int = Field(default=5, gt=0)
    max_events: int = Field(default=6, gt=0)

    @field_validator("time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        try:
            hour, minute = v.split(":")
            if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError(f"briefing.time must be HH:MM, got {v!r}") from None
        return v

    @property
    def hour(self) -> int:
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.time.split(":")[1])


class DesktopConfig(BaseModel):
    """Controlling what is already installed: browsers, media, local folders.

    Distinct from `browser` above, which is Playwright driving a scripted
    session Peter owns. This is Dhusnic's own browser, opened for them to look
    at.
    """

    enabled: bool = True
    # Which browser to launch links in. "default" follows the Windows setting.
    preferred_browser: str = "default"
    # Named browser profile, if you keep more than one. Firefox wants the
    # profile *name*; Chromium wants the directory ("Default", "Profile 1").
    browser_profile: str = ""
    # YouTube specifically opens here instead of preferred_browser — empty
    # means "no override, use preferred_browser like everything else".
    youtube_browser: str = ""
    youtube_browser_profile: str = ""
    # Spoken label -> Gmail account index, as in mail.google.com/mail/u/<n>.
    # An email address works too and is more robust if the order ever changes.
    gmail_accounts: dict[str, str] = Field(default_factory=dict)
    # Spoken name -> folder path, on top of the standard Windows folders.
    places: dict[str, str] = Field(default_factory=dict)
    # Which browsers' bookmarks to search. Empty means all installed ones.
    bookmark_sources: list[str] = Field(default_factory=list)
    # Named shortcuts for sites, so "open my mail" does not depend on a
    # bookmark existing.
    sites: dict[str, str] = Field(default_factory=dict)


class MeetingPrepConfig(BaseModel):
    """Proactive nudge before a calendar event. See peter/meeting_prep.py."""

    enabled: bool = True
    # How long before an event to speak up.
    lead_minutes: int = Field(default=10, gt=0, le=120)
    # How often to re-check the calendar. Keep at or below lead_minutes, or a
    # short meeting can slip through the gap between two polls unannounced.
    poll_interval_minutes: int = Field(default=5, gt=0, le=60)


class InboxDigestConfig(BaseModel):
    """Periodic "does anything need a reply" scan. See peter/inbox_digest.py.

    Read-only by design — this only ever reports, never drafts or sends.
    """

    enabled: bool = True
    poll_interval_minutes: int = Field(default=60, gt=0, le=1440)
    # Unread messages considered per check. Also the cap for the on-demand
    # inbox_digest tool.
    max_emails: int = Field(default=15, gt=0, le=100)


class TelegramConfig(BaseModel):
    """Reaching Peter — and being reached by Peter — away from the desk.

    The single biggest multiplier on every proactive feature here: a tray
    toast only exists if you are sitting in front of the machine, whereas a
    Telegram message finds you. See peter/telegram_bridge.py.
    """

    enabled: bool = True
    # Chat ids allowed to talk to Peter. **An empty list means nobody**, which
    # is the only safe default: a bot token is a public endpoint, and anyone
    # who guesses the bot's name can message it. Run
    # `python -m peter.main --telegram-setup` to find your own id.
    allowed_chat_ids: list[int] = Field(default_factory=list)
    # Seconds the getUpdates long poll is held open. Telegram supports up to
    # 50; this is one held HTTP request, not repeated polling, so a long value
    # is *cheaper* than a short one as well as more responsive.
    long_poll_seconds: int = Field(default=25, ge=1, le=50)
    # Forward proactive announcements (reminders, meeting prep, the inbox
    # digest, finished focus sessions, price alerts) to the same chats.
    forward_notifications: bool = True
    # Telegram rejects messages over 4096 characters.
    max_message_chars: int = Field(default=3800, ge=200, le=4096)
    timeout_seconds: float = Field(default=40.0, gt=0)


class PriceWatchConfig(BaseModel):
    """Standing price/stock watches on product pages. See peter/price_watch.py."""

    enabled: bool = True
    # How often the watch list is swept. Each page read is additionally spaced
    # by integrations.browser.min_interval_seconds per domain, so a sweep of
    # several watches on one site takes minutes by design — that spacing is
    # what keeps the account un-flagged.
    poll_interval_minutes: int = Field(default=90, ge=15, le=1440)
    max_watches: int = Field(default=20, ge=1, le=100)
    # Announce a fall of at least this percent even when no target was set.
    drop_percent: float = Field(default=5.0, gt=0, le=100)
    # Announce when something out of stock comes back.
    alert_on_restock: bool = True


class RecorderConfig(BaseModel):
    """Local meeting capture and transcription. See peter/meeting_notes.py.

    Everything here happens on this machine: the audio never leaves it, and
    faster-whisper is already installed for speech-to-text. Only the final
    summary is a model call, and only over the transcript.
    """

    enabled: bool = True
    # Prefer WASAPI loopback (what the speakers are playing — i.e. the other
    # people in the call). Falls back to the microphone when the installed
    # sounddevice build cannot do loopback, which captures your side only.
    capture_system_audio: bool = True
    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    max_minutes: int = Field(default=180, gt=0)
    # Whisper model for transcription, independent of the wake-word pipeline's
    # — a recording is transcribed in the background, so a slower, more
    # accurate model is affordable here in a way it is not for a live turn.
    stt_model: str = "small.en"
    # Start recording automatically when a meeting-prep nudge fires. Off by
    # default and deliberately so: recording a conversation without deciding
    # to is not a default anyone should inherit silently.
    auto_record_meetings: bool = False


class CiWatchConfig(BaseModel):
    enabled: bool = True
    poll_interval_minutes: int = Field(default=10, ge=2, le=180)
    # Empty means every branch the CLI reports on.
    branches: list[str] = Field(default_factory=list)
    runs_per_repo: int = Field(default=10, ge=1, le=50)


class DevConfig(BaseModel):
    """Git, pull requests and CI — the state of the work itself.

    `repos` is the whole switch: with none configured nothing here can do
    anything useful, so the tools are not registered at all and cost nothing.
    """

    enabled: bool = True
    # Spoken name -> path on disk. The first is the default when no repo is named.
    repos: dict[str, str] = Field(default_factory=dict)
    # Restrict `recent_commits` to your own commits. Empty means match the
    # repo's configured user.email.
    git_author: str = ""
    git_timeout_seconds: float = Field(default=20.0, gt=0)
    # The GitHub CLI, used for pull requests and CI runs. It carries its own
    # auth (`gh auth login`), so Peter never handles a GitHub token.
    gh_path: str = "gh"
    gh_timeout_seconds: float = Field(default=30.0, gt=0)
    ci_watch: CiWatchConfig = Field(default_factory=CiWatchConfig)


class WorklogConfig(BaseModel):
    """End-of-day record of what actually happened. See peter/worklog.py."""

    enabled: bool = True
    time: str = "18:30"
    days_back: int = Field(default=1, ge=1, le=14)

    @field_validator("time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        try:
            hour, minute = v.split(":")
            if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError(f"worklog.time must be HH:MM, got {v!r}") from None
        return v

    @property
    def hour(self) -> int:
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.time.split(":")[1])


class WaitingOnConfig(BaseModel):
    """Mail you sent that nobody answered. See peter/waiting_on.py."""

    enabled: bool = True
    # A message is only "waiting" once it has had a fair chance of a reply.
    quiet_days: int = Field(default=3, ge=1, le=60)
    # How far back in Sent to look at all.
    lookback_days: int = Field(default=21, ge=1, le=365)
    max_messages: int = Field(default=40, ge=1, le=200)


class WorkspaceConfig(BaseModel):
    """Saving and restoring a set of open applications. See peter/workspace.py."""

    enabled: bool = True
    # Executables never captured or relaunched, matched on filename. These are
    # either always running anyway or actively harmful to relaunch.
    ignore_executables: list[str] = Field(
        default_factory=lambda: [
            "explorer.exe", "searchhost.exe", "shellexperiencehost.exe",
            "textinputhost.exe", "applicationframehost.exe", "systemsettings.exe",
            "startmenuexperiencehost.exe", "widgets.exe", "lockapp.exe",
            "python.exe", "pythonw.exe", "cmd.exe", "conhost.exe",
        ]
    )
    max_apps: int = Field(default=25, ge=1, le=100)


class PhoneConfig(BaseModel):
    """Reading the phone over ADB — SMS, mainly, for one-time codes.

    Off by default: it needs USB debugging enabled and the machine authorised
    on the handset, which is a deliberate act, not a default state. Read-only
    on purpose — Peter never sends a message as you.
    """

    enabled: bool = False
    # Empty means "adb", found on PATH.
    adb_path: str = "adb"
    # Empty means the only attached device; required when more than one is.
    device_serial: str = ""
    sms_limit: int = Field(default=10, ge=1, le=100)
    # How recent a message must be to count as "the code that just arrived".
    otp_window_minutes: int = Field(default=10, ge=1, le=120)
    timeout_seconds: float = Field(default=20.0, gt=0)
    # Where `save_phone_screenshot` looks, in order, for the file to pull.
    # Stock Android's own screenshot folder plus the common vendor variant.
    pull_dirs: list[str] = Field(
        default_factory=lambda: [
            "/sdcard/Pictures/Screenshots",
            "/sdcard/DCIM/Screenshots",
        ]
    )
    # Package name Spotify controls (play_music_on_phone) launch by. Only
    # matters if you side-load a differently-packaged build.
    spotify_package: str = "com.spotify.music"
    # "<ip>:<port>" for ADB over Wi-Fi (Settings -> Developer options ->
    # Wireless debugging -> pair once, note the address it then shows).
    # Empty means USB-only. A wireless ADB session does not survive the
    # phone leaving and rejoining Wi-Fi, a reboot on either side, or
    # wireless debugging itself being toggled off and on — with this set,
    # every phone command retries once through `adb connect` if the device
    # turns out to be disconnected, instead of failing until you re-run
    # `adb connect` by hand. The IP can change if your router doesn't keep
    # it fixed; a DHCP reservation on the router avoids that.
    wireless_address: str = ""
    # Where `transcribe_phone_voice_note` looks, in order, for the newest
    # audio file. WhatsApp's own folder plus the common recorder-app ones —
    # there is no single standard location the way there is for screenshots.
    voice_note_dirs: list[str] = Field(
        default_factory=lambda: [
            "/sdcard/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Voice Notes",
            "/sdcard/Recordings",
            "/sdcard/Music/Recordings",
            "/sdcard/Recordings/Call",
        ]
    )


class ExpenseConfig(BaseModel):
    """A personal spend ledger built by parsing bank/UPI SMS. See peter/expenses.py.

    Heuristic, not authoritative: SMS formats vary widely across Indian banks
    and this errs toward under-counting — skipping a message it does not
    recognise — rather than guessing at one it half-understands. Treat it as
    a rough running total, not a substitute for the actual bank statement.
    Needs integrations.phone.enabled, since the source is SMS read over ADB.
    """

    enabled: bool = True


class DeliveryConfig(BaseModel):
    """A shipment tracker built by parsing courier SMS. See peter/deliveries.py.

    Same honesty caveat as expenses: courier SMS formats vary, and a message
    this does not recognise is silently skipped rather than guessed at.
    Needs integrations.phone.enabled, since the source is SMS read over ADB.
    """

    enabled: bool = True


class WeatherConfig(BaseModel):
    """Current weather via Open-Meteo. See peter/integrations/weather.py.

    Open-Meteo needs no API key and no signup, which is why it is the
    default rather than a metered provider — see .env.example for why every
    other integration in this file that talks to a paid API needs a secret
    and this one deliberately does not.
    """

    enabled: bool = True
    # A city name, geocoded once and cached in-process (coordinates do not
    # change) — or set latitude/longitude directly below to skip geocoding.
    location: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    units: Literal["metric", "imperial"] = "metric"
    timeout_seconds: float = Field(default=10.0, gt=0)


class RoutinesConfig(BaseModel):
    """Named chains of Peter's own tools, run as one voice command.

    Each routine is a list of steps under its name; each step names an
    existing tool and its arguments. "Good night" as one routine is worth
    more than the handful of tools it calls put together — orchestration
    over the existing tool surface, at the cost of zero new integrations.
    See peter/routines.py.

        routines:
          defs:
            good night:
              - tool: pause_music_on_phone
                args: {}
              - tool: lock_workstation
                args: {}

    A routine's steps run without re-confirming individually, even for a
    tool normally pulled into policy.standing_rules — writing the routine
    here by hand **is** the standing instruction, the same trust model
    standing_rules itself already uses. Spend-tier tools can never appear in
    a step regardless (there are none registered today; see peter/routines.py).
    """

    enabled: bool = True
    defs: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class NewsConfig(BaseModel):
    """Top headlines via Google News' public RSS feed — free, no API key.

    See peter/integrations/news.py. Same rationale as weather: a feed that
    needs no secret in .env is worth taking over a metered API for something
    this low-stakes.
    """

    enabled: bool = True
    # Empty means general top headlines. A topic/query narrows it, e.g.
    # "technology" or "Tamil Nadu".
    topic: str = ""
    max_items: int = Field(default=5, ge=1, le=20)
    region: str = "IN"
    language: str = "en"
    timeout_seconds: float = Field(default=10.0, gt=0)


class NotesConfig(BaseModel):
    """A personal journal — quick timestamped voice notes. See peter/notes.py.

    Distinct from memory's facts/preferences: a note is never injected into
    a future turn automatically, only recalled when asked.
    """

    enabled: bool = True


class DocsConfig(BaseModel):
    """Full-text search over folders you point Peter at. See peter/docs_index.py."""

    enabled: bool = True
    # Folders indexed on startup, so search works without asking first.
    folders: list[str] = Field(default_factory=list)
    extensions: list[str] = Field(
        default_factory=lambda: [
            ".md", ".txt", ".rst", ".py", ".js", ".ts", ".java", ".go",
            ".sql", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg",
        ]
    )
    # Files above this are almost always generated (lockfiles, bundles,
    # minified output) and indexing them buries the real content.
    max_file_kb: int = Field(default=512, gt=0)
    chunk_chars: int = Field(default=1200, ge=200, le=8000)
    max_files: int = Field(default=5000, gt=0)
    skip_directories: list[str] = Field(
        default_factory=lambda: [
            ".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
            "build", ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
        ]
    )


class IntegrationsConfig(BaseModel):
    mail: MailConfig = Field(default_factory=MailConfig)
    google: GoogleConfig = Field(default_factory=GoogleConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    briefing: BriefingConfig = Field(default_factory=BriefingConfig)
    desktop: DesktopConfig = Field(default_factory=DesktopConfig)
    meeting_prep: MeetingPrepConfig = Field(default_factory=MeetingPrepConfig)
    inbox_digest: InboxDigestConfig = Field(default_factory=InboxDigestConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    price_watch: PriceWatchConfig = Field(default_factory=PriceWatchConfig)
    recorder: RecorderConfig = Field(default_factory=RecorderConfig)
    dev: DevConfig = Field(default_factory=DevConfig)
    worklog: WorklogConfig = Field(default_factory=WorklogConfig)
    waiting_on: WaitingOnConfig = Field(default_factory=WaitingOnConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    phone: PhoneConfig = Field(default_factory=PhoneConfig)
    docs: DocsConfig = Field(default_factory=DocsConfig)
    expenses: ExpenseConfig = Field(default_factory=ExpenseConfig)
    deliveries: DeliveryConfig = Field(default_factory=DeliveryConfig)
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
    routines: RoutinesConfig = Field(default_factory=RoutinesConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    notes: NotesConfig = Field(default_factory=NotesConfig)


# =================================================================== .env
class Secrets(BaseModel):
    """Secrets, read only from the environment. Never logged, never in YAML.

    SecretStr keeps these out of tracebacks and repr() by default — a stray
    `print(config)` in a debugging session should not put your API key in the
    terminal scrollback.
    """

    anthropic_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    mail_address: str = ""
    mail_app_password: SecretStr = SecretStr("")
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    telegram_bot_token: SecretStr = SecretStr("")

    @classmethod
    def from_env(cls) -> "Secrets":
        return cls(
            anthropic_api_key=SecretStr(os.getenv("ANTHROPIC_API_KEY", "")),
            openai_api_key=SecretStr(os.getenv("OPENAI_API_KEY", "")),
            gemini_api_key=SecretStr(
                os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
            ),
            mail_address=os.getenv("PETER_MAIL_ADDRESS", ""),
            mail_app_password=SecretStr(os.getenv("PETER_MAIL_APP_PASSWORD", "")),
            google_client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
            google_client_secret=SecretStr(
                os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
            ),
            telegram_bot_token=SecretStr(os.getenv("TELEGRAM_BOT_TOKEN", "")),
        )

    # Convenience accessors, so callers never sprinkle .get_secret_value() around.
    @property
    def anthropic_key(self) -> str:
        return self.anthropic_api_key.get_secret_value()

    @property
    def openai_key(self) -> str:
        return self.openai_api_key.get_secret_value()

    @property
    def gemini_key(self) -> str:
        return self.gemini_api_key.get_secret_value()

    @property
    def any_llm_key(self) -> bool:
        return bool(self.anthropic_key or self.openai_key or self.gemini_key)

    @property
    def mail_password(self) -> str:
        return self.mail_app_password.get_secret_value()

    @property
    def google_secret(self) -> str:
        return self.google_client_secret.get_secret_value()

    @property
    def has_mail(self) -> bool:
        return bool(self.mail_address and self.mail_password)

    @property
    def has_google(self) -> bool:
        return bool(self.google_client_id and self.google_secret)

    @property
    def telegram_token(self) -> str:
        return self.telegram_bot_token.get_secret_value()

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_token)


# ================================================================== root
class Config(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)

    # Populated after construction; excluded from the model so it never
    # serialises into a log line or an error message.
    _secrets: Secrets

    def __init__(self, **data: Any):
        super().__init__(**data)
        object.__setattr__(self, "_secrets", Secrets.from_env())

    @property
    def secrets(self) -> Secrets:
        return self._secrets

    # ------------------------------------------------------------- paths
    @property
    def data_dir(self) -> Path:
        path = Path(self.app.data_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "peter.db"

    @property
    def audit_path(self) -> Path:
        return self.data_dir / "audit.jsonl"

    @property
    def google_token_path(self) -> Path:
        return self.data_dir / "google_token.json"

    @property
    def browser_profile_dir(self) -> Path:
        return self.resolve(self.integrations.browser.profile_dir)

    @property
    def screenshot_dir(self) -> Path:
        return self.data_dir / "screenshots"

    @property
    def docs_db_path(self) -> Path:
        """The document index lives in its own file.

        It is the one store that can grow to hundreds of megabytes and that
        you might reasonably want to delete and rebuild. Keeping it out of
        peter.db means doing so cannot take your memory with it.
        """
        return self.data_dir / "docs.db"

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def phone_pulls_dir(self) -> Path:
        return self.data_dir / "phone_pulls"

    @property
    def phone_voice_notes_dir(self) -> Path:
        return self.data_dir / "phone_voice_notes"

    def resolve(self, value: str) -> Path:
        """Resolve a config path value against the project root."""
        path = Path(value)
        return path if path.is_absolute() else PROJECT_ROOT / path


# ============================================================== loading
def _apply_env_overrides(data: dict) -> dict:
    """Fold PETER__SECTION__KEY environment variables into the parsed YAML."""
    for name, raw in os.environ.items():
        if not name.startswith(ENV_PREFIX):
            continue
        path = [part.lower() for part in name[len(ENV_PREFIX):].split("__") if part]
        if not path:
            continue

        cursor = data
        for key in path[:-1]:
            cursor = cursor.setdefault(key, {})
            if not isinstance(cursor, dict):
                raise ConfigError(
                    f"{name} cannot override {'.'.join(path)}: "
                    f"{key!r} is not a section"
                )
        cursor[path[-1]] = _coerce(raw)
    return data


def _coerce(raw: str) -> Any:
    """Turn an env-var string into the type YAML would have produced."""
    lowered = raw.strip().lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "none", ""):
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def load_config(path: Path | None = None) -> Config:
    """Read config.yml and .env, apply overrides, validate."""
    load_dotenv(ENV_PATH, override=False)

    config_path = path or CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(
            f"{config_path} not found. It ships with the repo — restore it from git."
        )

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{config_path} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"{config_path} must contain a mapping at the top level")

    data = _apply_env_overrides(data)

    try:
        return Config(**data)
    except ValidationError as exc:
        details = "\n".join(
            f"  {'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"invalid configuration in {config_path}:\n{details}") from exc


@lru_cache(maxsize=1)
def get_config() -> Config:
    """The process-wide configuration. Cached — call reload() in tests."""
    return load_config()


def reload_config() -> Config:
    get_config.cache_clear()
    return get_config()
