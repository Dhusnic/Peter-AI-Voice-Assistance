"""The multi-provider LLM layer.

Three vendors with three wire formats. The bugs live in the translation, so
these tests drive each provider with a fake SDK client and assert the exact
shape sent and the normalised shape returned.
"""

from types import SimpleNamespace

import pytest

from peter.core.errors import AuthError, IntegrationError
from peter.llm import loop, pricing
from peter.llm.base import (
    STOP_END,
    STOP_LENGTH,
    STOP_PAUSE,
    STOP_REFUSAL,
    STOP_TOOLS,
    LLMProvider,
    ProviderResponse,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)

SPEC = ToolSpec(
    name="get_time",
    description="Get the current time.",
    parameters={"type": "object", "properties": {"tz": {"type": "string"}}},
)


# ================================================================ tool specs
def test_spec_is_derived_from_the_anthropic_schema():
    spec = ToolSpec.from_anthropic_dict({
        "name": "search",
        "description": "  Search for something.  ",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
    })
    assert spec.name == "search"
    assert spec.description == "Search for something."
    assert spec.parameters["properties"]["q"]["type"] == "string"


def test_spec_survives_a_schema_with_no_parameters():
    spec = ToolSpec.from_anthropic_dict({"name": "ping", "description": "Ping."})
    assert spec.parameters == {"type": "object", "properties": {}}


def test_every_registered_tool_produces_a_usable_spec():
    """A spec missing a name or schema breaks every provider at once."""
    from peter.agent import registry

    registry.load_all_tools()
    specs = registry.tool_specs()

    assert len(specs) > 55
    for spec in specs:
        assert spec.name, "a tool has no name"
        assert spec.description.strip(), f"{spec.name} has no description"
        assert spec.parameters.get("type") == "object", f"{spec.name} schema is wrong"


def test_specs_keep_a_stable_order():
    """Tool order is part of the cached prefix on all three vendors."""
    from peter.agent import registry

    registry.load_all_tools()
    first = [s.name for s in registry.tool_specs()]
    second = [s.name for s in registry.tool_specs()]
    assert first == second == sorted(first)


# ================================================================== pricing
def test_known_models_are_priced():
    for model in ("claude-opus-5", "gpt-5.6-terra", "gemini-3.5-flash"):
        assert pricing.is_known(model), f"{model} missing from the price table"


def test_dated_snapshots_resolve_to_their_family():
    assert pricing.rates("gpt-5.5-2026-04-23") == pricing.rates("gpt-5.5")


def test_longest_prefix_wins():
    """gpt-5.6-terra must not resolve to a shorter gpt-5 entry."""
    assert pricing.rates("gpt-5.6-terra") == (2.00, 0.20, 12.00)


def test_unknown_model_costs_zero_rather_than_guessing():
    assert pricing.rates("some-future-model") is None
    assert pricing.estimate("some-future-model", Usage(input_tokens=1_000_000)) == 0.0


def test_cost_reflects_the_rate_card():
    cost = pricing.estimate("claude-opus-5", Usage(input_tokens=1_000_000))
    assert cost == pytest.approx(5.0)
    cost = pricing.estimate("claude-opus-5", Usage(output_tokens=1_000_000))
    assert cost == pytest.approx(25.0)


def test_cached_reads_are_cheaper_than_fresh_input():
    fresh = pricing.estimate("claude-opus-5", Usage(input_tokens=100_000))
    cached = pricing.estimate("claude-opus-5", Usage(cache_read=100_000))
    assert cached < fresh / 5


def test_cache_writes_cost_more_than_plain_input():
    plain = pricing.estimate("claude-opus-5", Usage(input_tokens=100_000))
    written = pricing.estimate("claude-opus-5", Usage(cache_write=100_000))
    assert written > plain


def test_gemini_flash_is_far_cheaper_than_opus():
    """The whole point of multi-provider: this comparison has to be visible."""
    work = Usage(input_tokens=100_000, output_tokens=10_000)
    assert pricing.estimate("gemini-3.7-flash", work) < pricing.estimate(
        "claude-opus-5", work
    ) / 4


# =================================================================== anthropic
def _anthropic_response(content, stop_reason="end_turn", **usage):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=usage.get("input_tokens", 10),
            output_tokens=usage.get("output_tokens", 5),
            cache_read_input_tokens=usage.get("cache_read", 0),
            cache_creation_input_tokens=usage.get("cache_write", 0),
        ),
    )


class FakeAnthropicClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def anthropic_provider(responses):
    from peter.llm.providers.anthropic_provider import AnthropicProvider

    client = FakeAnthropicClient(responses)
    provider = AnthropicProvider(
        model="claude-opus-5", system="SYSTEM", api_key="x", client=client
    )
    return provider, client


def test_anthropic_sends_a_cached_system_block():
    provider, client = anthropic_provider([
        _anthropic_response([SimpleNamespace(type="text", text="hi")])
    ])
    provider.add_user("hello")
    provider.complete([SPEC])

    system = client.calls[0]["system"][0]
    assert system["text"] == "SYSTEM"
    assert system["cache_control"]["type"] == "ephemeral"


def test_anthropic_tool_schema_uses_input_schema():
    provider, client = anthropic_provider([
        _anthropic_response([SimpleNamespace(type="text", text="hi")])
    ])
    provider.add_user("hello")
    provider.complete([SPEC])

    tool = client.calls[0]["tools"][0]
    assert tool["name"] == "get_time"
    assert "input_schema" in tool


