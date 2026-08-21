"""Skills: the manifest layer over the tool registry.

Two things matter most here, and get the most coverage: the manifests must
never drift from what is actually registered (the consistency test), and the
relevance filter must never silently hide a tool Claude needed (the
no-match-means-everything fallback).
"""

from types import SimpleNamespace

import pytest

from peter.agent import registry, skills
from peter.agent.skills import SkillManifest, register_skill


@pytest.fixture(autouse=True)
def _fresh():
    registry.reset_for_tests()
    yield
    registry.reset_for_tests()


def tool_filter_config(**kwargs):
    base = dict(enabled=True, max_skills=8, always_include=[])
    base.update(kwargs)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------- manifest
def test_register_and_get_skill():
    register_skill(SkillManifest(
        name="weather", version="1.0.0", description="Weather.",
        module="peter.skills.weather.tools", tools=("get_weather",),
    ))
    found = skills.get_skill("weather")
    assert found is not None
    assert found.tools == ("get_weather",)


def test_get_unknown_skill_returns_none():
    assert skills.get_skill("nonexistent") is None


def test_duplicate_skill_name_is_rejected():
    register_skill(SkillManifest(
        name="dup", version="1.0.0", description="First.", module="m1",
    ))
    with pytest.raises(ValueError, match="duplicate skill name"):
        register_skill(SkillManifest(
            name="dup", version="1.0.0", description="Second.", module="m2",
        ))


def test_unknown_permission_is_rejected():
    with pytest.raises(ValueError, match="unknown permission"):
        SkillManifest(
            name="bad", version="1.0.0", description="Bad.", module="m",
            permissions=("teleportation",),
        )


def test_all_skills_sorted_by_name():
    for name in ("zebra", "alpha", "middle"):
        register_skill(SkillManifest(
            name=name, version="1.0.0", description=name, module=f"m.{name}",
        ))
    assert [s.name for s in skills.all_skills()] == ["alpha", "middle", "zebra"]


def test_reset_for_tests_clears_everything():
    register_skill(SkillManifest(
        name="temp", version="1.0.0", description="Temp.", module="m",
    ))
    skills.reset_for_tests()
    assert skills.all_skills() == []


def test_registry_reset_also_clears_skills():
    """registry.reset_for_tests() must cascade — otherwise the existing
    "delete sys.modules, re-import" test pattern hits the duplicate-name
    check on re-import."""
    register_skill(SkillManifest(
        name="temp", version="1.0.0", description="Temp.", module="m",
    ))
    registry.reset_for_tests()
    assert skills.all_skills() == []


# --------------------------------------------------------------- consistency
def test_every_registered_tool_is_covered_by_exactly_one_skill():
    """The guarantee that matters most: manifests describe reality, not a
    stale snapshot of it. Runs against the full, real tool surface."""
    registry.load_all_tools()

    registered = {r.name for r in registry.all_records()}
    covered: dict[str, list[str]] = {}
    for m in skills.all_skills():
        for t in m.tools:
            covered.setdefault(t, []).append(m.name)

    missing = registered - covered.keys()
    extra = covered.keys() - registered
    duplicated = {t: owners for t, owners in covered.items() if len(owners) > 1}

    assert missing == set(), f"registered but not in any manifest: {missing}"
    assert extra == set(), f"in a manifest but not actually registered: {extra}"
    assert duplicated == {}, f"tool claimed by more than one skill: {duplicated}"


def test_every_skill_has_at_least_one_tool():
    registry.load_all_tools()
    empty = [m.name for m in skills.all_skills() if not m.tools]
    assert empty == [], f"skills with no tools declared: {empty}"


# ------------------------------------------------------------------ report
def test_skills_report_with_nothing_registered():
    assert skills.skills_report(SimpleNamespace()) == "No skills registered."


def test_skills_report_shows_enabled_and_not_configured(monkeypatch):
    register_skill(SkillManifest(
        name="always_on", version="1.0.0", description="On.",
        module="peter.tools.always_on",
    ))
    register_skill(SkillManifest(
        name="gated", version="1.0.0", description="Gated.",
        module="peter.tools.gated",
    ))
    monkeypatch.setattr(
        "peter.agent.registry.usable_modules",
        lambda config: ["peter.tools.always_on"],
    )

    text = skills.skills_report(SimpleNamespace())

    assert "always_on (1.0.0) [enabled]" in text
    assert "gated (1.0.0) [not configured]" in text


def test_skills_report_lists_tools_and_permissions(monkeypatch):
    register_skill(SkillManifest(
        name="weather", version="1.0.0", description="Weather.",
        module="peter.skills.weather.tools", permissions=("network",),
        tools=("get_weather",),
    ))
    monkeypatch.setattr(
        "peter.agent.registry.usable_modules",
        lambda config: ["peter.skills.weather.tools"],
    )

    text = skills.skills_report(SimpleNamespace())

    assert "tools: get_weather" in text
    assert "permissions: network" in text


# --------------------------------------------------------------- relevance
def _make_skill(name, tools, description=""):
    register_skill(SkillManifest(
        name=name, version="1.0.0", description=description or name,
        module=f"peter.tools.{name}", tools=tuple(tools),
    ))


