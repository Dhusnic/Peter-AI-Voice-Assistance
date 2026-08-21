"""Building a provider from configuration.

The provider SDKs are imported lazily, inside each provider module, so that
having only an Anthropic key does not require the OpenAI and Google packages to
be installed and importable at startup.
"""

from __future__ import annotations

import logging

from peter.core.config import Config
from peter.core.errors import ConfigError, NotConfiguredError
from peter.llm.base import LLMProvider

log = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "openai", "gemini")

_KEY_HINTS = {
    "anthropic": ("ANTHROPIC_API_KEY", "console.anthropic.com/settings/keys"),
    "openai": ("OPENAI_API_KEY", "platform.openai.com/api-keys"),
    "gemini": ("GEMINI_API_KEY", "aistudio.google.com/apikey"),
}


def api_key_for(config: Config, provider: str) -> str:
    secrets = config.secrets
    return {
        "anthropic": secrets.anthropic_key,
        "openai": secrets.openai_key,
        "gemini": secrets.gemini_key,
    }.get(provider, "")


def available(config: Config) -> list[str]:
    """Providers that have a key configured."""
    return [p for p in PROVIDERS if api_key_for(config, p)]


def model_for(config: Config, provider: str) -> str:
    model = config.agent.models.get(provider, "")
    if not model:
        raise ConfigError(
            f"no model configured for {provider!r} — set agent.models.{provider} "
            "in config/config.yml"
        )
    return model


def build_provider(
    config: Config,
    system: str,
    provider: str | None = None,
    model: str | None = None,
) -> LLMProvider:
    """Construct the configured provider, or the one named.

    `model` overrides the configured model for this provider only. Used by the
    background, non-conversational callers — the subagents in particular,
    where the work is extraction rather than reasoning and a cheaper model is
    the right tool. It is never used for the main conversation, which always
    follows config.yml.
    """
    name = (provider or config.agent.provider).strip().lower()
    if name not in PROVIDERS:
        raise ConfigError(
            f"unknown provider {name!r} — agent.provider must be one of "
            f"{', '.join(PROVIDERS)}"
        )

    key = api_key_for(config, name)
    if not key:
        env_var, where = _KEY_HINTS[name]
        raise NotConfiguredError(
            name, f"Set {env_var} in .env. Get a key at {where}."
        )

    model = (model or "").strip() or model_for(config, name)
    agent = config.agent
    log.info("using %s/%s", name, model)

    if name == "anthropic":
        from peter.llm.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            model=model,
            system=system,
            api_key=key,
            max_tokens=agent.max_tokens,
            effort=agent.effort,
            cache_ttl=agent.cache_ttl,
            enable_web=agent.enable_web_tools,
            timeout=agent.retry.request_timeout_seconds,
        )

    if name == "openai":
        from peter.llm.providers.openai_provider import OpenAIProvider

        return OpenAIProvider(
            model=model,
            system=system,
            api_key=key,
            max_tokens=agent.max_tokens,
            effort=agent.effort,
            timeout=agent.retry.request_timeout_seconds,
        )

    from peter.llm.providers.gemini_provider import GeminiProvider

    return GeminiProvider(
        model=model,
        system=system,
        api_key=key,
        max_tokens=agent.max_tokens,
        auto_light_model=agent.gemini_auto.light_model,
        auto_heavy_model=agent.gemini_auto.heavy_model,
        auto_heavy_word_threshold=agent.gemini_auto.heavy_word_threshold,
        cache_enabled=agent.cache.enabled,
        cache_ttl_seconds=agent.cache.ttl_seconds,
        cache_min_tokens=agent.cache.min_tokens,
        fallbacks=agent.gemini_fallbacks,
        timeout=agent.retry.request_timeout_seconds,
    )
