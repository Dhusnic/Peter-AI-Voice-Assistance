"""Configuration loading, validation, and the config.yml / .env boundary."""

import pytest
import yaml

from peter.core.config import Config, PolicyConfig, Secrets, load_config
from peter.core.errors import ConfigError


@pytest.fixture
def write_config(tmp_path):
    def _write(data: dict):
        path = tmp_path / "config.yml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return path
    return _write


# ------------------------------------------------------------------ loading
def test_every_provider_has_a_model_configured(config):
    """Switching provider must not need a second config edit."""
    for provider in ("anthropic", "openai", "gemini"):
        assert config.agent.models.get(provider), f"no model for {provider}"


def test_all_three_provider_keys_are_readable(monkeypatch):
    from peter.core.config import Secrets

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-value")
    monkeypatch.setenv("OPENAI_API_KEY", "o-value")
    monkeypatch.setenv("GEMINI_API_KEY", "g-value")
    secrets = Secrets.from_env()

    assert secrets.anthropic_key == "a-value"
    assert secrets.openai_key == "o-value"
    assert secrets.gemini_key == "g-value"
    assert secrets.any_llm_key is True


def test_google_api_key_is_accepted_for_gemini(monkeypatch):
    """GOOGLE_API_KEY is what AI Studio hands you in some flows."""
    from peter.core.config import Secrets

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "from-google-var")
    assert Secrets.from_env().gemini_key == "from-google-var"


def test_any_llm_key_is_false_with_none_set(monkeypatch):
    from peter.core.config import Secrets

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert Secrets.from_env().any_llm_key is False


def test_shipped_config_loads_and_validates(config):
    """If config.yml is broken, everything downstream is guesswork."""
    assert config.agent.provider in ("anthropic", "openai", "gemini")
    assert config.app.assistant_name
    assert config.integrations.briefing.hour == 7
    assert config.integrations.briefing.minute == 30


def test_defaults_fill_in_missing_sections(write_config):
    loaded = load_config(write_config({"app": {"user_name": "Ravi"}}))
    assert loaded.app.user_name == "Ravi"
    assert loaded.agent.models["anthropic"] == "claude-opus-5"  # default survived
    assert loaded.voice.tts.engine == "piper"


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yml")


def test_malformed_yaml_is_a_clear_error(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("app: {unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(path)


def test_top_level_list_is_rejected(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(path)


# --------------------------------------------------------------- validation
def test_invalid_value_names_the_field(write_config):
    """A typo should say which key is wrong, not fail three hours later."""
    with pytest.raises(ConfigError, match="agent.effort"):
        load_config(write_config({"agent": {"effort": "turbo"}}))


def test_invalid_briefing_time_is_rejected(write_config):
    with pytest.raises(ConfigError, match="briefing.time"):
        load_config(write_config({
            "integrations": {"briefing": {"time": "25:00"}}
        }))


def test_invalid_log_level_is_rejected(write_config):
    with pytest.raises(ConfigError, match="log_level"):
        load_config(write_config({"app": {"log_level": "CHATTY"}}))


def test_negative_max_tokens_is_rejected(write_config):
    with pytest.raises(ConfigError, match="max_tokens"):
        load_config(write_config({"agent": {"max_tokens": -1}}))


def test_unknown_policy_decision_is_rejected():
    with pytest.raises(Exception):
        PolicyConfig(default_tiers={"read": "maybe"})


# ---------------------------------------------------------- env overrides
def test_env_override_replaces_a_scalar(write_config, monkeypatch):
    monkeypatch.setenv("PETER__AGENT__PROVIDER", "gemini")
    loaded = load_config(write_config({"agent": {"provider": "anthropic"}}))
    assert loaded.agent.provider == "gemini"


def test_env_override_reaches_a_nested_section(write_config, monkeypatch):
    monkeypatch.setenv("PETER__VOICE__STT__DEVICE", "cuda")
    loaded = load_config(write_config({}))
    assert loaded.voice.stt.device == "cuda"


@pytest.mark.parametrize(
    "raw,expected",
    [("true", True), ("false", False), ("42", 42), ("0.5", 0.5), ("hello", "hello")],
)
def test_env_overrides_are_type_coerced(write_config, monkeypatch, raw, expected):
    from peter.core.config import _coerce

    assert _coerce(raw) == expected


def test_env_override_creates_a_missing_key(write_config, monkeypatch):
    monkeypatch.setenv("PETER__APP__LOG_LEVEL", "DEBUG")
    loaded = load_config(write_config({"app": {}}))
    assert loaded.app.log_level == "DEBUG"


def test_unrelated_env_vars_are_ignored(write_config, monkeypatch):
    monkeypatch.setenv("PATH_TO_SOMETHING", "x")
    monkeypatch.setenv("PETERSON", "y")
    assert load_config(write_config({})).agent.provider == "anthropic"


# ------------------------------------------------------------------ secrets
def test_secrets_come_from_env_not_yaml(write_config, monkeypatch):
    """A secret in config.yml would be committed. It must not be readable there."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fromenv")
    loaded = load_config(write_config({"anthropic_api_key": "sk-ant-fromyaml"}))
    assert loaded.secrets.anthropic_key == "sk-ant-fromenv"


def test_secrets_are_not_in_the_model_dump(write_config, monkeypatch):
    """A stray log of the config object must not leak the API key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-supersecret")
    loaded = load_config(write_config({}))

    assert "supersecret" not in str(loaded.model_dump())
    assert "supersecret" not in repr(loaded)


def test_secret_str_hides_the_value_in_repr(monkeypatch):
    monkeypatch.setenv("PETER_MAIL_APP_PASSWORD", "abcdefghijklmnop")
    secrets = Secrets.from_env()
    assert "abcdefghijklmnop" not in repr(secrets)
    assert secrets.mail_password == "abcdefghijklmnop"


def test_has_mail_requires_both_halves(monkeypatch):
    monkeypatch.setenv("PETER_MAIL_ADDRESS", "me@example.com")
    monkeypatch.delenv("PETER_MAIL_APP_PASSWORD", raising=False)
    assert Secrets.from_env().has_mail is False

    monkeypatch.setenv("PETER_MAIL_APP_PASSWORD", "pw")
    assert Secrets.from_env().has_mail is True


def test_has_google_requires_both_halves(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    assert Secrets.from_env().has_google is False


# -------------------------------------------------------------------- paths
def test_relative_data_dir_resolves_against_the_project(config):
    assert config.data_dir.is_absolute()
    assert config.db_path.parent == config.data_dir
    assert config.google_token_path.name == "google_token.json"
