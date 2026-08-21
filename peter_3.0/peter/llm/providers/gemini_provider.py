"""Google (Gemini).

Uses `models.generate_content` with explicit history, and
`FunctionDeclaration.parameters_json_schema` to pass JSON Schema through
untouched. The alternative — the typed `types.Schema` — silently drops
constructs it does not model, so a tool with an enum or a nested object arrives
at the model missing them.

Three things differ sharply from the other two:

**Automatic function calling is turned OFF.** The SDK will happily execute
Python callables on its own. That would run tools without ever passing through
Peter's permission gate, which is precisely the layer that exists to stop it.
Manual mode is a safety requirement here, not a style preference.

**Tool calls carry no id.** Anthropic and OpenAI both return one per call;
Gemini matches results to calls by *function name and position*. Synthetic ids
are generated so the shared loop has something to key on, then discarded when
results are sent back.

**System instructions go in the config**, not the history.

**`model="auto"` routes per turn.** When `agent.models.gemini` is `"auto"` in
config.yml, this provider does not call a fixed model — before each
`complete()` it classifies the turn's own text (see `peter/llm/router.py`) and
picks the cheap flash model for routine turns or the pro model for complex or
high-stakes ones. `self.model` always reflects whichever model the *last*
call actually used, so usage/cost tracking and `--health` stay accurate
without the rest of the codebase needing to know routing exists.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from peter.core.errors import AuthError, IntegrationError
from peter.llm import pricing, router
from peter.llm.base import (
    STOP_END,
    STOP_LENGTH,
    STOP_REFUSAL,
    STOP_TOOLS,
    LLMProvider,
    ProviderResponse,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)

log = logging.getLogger(__name__)

_STOP_MAP = {
    "STOP": STOP_END,
    "MAX_TOKENS": STOP_LENGTH,
    "SAFETY": STOP_REFUSAL,
    "RECITATION": STOP_REFUSAL,
    "PROHIBITED_CONTENT": STOP_REFUSAL,
    "BLOCKLIST": STOP_REFUSAL,
}


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        model: str,
        system: str,
        api_key: str,
        max_tokens: int = 8000,
        client: Any = None,
        auto_light_model: str = "gemini-3.7-flash",
        auto_heavy_model: str = "gemini-3.1-pro-preview",
        auto_heavy_word_threshold: int = 40,
        cache_enabled: bool = True,
        cache_ttl_seconds: int = 900,
        cache_min_tokens: int = 4096,
        fallbacks: dict[str, list[str]] | None = None,
        timeout: float = 60.0,
    ):
        self.auto = model.strip().lower() == "auto"
        # `self.model` (on the base class) is what usage/cost tracking and
        # --health read. In auto mode it starts pointed at the light model and
        # is updated to whichever model each `complete()` call actually used.
        # In fixed mode it starts on the configured model, same as always —
        # `_base_model` remembers that original choice so fallback has
        # something to rotate back toward if the substitute ever recovers.
        super().__init__(auto_light_model if self.auto else model, system, max_tokens)
        self._base_model = self.model
        self.auto_light_model = auto_light_model
        self.auto_heavy_model = auto_heavy_model
        self.auto_heavy_word_threshold = auto_heavy_word_threshold
        self.last_route_reason = ""

        # base model -> same-tier substitutes tried on a recoverable error,
        # before backoff. Gemini-only, same-price-tier by convention — a
        # fallback is a hedge against one model being briefly overloaded, not
        # licence to jump to a pricier one.
        self.fallbacks = fallbacks or {}
        # base model -> the candidate that last actually worked. Rotation
        # starts here instead of at the base every time, so once a fallback is
        # found good, later turns go straight to it instead of re-probing a
        # model that is still down.
        self._last_good: dict[str, str] = {}
        # What complete() last routed to before fallback ran — see
        # _should_cache(). Starts at the base model so a --health check or
        # any call before the first turn reads a sane value.
        self._current_base = self._base_model

        self.cache_enabled = cache_enabled
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_min_tokens = cache_min_tokens
        self._cache_name: str | None = None
        self._cache_fingerprint: str = ""
        self._cache_expires_at: float = 0.0
        self._cache_model: str = ""
        # Set after a failed create so one unsupported model or quota problem
        # does not mean a wasted cache round-trip on every single turn.
        self._cache_unavailable = False

        self.contents: list[Any] = []
        self._last_user_text = ""
        self._call_names: dict[str, str] = {}   # synthetic id -> function name

        if client is not None:
            self._client = client
        else:
            from google import genai
            from google.genai import types

            # google-genai passes timeout=None straight through to httpx,
            # which treats that as "no timeout" rather than "use a default"
            # — unlike anthropic/openai, which default to a sane ~10 minutes
            # on their own. Left unset, a slow response hangs indefinitely
            # with nothing to raise and nothing for call_with_retry to catch.
            self._client = genai.Client(
                api_key=api_key or None,
                http_options=types.HttpOptions(timeout=int(timeout * 1000)),
            )

    # ------------------------------------------------------------- history
    def reset(self) -> None:
        self.contents = []
        self._call_names.clear()

    def add_user(self, text: str) -> None:
        from google.genai import types

        self._last_user_text = text
        self.contents.append(
            types.Content(role="user", parts=[types.Part.from_text(text=text)])
        )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        from google.genai import types

        parts = [
            types.Part.from_function_response(
                name=r.name or self._call_names.get(r.id, "unknown"),
                response={"result": r.content},
            )
            for r in results
        ]
        # Gemini expects tool output under the "user" role, not a separate one.
        self.contents.append(types.Content(role="user", parts=parts))
        self._call_names.clear()

    def trim(self, max_messages: int) -> None:
        if len(self.contents) <= max_messages:
            return
        cut = len(self.contents) - max_messages
        while cut < len(self.contents) and not self._starts_turn(self.contents[cut]):
            cut += 1
        if cut < len(self.contents):
            self.contents = self.contents[cut:]

    @staticmethod
    def _starts_turn(content: Any) -> bool:
        """A plain user turn — not a function-response turn, which is also 'user'."""
        if getattr(content, "role", None) != "user":
            return False
        return not any(
            getattr(part, "function_response", None) is not None
            for part in (getattr(content, "parts", None) or [])
        )

    # ------------------------------------------------------------ completion
    def complete(self, tools: list[ToolSpec]) -> ProviderResponse:
        """Pick a base model (fixed, or routed by task — see router.py), then
        try it and its configured fallbacks in order.

        Rotating through same-tier substitutes happens here, with no delay
        between them — the whole point is answering fast despite one model
        being briefly overloaded, so waiting between substitutes would defeat
        it. The exponential backoff in loop.py is the outer safety net for
        when *every* candidate is down; this is the inner, near-instant one
        for when only the usual one is.
        """
        if self.auto:
            classification = router.classify(
                self._last_user_text, self.auto_heavy_word_threshold
            )
            base = (
                self.auto_heavy_model
                if classification.tier == router.HEAVY
                else self.auto_light_model
            )
            self.last_route_reason = classification.reason
        else:
            base = self._base_model

        # _should_cache() reads this. Falling back to a substitute must not
        # silently disable caching for the rest of the session — gemini-3.6
        # is exactly as cacheable as gemini-3.7, it is still the light tier,
        # just not literally auto_light_model any more.
        self._current_base = base

        last_error: Exception | None = None
        for position, candidate in enumerate(self._candidates(base)):
            if candidate != self.model:
                if position > 0:
                    log.info(
                        "gemini fallback: %s unavailable, trying %s",
                        self.model, candidate,
                    )
                    if self.on_fallback:
                        self.on_fallback(self.model, candidate)
                else:
                    log.info("gemini auto-routing: %s -> %s (%s)",
                             self.model, candidate, self.last_route_reason)
                self.model = candidate

            try:
                response = self._complete_once(tools)
            except IntegrationError as exc:
                if not exc.recoverable:
                    raise
                last_error = exc
                continue

            self._last_good[base] = candidate
            return response

        assert last_error is not None
        raise last_error

    def _candidates(self, base: str) -> list[str]:
        """`base` and its fallbacks, rotated to start at whichever one last
        worked, so a session does not keep re-probing a model still down."""
        chain = [base]
        for extra in self.fallbacks.get(base, ()):
            if extra not in chain:
                chain.append(extra)

        start = self._last_good.get(base, base)
        if start not in chain:
            start = base
        index = chain.index(start)
        return chain[index:] + chain[:index]

    def _complete_once(self, tools: list[ToolSpec]) -> ProviderResponse:
        """One attempt, against whatever `self.model` currently is."""
        from google.genai import types

        declarations = [
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                # Raw JSON Schema, bypassing the lossy typed Schema conversion.
                parameters_json_schema=t.parameters,
            )
            for t in tools
        ]

        # Peter's permission gate must see every tool call. Letting the SDK
        # execute callables itself would route around it entirely.
        no_afc = types.AutomaticFunctionCallingConfig(
            disable=True, maximum_remote_calls=None
        )

        cache_name = self._ensure_cache(declarations)
        if cache_name:
            # System instruction and tools live *inside* the cache. Sending
            # them again here is not merely redundant — the API rejects the
            # request for setting them alongside cached_content.
            config = types.GenerateContentConfig(
                cached_content=cache_name,
                max_output_tokens=self.max_tokens,
                automatic_function_calling=no_afc,
            )
        else:
            config = types.GenerateContentConfig(
                system_instruction=self.system,
                max_output_tokens=self.max_tokens,
                tools=[types.Tool(function_declarations=declarations)] if declarations else None,
                automatic_function_calling=no_afc,
            )

        try:
            response = self._client.models.generate_content(
                model=self.model, contents=self.contents, config=config
            )
        except Exception as exc:
            raise self._translate(exc) from exc

        candidate = self._first_candidate(response)
        if candidate is not None and getattr(candidate, "content", None) is not None:
            self.contents.append(candidate.content)

        usage = self._usage(response)
        self.usage.add(usage)

        tool_calls = self._tool_calls(candidate)
        return ProviderResponse(
            text=self._text(response, candidate),
            tool_calls=tool_calls,
            stop_reason=self._stop_reason(candidate, tool_calls),
            usage=usage,
        )

    # --------------------------------------------------------- prompt cache
    def _should_cache(self) -> bool:
        """Whether this model is worth holding a cache for.

        In auto mode, only the light *tier* — checked against `_current_base`
        (which model routing picked before fallback ran), not `self.model`
        (whichever candidate actually ended up handling the call). A fallback
        substitute is exactly as cheap and cacheable as the model it is
        standing in for; keying this off the literal model name would
        silently drop caching for the rest of the session the moment a
        fallback fires, which is the opposite of cost-efficient.

        Storage for the heavy tier costs 9x as much per hour ($4.50 vs $0.50
        per Mtok/hour) and by design it is used rarely — a persistent heavy
        cache would very likely cost more in storage than it ever saves.
        """
        if not self.cache_enabled or self._cache_unavailable:
            return False
        return (not self.auto) or self._current_base == self.auto_light_model

    def _fingerprint(self, declarations: list[Any]) -> str:
        """Identity of the cached prefix.

        A cache holds a specific system prompt and tool list. If either
        changes — a config edit, an integration being set up, a tool added —
        continuing to use the old cache would silently run Peter against a
        stale tool list. That is a correctness bug, not a cost one, so the
        fingerprint covers everything the cache contains.
        """
        parts = [self.system]
        parts += [
            f"{d.name}\x1f{d.description}\x1f{json.dumps(d.parameters_json_schema, sort_keys=True, default=str)}"
            for d in declarations
        ]
        return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()

    def _ensure_cache(self, declarations: list[Any]) -> str | None:
        """Return a usable cache name, creating or replacing one as needed."""
        if not self._should_cache() or not declarations:
            return None

        from google.genai import types

        fingerprint = self._fingerprint(declarations)
        now = time.time()
        fresh = (
            self._cache_name
            and self._cache_fingerprint == fingerprint
            and self._cache_model == self.model
            and now < self._cache_expires_at
        )
        if fresh:
            # Push expiry out so an active conversation keeps its cache, while
            # an idle one lets it lapse instead of paying storage all night.
            self._touch_cache()
            return self._cache_name

        # Stale for any reason — expired, or the prefix/model changed.
        self._drop_cache()

        try:
            cache = self._client.caches.create(
                model=self.model,
                config=types.CreateCachedContentConfig(
                    system_instruction=self.system,
                    tools=[types.Tool(function_declarations=declarations)],
                    ttl=f"{self.cache_ttl_seconds}s",
                ),
            )
        except Exception as exc:
            # Below the token floor, unsupported model, quota — none of it is
            # worth failing a turn over. Fall back to sending inline.
            self._cache_unavailable = True
            log.info("prompt caching unavailable, sending inline instead: %s", exc)
            return None

        self._cache_name = getattr(cache, "name", None)
        if not self._cache_name:
            return None
        self._cache_fingerprint = fingerprint
        self._cache_model = self.model
        self._cache_expires_at = now + self.cache_ttl_seconds
        cached_tokens = getattr(
            getattr(cache, "usage_metadata", None), "total_token_count", 0
        )
        log.info(
            "prompt cache created for %s (%s tokens, ttl %ds)",
            self.model, cached_tokens, self.cache_ttl_seconds,
        )
        return self._cache_name

    def _touch_cache(self) -> None:
        """Extend the current cache's TTL so an active session keeps it warm."""
        from google.genai import types

        try:
            self._client.caches.update(
                name=self._cache_name,
                config=types.UpdateCachedContentConfig(
                    ttl=f"{self.cache_ttl_seconds}s"
                ),
            )
            self._cache_expires_at = time.time() + self.cache_ttl_seconds
        except Exception:
            # Not fatal: the cache still works until it expires, and expiry
            # just means the next turn creates a fresh one.
            log.debug("could not extend cache ttl", exc_info=True)

    def _drop_cache(self) -> None:
        if not self._cache_name:
            return
        try:
            self._client.caches.delete(name=self._cache_name)
        except Exception:
            log.debug("could not delete cache %s", self._cache_name, exc_info=True)
        self._cache_name = None
        self._cache_fingerprint = ""
        self._cache_model = ""
        self._cache_expires_at = 0.0

    def close(self) -> None:
        """Release the cache at shutdown so storage is not billed after exit."""
        self._drop_cache()

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _first_candidate(response) -> Any:
        candidates = getattr(response, "candidates", None) or []
        return candidates[0] if candidates else None

    def _tool_calls(self, candidate) -> list[ToolCall]:
        if candidate is None:
            return []
        calls: list[ToolCall] = []
        parts = getattr(getattr(candidate, "content", None), "parts", None) or []
        for index, part in enumerate(parts):
            call = getattr(part, "function_call", None)
            if call is None:
                continue
            # Gemini supplies no call id; synthesise one so the shared loop and
            # the audit log have a stable handle.
            call_id = getattr(call, "id", None) or f"gemini-{index}-{call.name}"
            self._call_names[call_id] = call.name
            calls.append(
                ToolCall(
                    id=call_id,
                    name=call.name,
                    arguments=dict(call.args or {}),
                )
            )
        return calls

    @staticmethod
    def _text(response, candidate) -> str:
        try:
            direct = response.text
            if direct:
                return direct.strip()
        except Exception:
            # .text raises when the turn is only function calls.
            pass

        if candidate is None:
            return ""
        parts = getattr(getattr(candidate, "content", None), "parts", None) or []
        chunks = [getattr(p, "text", "") or "" for p in parts]
        return " ".join(c.strip() for c in chunks if c.strip()).strip()

    @staticmethod
    def _stop_reason(candidate, tool_calls: list[ToolCall]) -> str:
        if tool_calls:
            return STOP_TOOLS
        raw = getattr(candidate, "finish_reason", None) if candidate else None
        key = getattr(raw, "name", None) or str(raw or "STOP")
        return _STOP_MAP.get(key.upper(), STOP_END)

    def _usage(self, response) -> Usage:
        raw = getattr(response, "usage_metadata", None)
        if raw is None:
            return Usage()

        cached = getattr(raw, "cached_content_token_count", 0) or 0
        prompt = getattr(raw, "prompt_token_count", 0) or 0
        usage = Usage(
            input_tokens=max(0, prompt - cached),
            output_tokens=getattr(raw, "candidates_token_count", 0) or 0,
            cache_read=cached,
        )
        usage.cost_usd = pricing.estimate(self.model, usage)
        return usage

    def _translate(self, exc: Exception) -> Exception:
        import httpx

        # Network-level failure — DNS, refused/reset connection, timeout.
        # httpx is what google-genai transports over, so this is the reliable
        # way to catch it: no status code exists yet, Gemini was never reached,
        # and it is always worth retrying.
        if isinstance(exc, httpx.TransportError):
            return IntegrationError(
                f"could not reach Gemini: {exc}", service="gemini",
                recoverable=True, user_action="Check your internet connection.",
            )

        # google.genai.errors.APIError (and ClientError/ServerError) carry a
        # real HTTP status code — prefer that over parsing text when present.
        code = getattr(exc, "code", None)
        if isinstance(code, int):
            if code in (401, 403):
                return AuthError(
                    "Google rejected the API key", service="gemini",
                    user_action="Check GEMINI_API_KEY in .env. Get one at aistudio.google.com.",
                )
            if code == 429:
                return IntegrationError(
                    "Gemini quota or rate limit reached",
                    service="gemini", recoverable=True,
                )
            if code >= 500:
                return IntegrationError(
                    f"Gemini is unavailable: {exc}", service="gemini",
                    recoverable=True,
                )

        # Fallback: text matching, for whatever slips through unwrapped
        # (a raw socket/OS error, an older SDK version, etc).
        text = str(exc).lower()
        if "api key" in text or "unauthenticated" in text or "permission" in text:
            return AuthError(
                "Google rejected the API key",
                service="gemini",
                user_action="Check GEMINI_API_KEY in .env. Get one at aistudio.google.com.",
            )
        if "quota" in text or "429" in text or "resource_exhausted" in text:
            return IntegrationError(
                "Gemini quota or rate limit reached",
                service="gemini", recoverable=True,
            )
        if "deadline" in text or "unavailable" in text or "503" in text:
            return IntegrationError(
                f"Gemini is unavailable: {exc}", service="gemini", recoverable=True
            )
        if any(s in text for s in (
            "getaddrinfo", "name or service not known", "connection refused",
            "connection reset", "network is unreachable", "timed out",
            "temporary failure in name resolution", "errno 11001", "errno 111",
            "errno 61", "nodename nor servname",
        )):
            return IntegrationError(
                f"could not reach Gemini: {exc}", service="gemini",
                recoverable=True, user_action="Check your internet connection.",
            )
        return IntegrationError(f"Gemini call failed: {exc}", service="gemini")

    def health(self) -> str:
        if self.auto:
            return f"gemini/auto (currently {self.model})"
        return f"gemini/{self.model}"