def test_anthropic_reads_tool_calls_and_usage():
    provider, _ = anthropic_provider([
        _anthropic_response(
            [SimpleNamespace(type="tool_use", id="t1", name="get_time", input={"tz": "IST"})],
            stop_reason="tool_use",
            input_tokens=100, output_tokens=20, cache_read=500,
        )
    ])
    provider.add_user("what time is it")
    response = provider.complete([SPEC])

    assert response.stop_reason == STOP_TOOLS
    assert response.tool_calls == [ToolCall(id="t1", name="get_time", arguments={"tz": "IST"})]
    assert response.usage.cache_read == 500
    assert response.usage.cost_usd > 0


def test_anthropic_maps_pause_turn():
    provider, _ = anthropic_provider([
        _anthropic_response([SimpleNamespace(type="text", text="...")],
                            stop_reason="pause_turn")
    ])
    provider.add_user("search the web")
    assert provider.complete([SPEC]).stop_reason == STOP_PAUSE


def test_anthropic_maps_refusal_and_length():
    provider, _ = anthropic_provider([
        _anthropic_response([], stop_reason="refusal"),
        _anthropic_response([], stop_reason="max_tokens"),
    ])
    provider.add_user("x")
    assert provider.complete([SPEC]).stop_reason == STOP_REFUSAL
    provider.add_user("y")
    assert provider.complete([SPEC]).stop_reason == STOP_LENGTH


def test_anthropic_tool_results_use_tool_result_blocks():
    provider, _ = anthropic_provider([])
    provider.add_tool_results([ToolResult(id="t1", name="get_time", content="six")])

    message = provider.messages[-1]
    assert message["role"] == "user"
    assert message["content"][0]["type"] == "tool_result"
    assert message["content"][0]["tool_use_id"] == "t1"


def test_anthropic_trim_never_orphans_a_tool_result():
    provider, _ = anthropic_provider([])
    for _ in range(20):
        provider.add_user("a question")
        provider.messages.append({"role": "assistant", "content": []})
        provider.add_tool_results([ToolResult(id="x", name="t", content="r")])

    provider.trim(10)

    first = provider.messages[0]
    assert first["role"] == "user"
    assert isinstance(first["content"], str), "history must not start on a tool result"


# ===================================================================== openai
def _openai_response(output, usage=None, incomplete=None, output_text=""):
    return SimpleNamespace(
        output=output,
        output_text=output_text,
        usage=usage,
        incomplete_details=incomplete,
    )


class FakeOpenAIClient:
    """The SDK exposes `client.responses.create`, so the queue lives elsewhere."""

    def __init__(self, queued):
        self._queue = list(queued)
        self.calls = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._queue.pop(0)


def openai_provider(responses):
    from peter.llm.providers.openai_provider import OpenAIProvider

    client = FakeOpenAIClient(responses)
    provider = OpenAIProvider(
        model="gpt-5.6-terra", system="SYSTEM", api_key="x", client=client
    )
    return provider, client


def test_openai_puts_the_system_prompt_in_instructions():
    provider, client = openai_provider([
        _openai_response([], output_text="hi")
    ])
    provider.add_user("hello")
    provider.complete([SPEC])

    assert client.calls[0]["instructions"] == "SYSTEM"
    # It must not leak into the conversation as a message.
    assert all(i.get("role") != "system" for i in client.calls[0]["input"])


def test_openai_tool_schema_uses_parameters_not_input_schema():
    provider, client = openai_provider([_openai_response([], output_text="hi")])
    provider.add_user("hello")
    provider.complete([SPEC])

    tool = client.calls[0]["tools"][0]
    assert tool == {
        "type": "function",
        "name": "get_time",
        "description": "Get the current time.",
        "parameters": SPEC.parameters,
    }


def test_openai_parses_json_string_arguments():
    """Unlike Anthropic, OpenAI sends tool arguments as a JSON *string*."""
    call = SimpleNamespace(
        type="function_call", call_id="c1", name="get_time",
        arguments='{"tz": "IST"}',
        model_dump=lambda exclude_none=True: {
            "type": "function_call", "call_id": "c1", "name": "get_time",
            "arguments": '{"tz": "IST"}',
        },
    )
    provider, _ = openai_provider([_openai_response([call])])
    provider.add_user("what time is it")
    response = provider.complete([SPEC])

    assert response.stop_reason == STOP_TOOLS
    assert response.tool_calls[0].arguments == {"tz": "IST"}


def test_openai_malformed_arguments_do_not_crash_the_turn():
    from peter.llm.providers.openai_provider import _parse_arguments

    assert _parse_arguments("{not json") == {}
    assert _parse_arguments(None) == {}
    assert _parse_arguments('["a list"]') == {}
    assert _parse_arguments({"already": "a dict"}) == {"already": "a dict"}


def test_openai_subtracts_cached_tokens_from_input():
    """OpenAI counts cached tokens inside input_tokens; Anthropic reports them
    separately. They must mean the same thing once normalised."""
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=50,
        input_tokens_details=SimpleNamespace(cached_tokens=800),
    )
    provider, _ = openai_provider([_openai_response([], usage=usage, output_text="hi")])
    provider.add_user("hello")
    response = provider.complete([SPEC])

    assert response.usage.input_tokens == 200
    assert response.usage.cache_read == 800


def test_openai_tool_results_are_top_level_items():
    provider, _ = openai_provider([])
    provider.add_tool_results([ToolResult(id="c1", name="get_time", content="six")])

    item = provider.items[-1]
    assert item["type"] == "function_call_output"
    assert item["call_id"] == "c1"
    assert item["output"] == "six"


def test_openai_detects_truncation():
    provider, _ = openai_provider([
        _openai_response([], incomplete=SimpleNamespace(reason="max_output_tokens"))
    ])
    provider.add_user("write an essay")
    assert provider.complete([SPEC]).stop_reason == STOP_LENGTH


