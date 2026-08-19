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
    # Headed on purpose: you can see what it is doing and stop it, and headless
    # Chromium is the loudest automation signal there is.
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


class IntegrationsConfig(BaseModel):
    mail: MailConfig = Field(default_factory=MailConfig)
    google: GoogleConfig = Field(default_factory=GoogleConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    briefing: BriefingConfig = Field(default_factory=BriefingConfig)
    desktop: DesktopConfig = Field(default_factory=DesktopConfig)
    meeting_prep: MeetingPrepConfig = Field(default_factory=MeetingPrepConfig)
    inbox_digest: InboxDigestConfig = Field(default_factory=InboxDigestConfig)


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