def test_relevant_tool_names_matches_by_keyword():
    _make_skill("weather", ["get_weather"], "Current weather forecast.")
    _make_skill("mail", ["send_email"], "Read and send email.")

    result = skills.relevant_tool_names("what's the weather like today",
                                        tool_filter_config())

    assert result == {"get_weather"}


def test_relevant_tool_names_returns_none_on_no_match():
    _make_skill("weather", ["get_weather"])
    _make_skill("mail", ["send_email"])

    result = skills.relevant_tool_names(
        "completely unrelated gibberish zzz", tool_filter_config()
    )

    assert result is None


def test_relevant_tool_names_returns_none_with_no_skills_registered():
    assert skills.relevant_tool_names("weather", tool_filter_config()) is None


def test_always_include_skills_are_always_present():
    _make_skill("weather", ["get_weather"])
    _make_skill("system", ["lock_workstation"])

    result = skills.relevant_tool_names(
        "what's the weather", tool_filter_config(always_include=["system"])
    )

    assert result is not None
    assert "lock_workstation" in result
    assert "get_weather" in result


def test_always_include_present_even_on_total_no_match():
    """always_include should not depend on anything else matching."""
    _make_skill("weather", ["get_weather"])
    _make_skill("system", ["lock_workstation"])

    result = skills.relevant_tool_names(
        "zzz gibberish nonsense", tool_filter_config(always_include=["system"])
    )

    # Nothing scored, so the safe fallback (send everything) applies —
    # always_include does not change that outcome, it only guarantees
    # presence *within* a real filtered result.
    assert result is None


def test_max_skills_caps_the_number_of_matched_skills():
    for i in range(5):
        _make_skill(f"skill{i}", [f"tool{i}"], "shared keyword apple")

    result = skills.relevant_tool_names("apple", tool_filter_config(max_skills=2))

    assert result is not None
    assert len(result) == 2


def test_higher_scoring_skills_are_preferred_under_the_cap():
    _make_skill("weak", ["weak_tool"], "apple")
    _make_skill("strong", ["strong_tool"], "apple banana cherry")

    result = skills.relevant_tool_names(
        "apple banana cherry", tool_filter_config(max_skills=1)
    )

    assert result == {"strong_tool"}


# ---------------------------------------------------------------- config
def test_tool_filter_config_defaults():
    from peter.core.config import ToolFilterConfig

    cfg = ToolFilterConfig()
    assert cfg.enabled is False
    assert cfg.max_skills == 8
    assert "system" in cfg.always_include


# -------------------------------------------------------------------- tool
def test_list_skills_tool_reports_registered_skills(container):
    registry.reset_for_tests()
    from peter.skills.skills import tools as skill_tools  # noqa: F401
    from peter.skills.weather import tools as weather_tools  # noqa: F401

    result = registry.get_record("list_skills").raw_fn()

    assert "weather" in result
    assert "skills" in result


def test_list_skills_tool_omits_never_loaded_skills(container):
    """A module never imported this session never registers its skill —
    list_skills only reports what actually loaded, and says so in its own
    docstring. Here only skill_tools itself is imported, so nothing else
    should appear."""
    registry.reset_for_tests()
    from peter.skills.skills import tools as skill_tools  # noqa: F401

    result = registry.get_record("list_skills").raw_fn()

    assert "phone" not in result
    assert "weather" not in result
    assert "skills" in result


# --------------------------------------------------------------- brain hook
def test_brain_sends_every_tool_when_filter_disabled(config, store):
    from peter.agent.brain import Brain

    registry.reset_for_tests()
    from peter.skills.weather import tools as weather_tools  # noqa: F401

    config.agent.tool_filter.enabled = False
    brain = Brain(memory=store, config=config, provider=SimpleNamespace())

    tools = brain._turn_tools("what's the weather")

    assert {t.name for t in tools} == {r.name for r in registry.all_records()}


def test_brain_filters_tools_when_enabled_and_matched(config, store):
    from peter.agent.brain import Brain

    registry.reset_for_tests()
    from peter.skills.weather import tools as weather_tools  # noqa: F401
    from peter.skills.mail import tools as mail_tools  # noqa: F401

    config.agent.tool_filter.enabled = True
    config.agent.tool_filter.always_include = []
    config.agent.tool_filter.max_skills = 1
    brain = Brain(memory=store, config=config, provider=SimpleNamespace())

    tools = brain._turn_tools("what's the weather like")

    names = {t.name for t in tools}
    assert "get_weather" in names
    assert "send_email" not in names


def test_brain_falls_back_to_everything_on_no_match(config, store):
    from peter.agent.brain import Brain

    registry.reset_for_tests()
    from peter.skills.weather import tools as weather_tools  # noqa: F401

    config.agent.tool_filter.enabled = True
    brain = Brain(memory=store, config=config, provider=SimpleNamespace())

    tools = brain._turn_tools("zzz totally unrelated nonsense query")

    assert {t.name for t in tools} == {r.name for r in registry.all_records()}