def test_openai_trim_does_not_start_on_a_tool_output():
    provider, _ = openai_provider([])
    for _ in range(20):
        provider.add_user("q")
        provider.items.append({"type": "function_call", "call_id": "x", "name": "t"})
        provider.add_tool_results([ToolResult(id="x", name="t", content="r")])

    provider.trim(10)
    assert provider.items[0].get("role") == "user"


# ===================================================================== gemini
class FakeCaches:
    """Stands in for client.caches. Records every create/update/delete."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.updated: list[str] = []
        self._n = 0

    def create(self, **kwargs):
        if self.fail:
            raise RuntimeError("cached content is too small")
        self.created.append(kwargs)
        self._n += 1
        return SimpleNamespace(
            name=f"cachedContents/fake{self._n}",
            usage_metadata=SimpleNamespace(total_token_count=5000),
        )

    def update(self, **kwargs):
        self.updated.append(kwargs.get("name", ""))

    def delete(self, **kwargs):
        self.deleted.append(kwargs.get("name", ""))

    @property
    def live(self) -> set[str]:
        return {c.name for c in ()} | (
            {f"cachedContents/fake{i}" for i in range(1, self._n + 1)}
            - set(self.deleted)
        )


class FakeGeminiClient:
    def __init__(self, responses, cache_fail: bool = False):
        self.responses = list(responses)
        self.calls = []
        self.models = SimpleNamespace(generate_content=self._generate)
        self.caches = FakeCaches(fail=cache_fail)

    def _generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _gemini_response(parts, finish_reason="STOP", usage=None, text=""):
    candidate = SimpleNamespace(
        content=SimpleNamespace(role="model", parts=parts),
        finish_reason=finish_reason,
    )
    response = SimpleNamespace(
        candidates=[candidate], usage_metadata=usage, text=text
    )
    return response


def gemini_provider(responses):
    """Caching off, so these tests assert the inline wire format."""
    from peter.llm.providers.gemini_provider import GeminiProvider

    client = FakeGeminiClient(responses)
    provider = GeminiProvider(
        model="gemini-3.5-flash", system="SYSTEM", api_key="x", client=client,
        cache_enabled=False,
    )
    return provider, client


def gemini_cached_provider(responses, cache_fail=False, **kw):
    from peter.llm.providers.gemini_provider import GeminiProvider

    client = FakeGeminiClient(responses, cache_fail=cache_fail)
    provider = GeminiProvider(
        model=kw.pop("model", "gemini-3.7-flash"), system="SYSTEM", api_key="x",
        client=client, cache_enabled=True, cache_ttl_seconds=900, **kw,
    )
    return provider, client


def test_gemini_puts_system_in_the_config():
    provider, client = gemini_provider([_gemini_response([], text="hi")])
    provider.add_user("hello")
    provider.complete([SPEC])

    config = client.calls[0]["config"]
    assert config.system_instruction == "SYSTEM"


def test_gemini_disables_automatic_function_calling():
    """The SDK will execute Python callables itself, which would route around
    Peter's permission gate entirely. That must stay off."""
    provider, client = gemini_provider([_gemini_response([], text="hi")])
    provider.add_user("hello")
    provider.complete([SPEC])

    config = client.calls[0]["config"]
    assert config.automatic_function_calling.disable is True


def test_gemini_passes_raw_json_schema():
    """The typed Schema converter silently drops constructs it cannot model."""
    provider, client = gemini_provider([_gemini_response([], text="hi")])
    provider.add_user("hello")
    provider.complete([SPEC])

    declaration = client.calls[0]["config"].tools[0].function_declarations[0]
    assert declaration.name == "get_time"
    assert declaration.parameters_json_schema == SPEC.parameters


def test_gemini_synthesises_call_ids():
    """Gemini returns no call id; the shared loop needs one to key on."""
    part = SimpleNamespace(
        function_call=SimpleNamespace(name="get_time", args={"tz": "IST"}, id=None),
        text=None,
    )
    provider, _ = gemini_provider([_gemini_response([part])])
    provider.add_user("what time is it")
    response = provider.complete([SPEC])

    assert response.stop_reason == STOP_TOOLS
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id, "a call id must always be present"
    assert response.tool_calls[0].name == "get_time"
    assert response.tool_calls[0].arguments == {"tz": "IST"}


def test_gemini_maps_safety_stops_to_refusal():
    provider, _ = gemini_provider([_gemini_response([], finish_reason="SAFETY")])
    provider.add_user("x")
    assert provider.complete([SPEC]).stop_reason == STOP_REFUSAL


def test_gemini_tool_results_go_under_the_user_role():
    provider, _ = gemini_provider([])
    provider.add_tool_results([ToolResult(id="g1", name="get_time", content="six")])

    content = provider.contents[-1]
    assert content.role == "user"
    assert content.parts[0].function_response is not None


def test_gemini_usage_separates_cached_tokens():
    usage = SimpleNamespace(
        prompt_token_count=1000,
        candidates_token_count=50,
        cached_content_token_count=600,
    )
    provider, _ = gemini_provider([_gemini_response([], usage=usage, text="hi")])
    provider.add_user("hello")
    response = provider.complete([SPEC])

    assert response.usage.input_tokens == 400
    assert response.usage.cache_read == 600


