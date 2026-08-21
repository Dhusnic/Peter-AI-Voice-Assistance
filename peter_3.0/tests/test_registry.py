import pytest

from peter.agent import registry


@pytest.fixture(autouse=True)
def clean_registry():
    registry.reset_for_tests()
    yield
    registry.reset_for_tests()


def test_registration_records_name_and_tier():
    @registry.peter_tool(tier="read")
    def sample_tool(query: str) -> str:
        """Do a thing.

        Args:
            query: What to look for.
        """
        return f"looked for {query}"

    assert registry.tier_of("sample_tool") == "read"
    assert registry.describe() == [{"name": "sample_tool", "tier": "read"}]


def test_schema_is_derived_from_signature_and_docstring():
    @registry.peter_tool(tier="read")
    def documented(query: str, limit: int = 5) -> str:
        """Search for something.

        Args:
            query: The search text.
            limit: How many results.
        """
        return ""

    schema = registry.get_record("documented").sdk_tool.to_dict()
    assert "Search for something" in schema["description"]
    props = schema["input_schema"]["properties"]
    assert props["query"]["type"] == "string"
    assert props["limit"]["type"] == "integer"
    assert schema["input_schema"]["required"] == ["query"]


def test_tools_are_sorted_so_the_cache_prefix_is_stable():
    for name in ("zebra", "alpha", "middle"):
        def make(n):
            def fn() -> str:
                """A tool."""
                return n
            fn.__name__ = n
            return fn
        registry.peter_tool(tier="read")(make(name))

    assert [r.name for r in registry.all_records()] == ["alpha", "middle", "zebra"]


def test_duplicate_names_are_rejected():
    @registry.peter_tool(tier="read")
    def duplicated() -> str:
        """First."""
        return ""

    with pytest.raises(ValueError, match="duplicate tool name"):
        @registry.peter_tool(tier="read")
        def duplicated() -> str:  # noqa: F811
            """Second."""
            return ""


def test_invalid_tier_is_rejected():
    with pytest.raises(ValueError, match="tier must be one of"):
        registry.peter_tool(tier="superuser")


def test_calls_pass_through_the_interceptor():
    seen = []

    @registry.peter_tool(tier="write")
    def guarded(value: str) -> str:
        """Guarded.

        Args:
            value: Anything.
        """
        return f"ran {value}"

    def interceptor(name, tier, fn, kwargs):
        seen.append((name, tier, kwargs))
        return "intercepted"

    registry.set_interceptor(interceptor)
    result = registry.get_record("guarded").sdk_tool.call({"value": "x"})

    assert result == "intercepted"
    assert seen == [("guarded", "write", {"value": "x"})]


def test_without_an_interceptor_the_body_runs():
    @registry.peter_tool(tier="read")
    def direct(value: str) -> str:
        """Direct.

        Args:
            value: Anything.
        """
        return f"ran {value}"

    assert registry.get_record("direct").sdk_tool.call({"value": "x"}) == "ran x"


def test_sdk_tools_appends_server_tools_after_local_ones():
    @registry.peter_tool(tier="read")
    def local() -> str:
        """Local."""
        return ""

    server = {"type": "web_search_20260209", "name": "web_search"}
    tools = registry.sdk_tools([server])

    assert len(tools) == 2
    assert tools[-1] is server


def test_manifest_lists_names_with_tiers():
    @registry.peter_tool(tier="spend")
    def buy_thing() -> str:
        """Buy."""
        return ""

    assert registry.tool_manifest() == "- buy_thing [spend]"


def test_real_tool_modules_register_cleanly():
    """Import every shipped tool module and sanity-check the result."""
    registry.load_all_tools()
    records = registry.all_records()

    assert len(records) > 40, "expected the full phase-1 + phase-2 tool surface"
    assert {r.tier for r in records} <= {"read", "write", "spend"}

    by_name = {r.name: r.tier for r in records}
    # Anything destructive, irreversible, or arbitrary must not be read tier.
    for dangerous in ("delete_file", "run_powershell", "write_file", "move_file",
                      "send_email", "delete_email", "delete_calendar_event",
                      "archive_email", "create_calendar_event"):
        assert by_name[dangerous] == "write", f"{dangerous} is mis-tiered"
    for safe in ("get_current_time", "list_files", "read_file", "system_stats",
                 "check_email", "read_email", "check_calendar", "daily_briefing"):
        assert by_name[safe] == "read", f"{safe} should not need confirmation"


def test_every_shipped_tool_has_a_description():
    """The docstring IS the prompt — an undocumented tool is an unusable one."""
    registry.load_all_tools()
    for record in registry.all_records():
        schema = record.sdk_tool.to_dict()
        assert schema.get("description", "").strip(), f"{record.name} has no docstring"


# ============================================ offering only what can work
def _config_with(monkeypatch, *, mail: bool, google: bool):
    """The real config, with credentials forced on or off."""
    from peter.core.config import load_config

    config = load_config()
    monkeypatch.setattr(type(config.secrets), "has_mail", property(lambda _s: mail))
    monkeypatch.setattr(type(config.secrets), "has_google", property(lambda _s: google))
    return config


def test_unconfigured_integrations_are_not_offered(monkeypatch):
    """Every tool schema is re-sent on every API call. A module with no
    credentials is ~1,000 tokens per request describing an action that can
    only fail."""
    config = _config_with(monkeypatch, mail=False, google=False)
    chosen = registry.usable_modules(config)

    assert "peter.skills.mail.tools" not in chosen
    assert "peter.skills.calendar.tools" not in chosen


def test_configured_integrations_are_offered(monkeypatch):
    config = _config_with(monkeypatch, mail=True, google=True)
    chosen = registry.usable_modules(config)

    assert "peter.skills.mail.tools" in chosen
    assert "peter.skills.calendar.tools" in chosen


def test_each_integration_is_gated_independently(monkeypatch):
    config = _config_with(monkeypatch, mail=True, google=False)
    chosen = registry.usable_modules(config)

    assert "peter.skills.mail.tools" in chosen
    assert "peter.skills.calendar.tools" not in chosen


def test_tools_that_work_without_credentials_are_always_offered(monkeypatch):
    """Browser tools genuinely work on public pages with no saved login.
    Dropping a tool that can succeed would trade accuracy for tokens, which is
    the wrong way round."""
    config = _config_with(monkeypatch, mail=False, google=False)
    chosen = registry.usable_modules(config)

    for always in ("peter.skills.system.tools", "peter.skills.time.tools",
                   "peter.skills.memory.tools", "peter.skills.browser.tools",
                   "peter.skills.briefing.tools", "peter.skills.llm.tools"):
        assert always in chosen, always


def test_loading_without_a_config_still_offers_everything():
    """Callers with no config to hand — and the test suite — must keep seeing
    the full surface."""
    registry.reset_for_tests()
    registry.load_all_tools()
    assert len(registry.all_records()) > 55


def test_dropping_a_module_shrinks_the_prefix_but_stays_above_the_cache_floor(monkeypatch):
    """Gemini will not cache a prefix under 4,096 tokens, so trimming too far
    disables caching and costs MORE. This is the guard rail."""
    import json

    config = _config_with(monkeypatch, mail=False, google=False)
    registry.reset_for_tests()
    registry.load_all_tools(config=config)

    from peter.agent.prompts import system_prompt

    chars = sum(
        len(json.dumps({"n": s.name, "d": s.description, "p": s.parameters}))
        for s in registry.tool_specs()
    )
    approx_tokens = chars // 4 + len(system_prompt(config)) // 4
    assert approx_tokens > 4096, (
        f"prefix is ~{approx_tokens} tokens, under Gemini's 4,096 cache floor — "
        "caching would silently switch off and cost more"
    )

    registry.reset_for_tests()