def test_gemini_classifies_a_dns_failure_as_recoverable():
    """A DNS lookup failure ('[Errno 11001] getaddrinfo failed' on Windows)
    used to fall through to the non-recoverable catch-all, so a dropped wifi
    connection was never retried even though it always should be."""
    import httpx

    provider, _ = gemini_provider([])
    translated = provider._translate(httpx.ConnectError("[Errno 11001] getaddrinfo failed"))

    assert isinstance(translated, IntegrationError)
    assert translated.recoverable is True
    assert "internet connection" in translated.user_action.lower()


def test_gemini_classifies_an_unwrapped_dns_error_by_text_as_a_fallback():
    """Belt and braces: if some future SDK version lets a raw OS error through
    unwrapped instead of an httpx exception, text matching still catches it."""
    provider, _ = gemini_provider([])
    translated = provider._translate(Exception("[Errno 11001] getaddrinfo failed"))

    assert isinstance(translated, IntegrationError)
    assert translated.recoverable is True


def test_gemini_classifies_a_5xx_status_code_as_recoverable():
    provider, _ = gemini_provider([])
    translated = provider._translate(SimpleNamespace(code=503))
    assert translated.recoverable is True


def test_gemini_classifies_a_401_status_code_as_an_auth_error():
    provider, _ = gemini_provider([])
    translated = provider._translate(SimpleNamespace(code=401))
    assert isinstance(translated, AuthError)


def test_gemini_classifies_a_429_status_code_as_recoverable():
    provider, _ = gemini_provider([])
    translated = provider._translate(SimpleNamespace(code=429))
    assert translated.recoverable is True


# ============================================================ task router
def test_router_classifies_a_short_plain_request_as_light():
    from peter.llm import router

    result = router.classify("what time is it")
    assert result.tier == router.LIGHT


def test_router_classifies_a_reasoning_keyword_as_heavy():
    from peter.llm import router

    result = router.classify("can you debug why this keeps happening")
    assert result.tier == router.HEAVY


def test_router_does_not_escalate_on_common_conversational_words_alone():
    """'why', 'explain', 'plan' are some of the most common words in ordinary
    speech. Treating any of them as a complexity signal on their own would
    route a large share of routine turns to the expensive model — the exact
    opposite of what routing exists to do. See the comment in router.py."""
    from peter.llm import router

    for text in (
        "why is my cpu usage so high",
        "explain this error to me",
        "what's my plan for today",
        "why does this keep happening",
    ):
        assert router.classify(text).tier == router.LIGHT, text


def test_router_does_not_escalate_a_todo_or_reminder_for_what_it_mentions():
    """A todo/reminder item is bookkeeping, not the action it names — 'add buy
    milk to my todo list' must not escalate just because it contains 'buy'.
    Caught live: this was the actual bug reported after the first version of
    the router shipped."""
    from peter.llm import router

    for text in (
        "add buy milk to my todo list",
        "remind me to pay the electricity bill",
        "add pay rent to my todo list",
        "set a reminder to buy groceries tomorrow",
    ):
        result = router.classify(text)
        assert result.tier == router.LIGHT, text
        assert result.reason == "routine list/reminder bookkeeping"


def test_router_still_escalates_a_genuine_purchase_request():
    """The bookkeeping exception must not swallow real purchase intent."""
    from peter.llm import router

    for text in ("buy me a laptop from amazon", "please purchase this item now"):
        assert router.classify(text).tier == router.HEAVY, text


def test_router_classifies_a_high_stakes_verb_as_heavy_even_when_short():
    from peter.llm import router

    result = router.classify("cancel my order", heavy_word_threshold=40)
    assert result.tier == router.HEAVY


def test_router_escalates_on_length_alone():
    from peter.llm import router

    long_text = " ".join(["word"] * 41)
    assert router.classify(long_text, heavy_word_threshold=40).tier == router.HEAVY
    short_text = " ".join(["word"] * 39)
    assert router.classify(short_text, heavy_word_threshold=40).tier == router.LIGHT


def test_router_is_case_insensitive():
    from peter.llm import router

    assert router.classify("please DEBUG this for me").tier == router.HEAVY


def test_router_treats_empty_text_as_light():
    from peter.llm import router

    assert router.classify("").tier == router.LIGHT


# ===================================================== gemini smart routing
def gemini_auto_provider(responses):
    from peter.llm.providers.gemini_provider import GeminiProvider

    client = FakeGeminiClient(responses)
    provider = GeminiProvider(
        model="auto", system="SYSTEM", api_key="x", client=client,
        auto_light_model="gemini-3.7-flash",
        auto_heavy_model="gemini-3.1-pro-preview",
        auto_heavy_word_threshold=10,
    )
    return provider, client


def test_a_fixed_model_name_never_enters_auto_mode():
    provider, _ = gemini_provider([_gemini_response([], text="hi")])
    provider.add_user("please explain and compare these two options")
    provider.complete([SPEC])
    assert provider.model == "gemini-3.5-flash"


def test_auto_mode_starts_pointed_at_the_light_model():
    """Before any turn happens, --health and cost tracking need a real model
    name, not the literal string 'auto'."""
    provider, _ = gemini_auto_provider([])
    assert provider.auto is True
    assert provider.model == "gemini-3.7-flash"


def test_auto_routes_a_routine_request_to_the_light_model():
    provider, client = gemini_auto_provider([_gemini_response([], text="six pm")])
    provider.add_user("what time is it")
    provider.complete([SPEC])

    assert provider.model == "gemini-3.7-flash"
    assert client.calls[0]["model"] == "gemini-3.7-flash"


def test_auto_routes_a_reasoning_request_to_the_heavy_model():
    provider, client = gemini_auto_provider([_gemini_response([], text="...")])
    provider.add_user("can you explain and compare these two plans for me")
    provider.complete([SPEC])

    assert provider.model == "gemini-3.1-pro-preview"
    assert client.calls[0]["model"] == "gemini-3.1-pro-preview"
    assert provider.last_route_reason


def test_auto_routes_a_high_stakes_request_to_the_heavy_model_even_if_short():
    provider, client = gemini_auto_provider([_gemini_response([], text="ok")])
    provider.add_user("delete this file")
    provider.complete([SPEC])

    assert provider.model == "gemini-3.1-pro-preview"


def test_auto_routing_ignores_injected_now_and_memory_tags():
    """A long <memory> block should not itself trigger the heavy model —
    only the actual request the user made should count."""
    provider, client = gemini_auto_provider([_gemini_response([], text="ok")])
    provider.add_user(
        "<now>Monday, 01 January 2026, 10:00 AM</now>\n\n"
        "<memory>\nStanding preferences:\n- reply briefly\nPossibly relevant "
        "facts:\n- home_city: Coimbatore\n- college: PSG Tech\n</memory>\n\n"
        "what time is it"
    )
    provider.complete([SPEC])
    assert provider.model == "gemini-3.7-flash"


def test_auto_mode_bills_at_the_rate_of_the_model_actually_used():
    usage = SimpleNamespace(
        prompt_token_count=1_000_000, candidates_token_count=1_000_000,
        cached_content_token_count=0,
    )
    provider, _ = gemini_auto_provider(
        [_gemini_response([], text="...", usage=usage)]
    )
    provider.add_user("please explain and compare these two options in detail")
    response = provider.complete([SPEC])

    # gemini-3.1-pro-preview is 2.00/12.00 per Mtok, not flash's 0.75/3.75.
    assert response.usage.cost_usd == pytest.approx(2.00 + 12.00)


def test_auto_health_names_both_candidate_models():
    provider, _ = gemini_auto_provider([])
    assert provider.health() == "gemini/auto (currently gemini-3.7-flash)"


# ======================================================== explicit prompt cache
def test_cache_holds_the_system_prompt_and_tools():
    provider, client = gemini_cached_provider([_gemini_response([], text="hi")])
    provider.add_user("hello")
    provider.complete([SPEC])

    assert len(client.caches.created) == 1
    created = client.caches.created[0]["config"]
    assert created.system_instruction == "SYSTEM"
    assert created.tools[0].function_declarations[0].name == "get_time"


def test_a_cached_request_does_not_resend_the_prefix():
    """The whole saving. Re-sending system/tools alongside cached_content is
    not just wasteful — the API rejects the request outright."""
    provider, client = gemini_cached_provider([_gemini_response([], text="hi")])
    provider.add_user("hello")
    provider.complete([SPEC])

    sent = client.calls[0]["config"]
    assert sent.cached_content == "cachedContents/fake1"
    assert sent.system_instruction is None
    assert sent.tools is None


def test_the_cache_is_created_once_and_reused():
    provider, client = gemini_cached_provider(
        [_gemini_response([], text="a"), _gemini_response([], text="b")]
    )
    for _ in range(2):
        provider.add_user("hello")
        provider.complete([SPEC])

    assert len(client.caches.created) == 1
    assert client.calls[1]["config"].cached_content == "cachedContents/fake1"


def test_reuse_extends_the_ttl_so_an_active_session_stays_warm():
    provider, client = gemini_cached_provider(
        [_gemini_response([], text="a"), _gemini_response([], text="b")]
    )
    for _ in range(2):
        provider.add_user("hello")
        provider.complete([SPEC])

    assert client.caches.updated == ["cachedContents/fake1"]


def test_a_changed_tool_list_replaces_the_cache():
    """A stale cache would run Peter against a tool list that no longer
    matches the registry — a correctness bug, not just a cost one."""
    provider, client = gemini_cached_provider(
        [_gemini_response([], text="a"), _gemini_response([], text="b")]
    )
    provider.add_user("hello")
    provider.complete([SPEC])

    other = ToolSpec(name="new_tool", description="Another.",
                     parameters={"type": "object", "properties": {}})
    provider.add_user("hello again")
    provider.complete([SPEC, other])

    assert len(client.caches.created) == 2
    assert "cachedContents/fake1" in client.caches.deleted
    assert client.calls[1]["config"].cached_content == "cachedContents/fake2"


def test_a_changed_system_prompt_replaces_the_cache():
    provider, client = gemini_cached_provider(
        [_gemini_response([], text="a"), _gemini_response([], text="b")]
    )
    provider.add_user("hello")
    provider.complete([SPEC])

    provider.system = "A DIFFERENT SYSTEM PROMPT"
    provider.add_user("hello")
    provider.complete([SPEC])

    assert len(client.caches.created) == 2
    assert "cachedContents/fake1" in client.caches.deleted


def test_the_heavy_model_is_never_cached():
    """Heavy-model storage is 9x the light model's per hour and it is used
    rarely by design, so a persistent cache would cost more than it saves."""
    provider, client = gemini_cached_provider(
        [_gemini_response([], text="...")],
        model="auto", auto_light_model="gemini-3.7-flash",
        auto_heavy_model="gemini-3.1-pro-preview", auto_heavy_word_threshold=10,
    )
    provider.add_user("please compare these two options")
    provider.complete([SPEC])

    assert provider.model == "gemini-3.1-pro-preview"
    assert client.caches.created == []
    assert client.calls[0]["config"].cached_content is None
    # ...and the prefix still has to travel, since nothing is cached.
    assert client.calls[0]["config"].system_instruction == "SYSTEM"


def test_the_light_model_is_cached_in_auto_mode():
    provider, client = gemini_cached_provider(
        [_gemini_response([], text="ok")],
        model="auto", auto_light_model="gemini-3.7-flash",
        auto_heavy_model="gemini-3.1-pro-preview", auto_heavy_word_threshold=10,
    )
    provider.add_user("what time is it")
    provider.complete([SPEC])

    assert provider.model == "gemini-3.7-flash"
    assert len(client.caches.created) == 1


def test_a_failed_cache_falls_back_to_inline_instead_of_failing_the_turn():
    """Below the 4,096-token floor, or an unsupported model — none of it is
    worth losing the user's answer over."""
    provider, client = gemini_cached_provider(
        [_gemini_response([], text="hi")], cache_fail=True
    )
    provider.add_user("hello")
    response = provider.complete([SPEC])

    assert response.text == "hi"
    sent = client.calls[0]["config"]
    assert sent.cached_content is None
    assert sent.system_instruction == "SYSTEM"


def test_a_failed_cache_is_not_retried_every_turn():
    provider, client = gemini_cached_provider(
        [_gemini_response([], text="a"), _gemini_response([], text="b")],
        cache_fail=True,
    )
    for _ in range(2):
        provider.add_user("hello")
        provider.complete([SPEC])
    assert provider._cache_unavailable is True


def test_close_deletes_the_cache_so_storage_stops_billing():
    provider, client = gemini_cached_provider([_gemini_response([], text="hi")])
    provider.add_user("hello")
    provider.complete([SPEC])
    provider.close()

    assert client.caches.deleted == ["cachedContents/fake1"]
    assert client.caches.live == set()


def test_close_is_safe_when_no_cache_was_ever_created():
    provider, client = gemini_cached_provider([])
    provider.close()
    assert client.caches.deleted == []


def test_caching_can_be_turned_off_in_config():
    provider, client = gemini_provider([_gemini_response([], text="hi")])
    provider.add_user("hello")
    provider.complete([SPEC])

    assert client.caches.created == []
    assert client.calls[0]["config"].system_instruction == "SYSTEM"


# ============================================================== shared loop
class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, responses):
        super().__init__("fake-model", "SYSTEM")
        self.responses = list(responses)
        self.history: list = []
        self.results: list[list[ToolResult]] = []

    def reset(self):
        self.history = []

    def add_user(self, text):
        self.history.append(("user", text))

    def add_tool_results(self, results):
        self.results.append(results)
        self.history.append(("results", results))

    def trim(self, max_messages):
        pass

    def complete(self, tools):
        return self.responses.pop(0)


def test_loop_returns_text_when_no_tools_are_wanted():
    provider = FakeProvider([ProviderResponse(text="Six o'clock.", stop_reason=STOP_END)])
    result = loop.run_turn(provider, [SPEC], "what time is it", lambda c: "")

    assert result.text == "Six o'clock."
    assert result.tool_calls == []
    assert result.iterations == 1


def test_loop_executes_tools_and_continues():
    provider = FakeProvider([
        ProviderResponse(
            tool_calls=[ToolCall(id="1", name="get_time", arguments={})],
            stop_reason=STOP_TOOLS,
        ),
        ProviderResponse(text="It is six.", stop_reason=STOP_END),
    ])
    executed = []

    result = loop.run_turn(
        provider, [SPEC], "time?", lambda c: executed.append(c.name) or "six"
    )

    assert executed == ["get_time"]
    assert result.text == "It is six."
    assert result.tool_calls == ["get_time"]
    assert provider.results[0][0].content == "six"


def test_loop_resumes_a_paused_turn():
    """A pause treated as 'finished' is a silently truncated answer."""
    provider = FakeProvider([
        ProviderResponse(text="partial", stop_reason=STOP_PAUSE),
        ProviderResponse(text="the whole answer", stop_reason=STOP_END),
    ])
    result = loop.run_turn(provider, [SPEC], "search", lambda c: "")

    assert result.text == "the whole answer"


def test_loop_caps_pause_restarts():
    provider = FakeProvider(
        [ProviderResponse(text="...", stop_reason=STOP_PAUSE) for _ in range(20)]
    )
    result = loop.run_turn(provider, [SPEC], "x", lambda c: "", max_pause_restarts=3)
    assert len(provider.responses) == 20 - 4


def test_loop_caps_tool_iterations():
    """A model looping on tools burns money until something stops it."""
    provider = FakeProvider([
        ProviderResponse(
            tool_calls=[ToolCall(id=str(i), name="get_time", arguments={})],
            stop_reason=STOP_TOOLS,
        )
        for i in range(50)
    ])
    result = loop.run_turn(provider, [SPEC], "x", lambda c: "ok", max_iterations=5)

    assert result.stop_reason == "max_iterations"
    assert len(result.tool_calls) == 5


def test_loop_reports_a_tool_crash_to_the_model():
    """A raising tool must become a result, not kill the turn."""
    def explode(call):
        raise ValueError("disk on fire")

    provider = FakeProvider([
        ProviderResponse(
            tool_calls=[ToolCall(id="1", name="get_time", arguments={})],
            stop_reason=STOP_TOOLS,
        ),
        ProviderResponse(text="Sorry, that failed.", stop_reason=STOP_END),
    ])
    result = loop.run_turn(provider, [SPEC], "x", explode)

    sent = provider.results[0][0]
    assert sent.is_error is True
    assert "disk on fire" in sent.content
    assert result.text == "Sorry, that failed."


def test_loop_replaces_empty_tool_output():
    provider = FakeProvider([
        ProviderResponse(
            tool_calls=[ToolCall(id="1", name="get_time", arguments={})],
            stop_reason=STOP_TOOLS,
        ),
        ProviderResponse(text="done", stop_reason=STOP_END),
    ])
    loop.run_turn(provider, [SPEC], "x", lambda c: "   ")
    assert provider.results[0][0].content == "(the tool returned nothing)"


def test_loop_handles_a_refusal():
    provider = FakeProvider([ProviderResponse(text="", stop_reason=STOP_REFUSAL)])
    result = loop.run_turn(provider, [SPEC], "x", lambda c: "")
    assert "can't help" in result.text


def test_loop_handles_truncation():
    provider = FakeProvider([ProviderResponse(text="", stop_reason=STOP_LENGTH)])
    result = loop.run_turn(provider, [SPEC], "x", lambda c: "")
    assert "too long" in result.text


def test_loop_never_returns_empty_text():
    provider = FakeProvider([ProviderResponse(text="", stop_reason=STOP_END)])
    assert loop.run_turn(provider, [SPEC], "x", lambda c: "").text == "Done."


# ============================================================ provider retry
class FlakyProvider(LLMProvider):
    """Fails `fail_times` times with a recoverable error, then answers."""

    name = "flaky"

    def __init__(self, fail_times: int, error: Exception | None = None):
        super().__init__("fake-model", "SYSTEM")
        self.fail_times = fail_times
        self.error = error or IntegrationError(
            "flaky is unavailable", service="flaky", recoverable=True
        )
        self.calls = 0

    def reset(self):
        pass

    def add_user(self, text):
        pass

    def add_tool_results(self, results):
        pass

    def trim(self, max_messages):
        pass

    def complete(self, tools):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error
        return ProviderResponse(text="back online", stop_reason=STOP_END)


def test_retry_recovers_after_transient_failures():
    provider = FlakyProvider(fail_times=2)
    result = loop.run_turn(
        provider, [SPEC], "x", lambda c: "",
        retry_attempts=5, retry_base_delay=0, retry_max_delay=0,
    )
    assert result.text == "back online"
    assert provider.calls == 3


def test_retry_reports_every_attempt():
    provider = FlakyProvider(fail_times=2)
    seen = []
    loop.run_turn(
        provider, [SPEC], "x", lambda c: "",
        retry_attempts=5, retry_base_delay=0, retry_max_delay=0,
        on_retry=lambda attempt, attempts, delay, error: seen.append((attempt, attempts)),
    )
    assert seen == [(1, 5), (2, 5)]


def test_retry_gives_up_and_raises_the_real_error_after_the_last_attempt():
    provider = FlakyProvider(fail_times=99)
    with pytest.raises(IntegrationError, match="flaky is unavailable"):
        loop.run_turn(
            provider, [SPEC], "x", lambda c: "",
            retry_attempts=3, retry_base_delay=0, retry_max_delay=0,
        )
    assert provider.calls == 3


def test_a_non_recoverable_error_is_never_retried():
    provider = FlakyProvider(
        fail_times=99,
        error=AuthError("bad key", service="flaky"),
    )
    with pytest.raises(AuthError):
        loop.run_turn(
            provider, [SPEC], "x", lambda c: "",
            retry_attempts=5, retry_base_delay=0, retry_max_delay=0,
        )
    assert provider.calls == 1


def test_default_retry_attempts_is_one_so_fakes_never_sleep():
    """The loop's default must not retry — tests and callers that don't pass
    retry settings explicitly must fail fast, not hang on a real sleep."""
    provider = FlakyProvider(fail_times=1)
    with pytest.raises(IntegrationError):
        loop.run_turn(provider, [SPEC], "x", lambda c: "")
    assert provider.calls == 1


# ============================================================ gemini fallback
class FakeGeminiClientPerModel:
    """generate_content fails or succeeds per model name, so a fallback chain
    can be exercised: one model down, its substitute up."""

    def __init__(self, behaviour: dict[str, list], cache_fail: bool = True):
        # model -> list of outcomes, each either a response or an Exception
        # instance to raise. Popped in order per call to that model.
        self.behaviour = {k: list(v) for k, v in behaviour.items()}
        self.calls: list[str] = []
        self.models = SimpleNamespace(generate_content=self._generate)
        self.caches = FakeCaches(fail=cache_fail)

    def _generate(self, model, **kwargs):
        self.calls.append(model)
        queue = self.behaviour.get(model, [])
        if not queue:
            raise RuntimeError(f"no scripted behaviour left for {model}")
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def gemini_fallback_provider(behaviour, cache_enabled=False, cache_fail=True, **kw):
    from peter.llm.providers.gemini_provider import GeminiProvider

    client = FakeGeminiClientPerModel(behaviour, cache_fail=cache_fail)
    provider = GeminiProvider(
        model=kw.pop("model", "gemini-3.7-flash"), system="SYSTEM", api_key="x",
        client=client, cache_enabled=cache_enabled,
        fallbacks={"gemini-3.7-flash": ["gemini-3.6-flash", "gemini-3.5-flash"]},
        **kw,
    )
    return provider, client


_OVERLOADED = SimpleNamespace(status_code=503, message="overloaded")


def _unavailable():
    from google.genai import errors as genai_errors

    return genai_errors.APIError(503, {"error": {"message": "overloaded"}})


def test_a_failed_model_falls_through_to_its_configured_substitute():
    provider, client = gemini_fallback_provider({
        "gemini-3.7-flash": [_unavailable()],
        "gemini-3.6-flash": [_gemini_response([], text="hi")],
    })
    provider.add_user("hello")
    response = provider.complete([SPEC])

    assert response.text == "hi"
    assert provider.model == "gemini-3.6-flash"
    assert client.calls == ["gemini-3.7-flash", "gemini-3.6-flash"]


def test_fallback_tries_every_candidate_before_giving_up():
    provider, client = gemini_fallback_provider({
        "gemini-3.7-flash": [_unavailable()],
        "gemini-3.6-flash": [_unavailable()],
        "gemini-3.5-flash": [_unavailable()],
    })
    provider.add_user("hello")
    with pytest.raises(IntegrationError):
        provider.complete([SPEC])

    assert client.calls == ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"]


def test_fallback_candidates_are_tried_with_no_delay(monkeypatch):
    """The whole point is speed: rotating through same-tier substitutes must
    not wait between them the way the outer backoff does."""
    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    provider, _ = gemini_fallback_provider({
        "gemini-3.7-flash": [_unavailable()],
        "gemini-3.6-flash": [_gemini_response([], text="hi")],
    })
    provider.add_user("hello")
    provider.complete([SPEC])

    assert slept == []


def test_a_non_recoverable_error_skips_the_fallback_chain():
    """A bad API key on the primary model is not fixed by trying another
    model — it should fail immediately, not burn two more calls proving it."""
    provider, client = gemini_fallback_provider({
        "gemini-3.7-flash": [RuntimeError("api key invalid")],
    })
    provider.add_user("hello")
    with pytest.raises(AuthError):
        provider.complete([SPEC])

    assert client.calls == ["gemini-3.7-flash"]


def test_later_turns_start_directly_on_the_model_that_last_worked():
    """Once a fallback is known good, do not keep re-probing the dead
    primary every single turn — that is exactly the 'too much delay' this
    exists to avoid."""
    provider, client = gemini_fallback_provider({
        "gemini-3.7-flash": [_unavailable()],
        "gemini-3.6-flash": [
            _gemini_response([], text="a"), _gemini_response([], text="b"),
        ],
    })
    provider.add_user("first"); provider.complete([SPEC])
    provider.add_user("second"); provider.complete([SPEC])

    assert client.calls == ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.6-flash"]


def test_a_model_with_no_configured_fallback_just_raises():
    provider, client = gemini_fallback_provider(
        {"gemini-3.1-pro-preview": [_unavailable()]},
        model="gemini-3.1-pro-preview",
    )
    provider.add_user("hello")
    with pytest.raises(IntegrationError):
        provider.complete([SPEC])
    assert client.calls == ["gemini-3.1-pro-preview"]


def test_fallback_cost_is_billed_at_the_substitute_actually_used():
    usage = SimpleNamespace(
        prompt_token_count=1_000_000, candidates_token_count=1_000_000,
        cached_content_token_count=0,
    )
    provider, _ = gemini_fallback_provider({
        "gemini-3.7-flash": [_unavailable()],
        "gemini-3.6-flash": [_gemini_response([], text="hi", usage=usage)],
    })
    provider.add_user("hello")
    response = provider.complete([SPEC])

    # gemini-3.6-flash: 0.75/3.75 per Mtok — same as 3.7, not a pricier model.
    assert response.usage.cost_usd == pytest.approx(0.75 + 3.75)


def test_caching_stays_on_for_a_light_tier_fallback():
    """The actual regression this exists to prevent: keying caching off the
    literal model name meant falling back to gemini-3.6 silently turned
    caching off for the rest of the session, even though 3.6 is exactly as
    cheap and cacheable as 3.7. It must key off the *tier* instead."""
    provider, client = gemini_fallback_provider(
        {
            "gemini-3.7-flash": [_unavailable()],
            "gemini-3.6-flash": [_gemini_response([], text="hi")],
        },
        cache_enabled=True, cache_fail=False,
    )
    provider.add_user("hello")
    provider.complete([SPEC])

    assert client.caches.created, "the light-tier fallback should still be cached"
    assert client.caches.created[-1]["model"] == "gemini-3.6-flash"


def test_caching_stays_off_for_the_heavy_tier_even_after_a_fallback_elsewhere():
    provider, client = gemini_fallback_provider(
        {"gemini-3.1-pro-preview": [_gemini_response([], text="hi")]},
        cache_enabled=True, cache_fail=False,
        model="auto", auto_light_model="gemini-3.7-flash",
        auto_heavy_model="gemini-3.1-pro-preview", auto_heavy_word_threshold=3,
    )
    provider.add_user("please analyze and compare these options")
    provider.complete([SPEC])

    assert client.caches.created == []


# ==================================================================== factory
def test_factory_lists_only_providers_with_keys(config, monkeypatch):
    from peter.core.config import Secrets
    from peter.llm import factory

    monkeypatch.setattr(
        config, "_secrets",
        Secrets(anthropic_api_key="a-key-value", openai_api_key=""),
        raising=False,
    )
    object.__setattr__(config, "_secrets",
                       Secrets.model_validate({"anthropic_api_key": "a-key-value"}))

    assert factory.available(config) == ["anthropic"]


def test_factory_rejects_an_unknown_provider(config):
    from peter.core.errors import ConfigError
    from peter.llm import factory

    with pytest.raises(ConfigError, match="unknown provider"):
        factory.build_provider(config, "SYSTEM", provider="llama")


def test_factory_explains_a_missing_key(config):
    from peter.core.config import Secrets
    from peter.core.errors import NotConfiguredError
    from peter.llm import factory

    object.__setattr__(config, "_secrets", Secrets())

    with pytest.raises(NotConfiguredError) as excinfo:
        factory.build_provider(config, "SYSTEM", provider="openai")
    assert "OPENAI_API_KEY" in excinfo.value.user_action


def test_every_provider_has_a_configured_model(config):
    from peter.llm import factory

    for provider in factory.PROVIDERS:
        assert factory.model_for(config, provider), f"{provider} has no model"
