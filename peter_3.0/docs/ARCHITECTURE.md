# Peter 3.0 — Features & Technical Architecture

This document has two parts:

1. **What Peter 3.0 can actually do** — the feature set, grouped by capability.
2. **How it is built** — a detailed walkthrough of every subsystem ("agent" /
   layer), with a Mermaid diagram and a plain-English explanation for each.

If you only read one section, read Part 2 → *"One turn, end to end"* — it
explains the single most important design decision in the codebase.

---

## Part 1 — Feature List

### 1.1 Feature map

A top-down tree with 10 wide branches squeezes every leaf onto one band and
shrinks past legibility. Laid out left-to-right instead, with each category
in its own panel, the same content reads top-to-bottom in natural,
scrollable groups:

```mermaid
flowchart LR
    P(("Peter 3.0"))

    subgraph SG_V["Voice"]
        direction TB
        V1["Wake word: 'Hey Peter'"]
        V2["Speech-to-text, local, offline"]
        V3["Text-to-speech, sentence-streamed"]
        V4["Barge-in: interrupt Peter mid-sentence"]
        V5["--text mode: type instead of speak"]
    end

    subgraph SG_S["System Control"]
        direction TB
        S1["Open apps / URLs"]
        S2["Read, write, move, delete, search files"]
        S3["Screenshot, clipboard, volume"]
        S4["System stats: CPU / RAM / disk / battery"]
        S5["Lock workstation"]
        S6["Run PowerShell — the full-access escape hatch"]
    end

    subgraph SG_T["Time & Tasks"]
        direction TB
        T1["Alarms, timers, reminders"]
        T2["To-do list"]
        T3["Survives restart — jobs persisted in SQLite"]
    end

    subgraph SG_M["Memory"]
        direction TB
        M1["Remembers facts you tell it"]
        M2["Remembers your preferences"]
        M3["Recalls them by keyword search, unprompted"]
        M4["Keeps a rolling log of past conversations"]
    end

    subgraph SG_E["Email"]
        direction TB
        E1["Read / search / count unread"]
        E2["Send"]
        E3["Star, archive, delete, mark read"]
        E4["No Google OAuth needed — plain IMAP/SMTP"]
    end

    subgraph SG_C["Calendar & Tasks"]
        direction TB
        C1["Check today / upcoming events"]
        C2["Create / delete events"]
        C3["Google Tasks: list, add, complete"]
        C4["Morning briefing: mail + calendar + reminders"]
    end

    subgraph SG_B["Browser (sites with no API)"]
        direction TB
        B1["Read any product page: price, name, availability"]
        B2["Click / type / fill forms on your behalf"]
        B3["Log in once, session reused"]
        B4["Hard-blocked from ever clicking 'Buy' / 'Pay'"]
        B5["Per-site rate limiting to avoid bans"]
    end

    subgraph SG_L["Multi-LLM Brain"]
        direction TB
        L1["3 providers: Claude, GPT, Gemini"]
        L2["Switch by voice mid-conversation"]
        L3["Live running-cost meter, per session — shown in ₹"]
        L4["Per-provider model choice, editable in config.yml"]
        L5["Gemini: smart per-turn routing between a cheap\nand a strong model, based on the turn's own text"]
        L6["Gemini: same-tier fallback on a rate limit / 503,\nno added delay, sticky for the rest of the session"]
        L7["Retry with exponential backoff on any\nrecoverable error, announced as it happens"]
    end

    subgraph SG_G["Safety & Governance"]
        direction TB
        G1["Every action tiered: read / write / spend"]
        G2["A small set of destructive tools still confirm"]
        G3["Spend never auto-executes — hands off to you"]
        G4["Append-only audit log of every tool call"]
    end

    subgraph SG_PR["Proactive"]
        direction TB
        PR1["Meeting-prep nudge: calendar + memory, unprompted"]
        PR2["Inbox digest: what actually needs a reply"]
        PR3["Focus mode: mute, time-box, restore, summarize"]
        PR4["Waiting-on: mail you sent that nobody answered"]
        PR5["Price sweeps and CI failures, announced once each"]
        PR6["Daily work log written into memory"]
    end

    subgraph SG_PH["Phone"]
        direction TB
        PH1["Telegram: ask Peter anything, from anywhere"]
        PH2["Every proactive nudge mirrored to your phone"]
        PH3["Unknown chats get no reply at all"]
        PH4["SMS over ADB: read the one-time code aloud"]
    end

    subgraph SG_EY["Vision"]
        direction TB
        EY1["Look at the screen: 'what is this error?'"]
        EY2["Look at an image file"]
        EY3["Look at the current browser page"]
    end

    subgraph SG_R["Meetings"]
        direction TB
        R1["Record system audio, not just your mic"]
        R2["Transcribed on-device, audio never leaves"]
        R3["Decisions and action items, written to memory"]
    end

    subgraph SG_D["Development"]
        direction TB
        D1["Git status and your commits"]
        D2["Reviews requested of you, across every repo"]
        D3["A build that just broke, announced once"]
        D4["Standup written from real activity"]
    end

    subgraph SG_DOC["Documents"]
        direction TB
        DOC1["Index folders you point at"]
        DOC2["Search passages, free"]
        DOC3["Answers cited back to the file"]
        DOC4["Workspaces: save and reopen what you had open"]
    end

    subgraph SG_CO["Cost"]
        direction TB
        CO1["Every turn's cost kept, per day and per model"]
        CO2["Totalled in rupees, stored in dollars"]
        CO3["Optional daily cap: warn or block"]
    end

    P --> SG_V
    P --> SG_S
    P --> SG_T
    P --> SG_M
    P --> SG_E
    P --> SG_C
    P --> SG_B
    P --> SG_L
    P --> SG_G
    P --> SG_PR
    P --> SG_PH
    P --> SG_EY
    P --> SG_R
    P --> SG_D
    P --> SG_DOC
    P --> SG_CO
```

### 1.2 What each area means in practice

| Area | You can say... | What actually happens |
|---|---|---|
| **Voice** | "Hey Peter, what's the weather" | Wake word fires locally → speech is transcribed on-device (faster-whisper) → sent to the LLM → the reply is streamed to TTS sentence-by-sentence, so Peter starts talking before the full answer is even generated. |
| **System control** | "Take a screenshot" / "Open Chrome" / "What's my CPU doing" | Runs directly against Windows via `psutil`, `pywin32`, `pycaw`. `run_powershell` is the deliberate escape hatch for anything not covered by a named tool — always confirmed, always logged. |
| **Time & tasks** | "Remind me to stretch in 20 minutes" | Stored in a SQLite-backed APScheduler job. Survives a restart — kill the process, the reminder still fires later because the job lives on disk, not in memory. |
| **Memory** | "My college is PSG Tech" ... weeks later ... "which college am I in" | Facts and preferences are stored in SQLite with an FTS5 full-text index. Every turn, Peter's memory layer searches your new message for keyword overlap against stored facts and quietly injects the relevant ones — you never have to ask it to "remember to check its memory." |
| **Email** | "Any unread mail from my professor" | Reads over IMAP with an app password — no Google Cloud project, no OAuth, no weekly re-authorization. |
| **Calendar** | "What's on my calendar tomorrow" | Talks to Google Calendar/Tasks via a narrow OAuth client (sensitive, not restricted, scope — see §2.7). |
| **Browser** | "Check the price of this laptop on Flipkart" | No official API exists for that site (see README for the full API survey), so Peter drives a real, logged-in Playwright browser instead. It reads the page's own structured product data first (JSON-LD/OpenGraph — what Google Shopping reads), falling back to a screenshot only if that's absent. |
| **Multi-LLM** | "Switch to Gemini" | The whole conversation's tool-calling loop is vendor-neutral, so switching providers mid-session works without rewriting history — see §2.2.2. When Gemini is set to `auto` (this deployment's default), each turn's own text picks a cheap or a strong model with no extra API call — see §2.2.4. |
| **Safety** | "Delete this file" → Peter asks first, "open Notepad" → just runs | Every one of the 110 registered tools carries a permission tier at registration, but `write` now **defaults to running immediately** — only the handful of genuinely destructive/irreversible tools (delete, send, run a shell command, lock the workstation) are pulled back to confirm via `policy.standing_rules`. `spend`-class actions are not merely "asked about" — the code path to auto-execute them does not exist. See §2.4. |
| **Proactive** | (nothing — that's the point) | "Team meeting in 10 minutes, with Priya and Arjun" fires on its own from a calendar poll. "3 of your 23 unread look like they need a reply" fires from a mail poll. Both are read-only nudges, never actions taken on your behalf — see §2.10. |
| **Phone** | (from Telegram) "what's on my calendar tomorrow" | The same brain, tools, memory and permission gate, reached over the Telegram Bot API — and every proactive nudge mirrored the other way, so a reminder finds you when you are not at the machine. An unknown chat gets *no reply at all*: replying would confirm the bot exists. Anything needing confirmation is declined remotely rather than left hanging on a console prompt nobody is at — see §2.11. |
| **Vision** | "What's this error?" (pointing at the screen) | The screen is captured, downscaled, and actually read by a vision model. `take_screenshot` saved a PNG and stopped; this closes the loop. One isolated call, never left in conversation history — a megapixel image re-sent every turn would be the most expensive mistake available — see §2.14. |
| **Meetings** | "Start recording the sprint planning" | Captures what the *speakers* are playing (WASAPI loopback — i.e. the other people), transcribed on-device with faster-whisper in a background thread, then summarised into decisions and action items and written to memory. The audio never leaves the machine; only the final text is a model call. That stored episode is what lets a meeting-prep nudge say "your last conversation with Priya was about the thresholds" weeks later — see §2.12. |
| **Development** | "What's waiting on me?" / "Write my standup" | `git` and `gh` as subprocesses, so Peter never holds a GitHub token — `gh auth login` already keeps it in the OS keychain. Read-only by design: no commit, push or checkout tool. The standup is assembled from commits, calendar, focus sessions and to-dos, and the model is told to phrase that material and invent nothing — see §2.13. |
| **Documents** | "What did we agree the retry budget was?" | FTS5 over folders you point at, incremental on (size, mtime). `search_docs` is a SQLite query and free; `ask_docs` spends one call to turn the matching passages into an answer cited back to the file, and says so plainly when they do not contain one — see §2.14. |
| **Cost** | "How much have I spent this week?" | Every turn is appended to a ledger, derived by subtracting cumulative counters. Stored in USD (what vendors bill in), displayed in rupees — storing the converted figure would freeze each day's exchange rate into history. An optional daily cap warns or blocks, checked *before* a turn, since that is the only moment it can stop anything — see §2.17. |
| **Expenses & deliveries** | "Scan my bank texts" / "what did I spend on food" / "what's still on the way" | Bank/UPI and courier SMS parsed heuristically into two small ledgers, reusing the same SMS-reading pipeline §2.15 built for OTPs. On-demand only, no background sweep — see §2.18. |
| **Weather** | "What's the weather" / "weather in Mumbai" | Open-Meteo — free, no API key. A city name is geocoded once and cached for the session; folds into the morning briefing when added to `briefing.include` — see §2.18. |

### 1.3 What is deliberately **not** built yet

Being explicit about the boundary matters as much as the feature list:

- **No autonomous purchase completion.** Peter can fill a cart and reach the payment screen; RBI's mandatory two-factor authentication rules (from 1 April 2026) mean the OTP/UPI PIN step is legally yours, not automatable, so the code has no path that attempts it.
- **No cart-building hand-off** (was Phase 5, now deliberately dropped). Peter can walk a checkout to the payment screen, but since it can never legally complete one, the remaining value is a cart built by scraping flows that break constantly and put the account at risk. Not worth it.
- **No send-SMS tool**, even though §2.15 can now place and answer calls. Sending as you needs default-SMS-app privileges or `service call isms` incantations that differ per Android version, and is a bad idea for something driven by speech recognition; calling is a single well-documented public intent (`ACTION_CALL`) with no equivalent minefield.
- **No git write tools.** §2.13 reads repositories — status, commits, PRs, CI. There is no commit, push, merge or checkout tool: an assistant that rewrites your working tree on a misheard sentence is a liability, and the upside is saving you from typing `git commit`.
- **No automatic recording.** §2.12 records when asked. `auto_record_meetings` exists, defaults to off, and stays off unless deliberately enabled — recording a conversation is not a default anyone should inherit silently.

---

## Part 2 — Technical Architecture

### 2.0 System overview

Peter is six layers, each independently testable. A voice turn flows straight
down through all of them and back up:

```mermaid
flowchart TD
    subgraph Voice["1 · Voice I/O  (peter/voice/)"]
        direction LR
        Mic[Microphone ring buffer] --> Wake[openWakeWord] --> STT[faster-whisper] 
        TTS[Piper / edge-tts, sentence-streamed] 
    end

    subgraph Agent["2 · Agent Core  (peter/agent/, peter/llm/)"]
        direction LR
        Brain[Brain] --> Loop[shared tool-call loop] --> Provider[LLMProvider: Anthropic / OpenAI / Gemini]
    end

    subgraph Gate["3 · Policy Gate  (peter/policy/)"]
        direction LR
        Classify[tier: read / write / spend] --> Decide[allow / confirm / handoff / deny] --> Audit[audit.jsonl]
    end

    subgraph Registry["4 · Tool Registry  (peter/tools/)"]
        direction LR
        T1[system] & T2[time] & T3[memory] & T4[mail] & T5[calendar] & T6[browser] & T7[llm]
    end

    subgraph Services["5 · Services & Integrations  (peter/core/services.py, peter/integrations/)"]
        direction LR
        Sched[APScheduler] & Mail[IMAP/SMTP] & Google[Calendar/Tasks OAuth] & Browse[Playwright]
    end

    subgraph Memory["6 · Memory  (peter/memory/)"]
        direction LR
        DB[(SQLite + FTS5\nfacts · preferences · episodes · todos)]
    end

    Mic --> Wake --> STT -->|text| Brain
    Brain -->|speak| TTS
    Loop -->|tool call| Gate
    Gate -->|approved| Registry
    Registry --> Services
    Registry --> Memory
    Brain -.->|inject relevant facts\ninto the user turn| Memory
```

**Why this shape, not a "fleet of agents":** a single LLM loop with a large,
well-described tool registry outperforms a hand-wired multi-agent mesh for
this workload, and is far easier to debug and audit. Each layer below is
independently unit-tested; the plan explicitly reserves real subagents for
later, for genuinely parallel work (e.g. reading five product pages at once)
where a single context window would get flooded.

---

### 2.1 Voice I/O — `peter/voice/`

**Job:** turn "someone is talking near the mic" into text, and turn Peter's
reply back into audio, without ever blocking on network for the listening
part.

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Listening: wake word detected
    Listening --> Thinking: VAD detects end of speech
    Thinking --> Speaking: Brain.ask() returns text
    Speaking --> Idle: TTS queue drained
    Speaking --> Listening: wake word fires again (barge-in)\nTTS is stopped immediately
    Listening --> Idle: silence / no speech heard
```

- **Wake word** (`wake.py`): `openWakeWord` running on `onnxruntime`, fully
  local. No audio leaves the machine until "Hey Peter" is detected — this is
  a hard privacy property, not a performance optimization, and is worth
  keeping even if a cloud wake-word engine were faster.
- **Speech-to-text** (`stt.py`): `faster-whisper`, endpointed by voice-activity
  detection so Peter knows when you've *stopped* talking rather than waiting
  for a fixed timeout.
- **Text-to-speech** (`tts.py`): the reply is **streamed and split into
  sentences** before being handed to the speech engine, so Peter starts
  speaking the first sentence while later ones are still being synthesized —
  this is what makes response latency feel like ~1s instead of "wait for the
  whole paragraph."
- **Barge-in**: `main.py`'s `_voice_tick()` checks the wake word *even while
  Peter is speaking*; hearing it mid-sentence calls `speaker.stop()` and
  immediately starts listening. An always-on assistant that can't be
  interrupted is unusable.
- **`--text` mode** exists specifically so every other layer can be developed
  and tested without a microphone, wake-word model, or TTS engine in the
  loop at all.

---

### 2.2 Agent Core — `peter/agent/brain.py` + `peter/llm/`

**Job:** decide what to say and which tools to call, on whichever LLM vendor
is currently active, without the rest of the codebase caring which vendor
that is.

#### 2.2.1 One turn, end to end

This is the sequence that runs on *every single* thing you say to Peter —
the most important diagram in this document:

```mermaid
sequenceDiagram
    participant U as You
    participant B as Brain.ask()
    participant L as loop.run_turn()
    participant P as LLMProvider (active vendor)
    participant G as PolicyGate
    participant R as Tool Registry
    participant Svc as Services / Memory

    U->>B: "remind me to call mom at 6"
    B->>B: build user turn:\n<now>...</now> + relevant memory + your text
    B->>L: run_turn(provider, tools, user_text, execute=Brain._execute)
    L->>P: add_user(text); complete(tools)
    P-->>L: response: tool_calls=[set_reminder(...)]
    loop for each tool call
        L->>B: execute(call)
        B->>R: registry.get_record("set_reminder")
        R->>G: gate.check(tool="set_reminder", tier="write")
        G-->>U: "Set a reminder for 6pm to call mom — ok?"
        U-->>G: yes
        G->>Svc: scheduler.add_once(...)
        Svc-->>G: job id
        G-->>R: allowed, result
        R-->>B: "Reminder set for 6:00 PM."
        B-->>L: ToolResult(content="Reminder set for 6:00 PM.")
    end
    L->>P: add_tool_results([...]); complete(tools)
    P-->>L: response: text="Done — I'll remind you at six."
    L-->>B: TurnResult(text, tool_calls, stop_reason)
    B-->>U: speaks the reply
```

Two details that are easy to miss and matter a lot:

- **The tool loop can go around more than once.** A single user turn may
  call several tools in sequence — check the calendar, *then* decide there's
  a conflict, *then* ask for confirmation to move an event. `loop.run_turn`
  caps this at `MAX_ITERATIONS = 12` so a confused model can't spend money
  in a circle.
- **`pause_turn` is a real stop reason, not an error.** Anthropic's
  server-side tools (web search/fetch) can pause a turn mid-flight; the loop
  detects `STOP_PAUSE` and re-sends to resume, up to
  `agent.max_pause_restarts` times, instead of silently returning a
  truncated answer.

#### 2.2.2 Why there are three vendor SDKs behind one interface

```mermaid
flowchart LR
    Brain --> Loop["peter/llm/loop.py\n(one implementation, vendor-neutral)"]
    Loop --> Base["peter/llm/base.py\nToolSpec · ToolCall · ToolResult · Usage · ProviderResponse"]

    Base --> AP["anthropic_provider.py"]
    Base --> OP["openai_provider.py"]
    Base --> GP["gemini_provider.py"]

    AP --> ASDK["anthropic SDK\nmessages.create()"]
    OP --> OSDK["openai SDK\nresponses.create()"]
    GP --> GSDK["google-genai SDK\ngenerate_content()"]

    ASDK -.->|"web_search / web_fetch\nserver tools"| Claude[(Claude)]
    OSDK -.-> GPT[(GPT)]
    GSDK -.-> Gemini[(Gemini)]

    Factory["peter/llm/factory.py\nbuild_provider(config, system, name)"] -.builds.-> AP
    Factory -.builds.-> OP
    Factory -.builds.-> GP
```

- Every vendor SDK ships its **own** auto-executing tool runner
  (`tool_runner` for Anthropic, implicit execution for OpenAI, `automatic_
  function_calling` for Gemini). All three are deliberately **not used** —
  each would run tool calls itself, bypassing the Policy Gate entirely. The
  shared `loop.py` is the only thing allowed to decide when a tool actually
  runs, and it always routes through `Brain._execute` → the registry → the
  gate.
- Each provider still has to translate the vendor's wire format into the
  shared `ToolCall` / `ToolResult` / `Usage` shapes, and each vendor has its
  own quirks the provider file absorbs so nothing above it has to know:
  OpenAI sends tool arguments as a JSON *string* that must be parsed;
  Gemini's function calls carry no call-id, so one is synthesized
  (`gemini-{index}-{name}`); OpenAI folds cached tokens *inside*
  `input_tokens` where Anthropic and Gemini report them separately, so
  `_usage()` normalizes that before it reaches the shared `Usage` type.
- **Switching provider keeps memory and cost, not conversation history.**
  The three vendors' conversation formats are structurally incompatible
  (Anthropic's content blocks vs. OpenAI's response items vs. Gemini's
  `Content`/`Part` tree), so `Brain.switch_provider()` starts a fresh
  provider-side history but carries the cumulative `Usage` total forward and
  re-injects memory on the next turn — the point of switching is usually
  "compare cost/quality," and that comparison needs the running total to
  survive.

#### 2.2.3 Prompt caching — why the system prompt is frozen

```mermaid
flowchart TD
    Sys["System prompt\n(peter/agent/prompts.py)\nBYTE-IDENTICAL every turn"]
    User["User turn\n<now>...timestamp...</now>\n+ relevant memory facts\n+ your actual words"]

    Sys -->|cache_control: ephemeral, ttl configurable| Cache[("Provider-side\nprompt cache")]
    Cache -->|cache hit on turns 2, 3, 4...| Cheap["~90% cheaper input tokens"]
    User -->|never cached, changes every turn| Fresh[Sent fresh each time]
```

Prompt caching is a **prefix match**. The current time, today's date, or any
per-turn ID *must never* appear in the system prompt — putting it there would
silently invalidate the cache on every single turn and quietly multiply your
bill. That's why `Brain._build_user_content()` puts `<now>...</now>` and the
memory block in the *user* turn instead, keeping the system prompt frozen so
`usage.cache_read` stays non-zero from the second turn onward.

#### 2.2.4 Gemini: smart routing, same-tier fallback, and explicit caching — `peter/llm/router.py`, `peter/llm/providers/gemini_provider.py`, `peter/core/retry.py`

**Job:** when `agent.models.gemini` is set to `auto`, spend the strong model's
money only on turns that actually need it, survive Gemini being briefly
overloaded without adding real delay, and never let either of those things
quietly break prompt caching.

```mermaid
flowchart TD
    Turn["Turn text (after <now>/<memory> tags stripped)"] --> Router["router.classify()\nfree, instant, no extra API call"]
    Router -->|"list/reminder bookkeeping\n('add milk to my todo list')"| Light1[light_model]
    Router -->|"high-stakes action word\n(delete, buy, run command...)"| Heavy1[heavy_model]
    Router -->|"reasoning/complexity keyword\n(compare, debug, step-by-step...)"| Heavy2[heavy_model]
    Router -->|"longer than heavy_word_threshold"| Heavy3[heavy_model]
    Router -->|"otherwise: routine, short"| Light2[light_model]

    Light1 & Light2 --> Base["base model for this turn"]
    Heavy1 & Heavy2 & Heavy3 --> Base

    Base --> Candidates["candidates = [base] + gemini_fallbacks[base]\nrotated to start at whichever last worked"]
    Candidates --> Try1["try model 1"]
    Try1 -->|"recoverable error\n(429 / 5xx / network)"| Try2["try model 2 — no delay"]
    Try1 -->|ok| Done
    Try2 -->|ok| Done["record _last_good[base] = this model\ncache tier decided by BASE, not the model\nthat actually answered"]
    Try2 -->|"still failing after all candidates"| Backoff["call_with_retry():\nexponential backoff + jitter,\nannounced each attempt, voice + CLI"]
    Backoff -->|exhausted| RealError[surface the real error]
```

- **The router is a free heuristic, not an LLM call.** `classify()` runs a
  handful of regexes against the turn's own text — checked in a specific
  order, because order is where this went wrong twice in testing: bare
  words like "why" or "explain" matched so much ordinary conversation that
  *most* turns escalated to the expensive model, which is the opposite of
  the goal; then "add buy milk to my todo list" escalated because "buy"
  appeared inside the todo item's own text. The fix was checking list/
  reminder bookkeeping **first** (an unconditional escape to the cheap
  tier) and requiring genuine complexity phrases rather than single common
  words for everything after it.
- **Same-tier fallback is a fast hedge, not the backoff.** `gemini_fallbacks`
  in config.yml maps a model to same-*price* substitutes
  (`gemini-3.7-flash → gemini-3.6-flash → gemini-3.5-flash`), tried
  immediately with zero delay between them — this is what covers "one
  specific model is briefly overloaded." The exponential backoff below it
  only engages once every candidate in that list has also failed, which
  means the whole deployment (not just one model) is actually down.
- **Fallback is sticky.** `_last_good[base]` remembers which candidate
  worked last, so once a session has fallen over to `gemini-3.6-flash` it
  starts there next turn instead of re-probing the dead primary every
  single time — cheap to check, and it's what keeps "no added delay" true
  across a whole session, not just the first retry.
- **Caching has to key off the *routed tier*, not the model that actually
  answered — this was a real bug, found by testing the fallback live.**
  `_should_cache()` originally compared `self.model` to `auto_light_model`
  literally; once a session fell back to `gemini-3.6-flash`, that
  comparison went permanently false and silently disabled caching — and
  its ~90% saving — for the rest of the session, even though 3.6-flash is
  the same price tier and equally cacheable. Fixed by tracking
  `self._current_base` (the tier the router picked, set once per turn
  before any fallback rotation) and comparing *that* instead.
- **The heavy tier is never cached.** Caching only applies to the light
  model — the heavy model is used rarely enough per session that paying to
  *create* a cache for it would cost more than it saves.
- **Retry announces itself, in both voice and text.** `call_with_retry()`
  (used for the outer backoff, and by anything else in the codebase that
  needs the same shape) takes an `on_retry` callback; `Brain._on_retry`
  speaks "Gemini isn't responding (attempt 2 of 5). Retrying in 20
  seconds..." through `services().say()` — so a real outage is narrated,
  not silent, in whichever mode you're in.
- **Cost displays in ₹, not $.** Every vendor bills in USD; `Usage.summary()`
  converts using `agent.usd_to_inr_rate` (a manually maintained rate, no
  live FX feed) only at display time — the underlying `cost_usd` totals stay
  in USD, since that's what every pricing table in `pricing.py` is
  denominated in.

---

### 2.3 Tool Registry — `peter/agent/registry.py` + `peter/tools/`

**Job:** turn a plain Python function into something an LLM can discover,
understand, and call — for all three vendors, from one definition.

```mermaid
flowchart LR
    Fn["def set_reminder(text: str, at_iso: str) -> str:\n    '''docstring the model reads'''"]
    Deco["@peter_tool(tier='write')"]
    Fn --> Deco
    Deco --> Wrapped["ToolRecord\n(sdk_tool, tier, name)"]
    Wrapped --> Schema["ToolSpec\n(name, description, JSON-Schema parameters)\nvia Anthropic beta_tool inference"]
    Schema --> A2[Anthropic tools=[...]]
    Schema --> O2["OpenAI tools=[...] (parameters key)"]
    Schema --> G2["Gemini function_declarations=[...] (parameters_json_schema)"]
```

- **One decorator, three vendors.** `@peter_tool(tier=...)` wraps the
  function once; the Anthropic SDK's schema-inference (from type hints +
  docstring) is reused as the JSON-Schema engine for *all three* providers —
  not because Anthropic is privileged, but because it's already correct and
  rewriting signature→schema inference three times would just be three
  chances to get it wrong differently.
- **Tool order is part of the cached prefix.** `tool_specs()` returns tools
  in a stable, sorted order deliberately — a registry that reordered itself
  between runs would invalidate the prompt cache for no reason.
- **129 tools currently registered**, split by permission tier:

| Module | Read | Write | What it covers |
|---|---:|---:|---|
| `system.py` | 6 | 9 | apps, files, clipboard, volume, screenshot, stats, lock, PowerShell |
| `mail_tools.py` | 6 | 5 | read/search/send/star/archive/delete + inbox_digest + waiting_on |
| `time_tools.py` | 3 | 6 | alarms, timers, reminders, to-dos |
| `calendar_tools.py` | 4 | 4 | events + Google Tasks |
| `browser_tools.py` | 6 | 4 | read/click/type/login, plus multi-site comparison |
| `desktop_tools.py` | 2 | 6 | apps, bookmarks, YouTube, media keys, local folders |
| `memory_tools.py` | 2 | 4 | facts + preferences |
| `focus_tools.py` | 1 | 2 | timed mute-and-restore focus sessions |
| `briefing_tools.py` | 2 | 0 | morning briefing status |
| `llm_tools.py` | 2 | 1 | switch / inspect provider, spend report |
| `vision_tools.py` | 3 | 0 | look at the screen, an image, or the browser page |
| `watch_tools.py` | 2 | 2 | standing price and stock watches |
| `workspace_tools.py` | 1 | 3 | save / restore a set of open applications |
| `docs_tools.py` | 3 | 2 | index folders, search them, answer from them |
| `recorder_tools.py` | 4 | 3 | record, transcribe, summarise, read back |
| `dev_tools.py` | 7 | 0 | git, PRs, CI, work log, standup |
| `telegram_tools.py` | 1 | 1 | send to your phone, bridge status |
| `phone_tools.py` | 5 | 12 | SMS, one-time code, call log, phone screen, phone status / open a link, save a screenshot, transcribe a voice note, call a contact/make/answer/end a call, Spotify play/pause/skip, set/dismiss a phone alarm |
| `expense_tools.py` | 1 | 1 | scan bank/UPI SMS, report spending |
| `delivery_tools.py` | 1 | 1 | scan courier SMS, list pending shipments |
| `weather_tools.py` | 1 | 0 | current weather (Open-Meteo, no key needed) |
| **Total** | **63** | **66** | |

  Seven of the write-tier tools are pulled back to *confirm* by standing rules
  in `config.yml` — `delete_file`, `delete_email`, `delete_calendar_event`,
  `run_powershell`, `lock_workstation`, `send_email`, `make_phone_call`. Those
  destroy data, run arbitrary commands, send something that cannot be unsent,
  or — the newest addition — connect a real phone call with no on-device
  confirmation screen of its own to catch a misheard number. `call_contact`
  is a deliberately separate tool from `make_phone_call` rather than an
  optional argument on it, specifically so it can sit *outside* that standing
  rule: it only ever dials a number already saved under a real name in the
  phone's own contacts, a materially different risk from a number the model
  transcribed from speech — and the gate applies a tier per tool, not per
  argument, so two different confirmation behaviours could not have shared
  one tool. Everything else new in §2.15 (answering, hanging up, media
  control, alarms) stays plain `write`: either time-sensitive enough that a
  confirm prompt would defeat the point (an unanswered call goes to voicemail
  while you're confirming), or trivially reversible.

  **Tool groups whose credentials are missing are not registered at all**
  (`_REQUIRES` in the registry). Every schema is re-sent on every API call, so
  an unusable group costs ~1,000 tokens per request to describe actions that
  can only fail. `prompts.py` still states plainly which integrations are
  unconfigured, so Peter can say "you have not set that up" without carrying
  the schemas to do it.

  Notice there is no `spend` tier in this table — see §2.6, that boundary is
  enforced structurally in the browser layer, not by a tool flag that a
  mistake could flip.

---

### 2.4 Policy Gate — `peter/policy/gate.py`

**Job:** the one place every tool call passes through before its body runs.
Nothing calls a tool directly.

```mermaid
flowchart TD
    Call["Tool call from the agent loop"] --> Override{"policy.standing_rules\nnames this exact tool?"}
    Override -->|"yes, e.g. delete_file -> confirm"| Decision["Use the override decision"]
    Override -->|"no override"| Tier{"Tool's tier default"}
    Tier -->|"read -> allow"| Decision
    Tier -->|"write -> allow"| Decision
    Tier -->|"spend -> handoff"| Decision

    Decision -->|allow| Run
    Decision -->|confirm| Ask["Ask the human\n(spoken yes/no, or tray toast)"]
    Decision -->|handoff| Handoff["Never auto-execute.\nReturn a hand-off message:\nwhat's ready + what to tap"]

    Ask -->|"yes / within timeout"| Run[Tool body runs]
    Ask -->|"no / timeout / declined"| Decline["Return 'user declined' as a\nnormal tool RESULT, not an exception"]

    Run --> Log
    Handoff --> Log
    Decline --> Log[Audit log: one JSON line\ntimestamp · tool · args · tier · decision · result]
```

- **`write` defaults to `allow`, not `confirm`.** Early on, every write-tier
  tool prompted — opening an app, playing a video, adding a calendar event,
  all needed a spoken yes/no. In practice this trained the habit of
  reflexively saying "yes" to everything, which defeats the point of
  asking. The tier default flipped to `allow`; only tools in
  `policy.standing_rules` that are genuinely destructive or hard to
  reverse — `delete_file`, `delete_email`, `delete_calendar_event`,
  `run_powershell`, `lock_workstation`, `send_email` — are pulled back to
  `confirm`. `spend` still always hands off and never even reaches a
  prompt.
- **Per-tool overrides beat the tier default in *either* direction** —
  `standing_rules` can loosen a tool (`set_reminder: allow` even though
  reminders aren't literally read-tier) or tighten one (the destructive list
  above), all from `config.yml` with no code change.
- **A decline is a tool *result*, not a raised exception.** If it raised,
  the whole turn would abort and the user would hear nothing but a
  generic error. Returning it as text means the model sees "the user
  declined" and can react naturally — apologize, offer an alternative, ask
  what to do instead.
- **Fails closed.** The default confirmer when nothing else is wired up
  (`AlwaysDeny`) refuses everything rather than allowing it. A Peter that's
  annoyingly cautious is a nuisance; one that silently allows destructive
  actions because no confirmer was configured is a disaster.
- **Every decision is audited**, approved or not — `data/audit.jsonl` is the
  forensic trail for "why did Peter do that."

---

### 2.5 Memory — `peter/memory/store.py`

**Job:** remember things across restarts, and surface the *relevant* ones
without being asked.

```mermaid
erDiagram
    facts {
        string key PK
        string value
        string source
        datetime updated_at
    }
    preferences {
        string key PK
        string value
    }
    episodes {
        int id PK
        string summary
        datetime created_at
    }
    todos {
        int id PK
        string text
        bool done
    }
    facts ||--o{ FTS5_INDEX : "indexed for keyword search"
```

```mermaid
sequenceDiagram
    participant U as "which bus do I take home"
    participant Brain
    participant Mem as MemoryStore

    U->>Brain: ask(text)
    Brain->>Mem: context_block(text)
    Mem->>Mem: FTS5 search_facts("bus home") — tokenized so raw\nspeech can't break FTS5 query syntax
    Mem-->>Brain: "bus_route: route 70 to Gandhipuram"
    Brain->>Brain: inject into the USER turn (never the system prompt)
```

- **SQLite + FTS5**, four tables: `facts` (durable statements about you),
  `preferences` (how Peter should *behave* — e.g. "keep replies under two
  sentences"), `episodes` (rolling conversation summaries), `todos`.
- **Recall is automatic, not a tool the model has to remember to call.**
  Every turn, `Brain._build_user_content()` runs a keyword search over your
  message and silently prepends whatever facts share a token with it — the
  bus-route example above only works because "bus" appears in both the
  stored key's value and your question.
- Speech is tokenized before it reaches FTS5's query parser specifically
  because raw transcribed speech can contain characters (quotes, hyphens,
  stray punctuation) that are meaningful *syntax* to FTS5's query language —
  an untokenized query can outright fail to parse.

---

### 2.6 Browser Automation — `peter/integrations/browser/`

**Job:** the only way to interact with the sites that have no API at all
(Blinkit, Zepto, Myntra, Meesho, Swiggy, Zomato, most of Flipkart) — while
never being able to spend money and never getting the whole session banned.

```mermaid
flowchart TD
    Tool["browser_tools.py\nread tools: browse_page, check_price, find_on_page...\nwrite tools: browser_click, browser_type, browser_login"]
    Tool --> Manager["BrowserManager\n(Playwright, persistent logged-in profile)"]
    Manager --> Guard{"_guard(): rate limiter\nper-domain, before every navigation"}
    Guard -->|too soon| Wait[Wait / back off]
    Guard -->|ok| Nav[Navigate / act]
    Nav --> Detect{"Bot-wall detector\n(CAPTCHA / block page)?"}
    Detect -->|yes| Stop["STOP — hand off to human.\nNever solved, evaded, or retried automatically"]
    Detect -->|no| Extract["extract.py:\n1. JSON-LD / OpenGraph (~50 tokens)\n2. fallback: screenshot (~1500 tokens)"]

    Tool -->|browser_click specifically| Interlock{"interlock.is_purchase_action(label)?"}
    Interlock -->|"'buy', 'pay', 'confirm & pay',\n'place order', bare payment verbs..."| Block["PurchaseBlocked raised.\nNO override parameter exists —\nverified by a signature-inspection test"]
    Interlock -->|not a purchase label| Nav
```

- **Structured data first, screenshots last.** Almost every commerce page
  already publishes JSON-LD/OpenGraph product data for Google Shopping to
  read — price, name, availability, brand. Reading *that* costs roughly 50
  tokens; a screenshot costs roughly 1,500. Screenshots are the fallback
  when structured data is genuinely absent, not the default.
- **Per-domain rate limiting is the primary anti-ban strategy** — more
  effective in practice than trying to out-fingerprint commercial bot
  detection (Akamai/PerimeterX/Cloudflare all run on these sites). A
  persistent, real, headed browser profile with genuine cookies plus slow,
  human-paced polling avoids most of what would otherwise get an account
  suspended.
- **A bot-wall (CAPTCHA, block page) is a stop signal, never a puzzle.**
  `detect.py` exists to *recognize* that state and hand off to you, not to
  solve it.
- **The purchase interlock has no bypass parameter, on purpose.** This
  isn't "ask before buying" — the function that would complete a purchase
  simply doesn't exist in a callable form. `guard()` takes only a label to
  check, and a dedicated test inspects the function's *signature* to assert
  no override argument was ever added back in by accident.  This is the
  code-level enforcement of RBI's mandatory-2FA rule from §1.3: even if the
  policy gate were somehow bypassed, this layer still can't complete a
  payment.
- Ten tools total (one of them, `compare_across_sites`, delegates to the
  §2.16 subagent fan-out rather than reading a page itself), split precisely
  because a single `browse_and_extract` tool can't carry one permission tier:
  reading is `read`, clicking/typing is `write`, and "place order" isn't a
  *tier* at all — it's structurally unreachable.
- **The engine itself is configurable** (`integrations.browser.engine:
  chromium | firefox`, default `chromium`) — Playwright drives Firefox just
  as directly as Chromium, as its own bundled build rather than your
  installed one, so "log me in to Myntra" can open in whichever browser
  feels native rather than always defaulting to something Chrome-shaped
  regardless of `desktop.preferred_browser` (§2.6b — a genuinely different
  setting, since that one only ever opens plain links for you to look at).
  Chromium's anti-detection launch flag
  (`--disable-blink-features=AutomationControlled`) is Chromium-only CLI
  syntax; sending it to Firefox fails the launch outright rather than being
  ignored, so `_ENGINE_ARGS` is keyed per engine instead of hardcoded.
  Switching engine needs a fresh `profile_dir` — a Chromium user-data
  directory and a Firefox profile are structurally incompatible formats, so
  a launch against the wrong one fails rather than silently doing something
  odd.

---

### 2.6b Desktop control — `peter/integrations/desktop/`

**Job:** drive the software already installed on the machine — your own
browser, its bookmarks, whatever is playing, and local folders — from a spoken
request that never names anything exactly.

```mermaid
flowchart TD
    Say["'open the staging dashboard'"] --> Rank["matching.rank()<br/>token overlap + per-word fuzzy"]
    Rank --> Q{"clear winner?<br/>(high score AND beats runner-up)"}
    Q -->|yes| Act["Open it in the preferred browser"]
    Q -->|"several close"| Ask["Return the candidates as a normal result<br/>Peter asks which one"]
    Q -->|"nothing above floor"| None["Say so, suggest search_bookmarks"]

    subgraph Sources["what gets searched"]
        FF["Firefox<br/>places.sqlite (copied — locked while running)"]
        CR["Chrome / Edge / Brave<br/>Bookmarks JSON"]
        PL["Standard Windows folders<br/>+ desktop.places from config"]
    end
    Sources -.-> Rank

    Media["'pause' / 'next' / 'louder'"] --> Keys["media.send()<br/>keybd_event, real media keys"]
    Keys --> Any["Whatever holds media focus:<br/>YouTube, Spotify, VLC..."]
```

Four decisions worth knowing:

- **Ambiguity is a result, not a failure.** With 104 real bookmarks, "log
  search" genuinely matches four things. `rank()` only reports a confident
  winner when the top score is high *and* clears the runner-up by a margin;
  otherwise the tool returns the candidates and Peter asks. Silently opening
  one of four similar bookmarks is worse than one short question.
- **Character similarity cannot carry a match on its own.** Found live:
  `"zzz nothing"` scores 0.44 against `"HDFC Net Banking"` on incidental
  shared letters. Fuzzy scoring is discounted by half unless at least one word
  actually overlaps — but per-*word* similarity is kept, so `"dashbord"` still
  finds `"dashboard"`, which is what a speech transcript really produces.
- **Firefox's bookmark database is copied before reading.** `places.sqlite` is
  locked exactly when you want it — while Firefox is open — so it is copied to
  temp and the copy is queried.
- **Playback uses real media keys, not page automation.** `keybd_event` with
  the same virtual keys as a media keyboard, so Windows routes them to whatever
  currently has media focus. Driving the YouTube page through Playwright would
  only work for YouTube, only in Peter's own browser instance, and would break
  whenever the markup changed.
- **YouTube can open in a different browser than everything else.**
  `desktop.youtube_browser` (e.g. Brave) overrides `preferred_browser`
  (e.g. Firefox) specifically for `youtube.com`/`youtu.be` URLs — checked
  once, in `_open_with_preferred()`, so it applies uniformly whether the
  video came from `play_youtube`, `open_named_site("youtube")`, or a
  YouTube bookmark. Left blank, there is no override and everything shares
  one browser as before.

YouTube search deserves its own note: the top result is found by fetching the
search page and reading the `"videoId"` fields out of the JSON embedded in its
HTML — no API key, no quota, no browser. It depends on an internal page format,
so it fails cleanly: if the pattern stops matching, the tool opens the search
results page instead of doing nothing.

### 2.7 Integrations — `peter/integrations/{mail,google}/` + `peter/core/services.py`

**Job:** everything networked and optional is built **lazily**, on first
use, and never blocks startup.

```mermaid
flowchart LR
    subgraph Container["ServiceContainer (peter/core/services.py)"]
        direction TB
        Eager["Eager, built at startup:\nmemory · scheduler · audit · brain"]
        Lazy["Lazy, built on first use:\nmail() · calendar() · tasks() · browser()"]
    end

    Lazy -->|first call to mail tool| MailC["MailClient\nIMAP + SMTP, app password\nNO Gmail OAuth — see below"]
    Lazy -->|first call to calendar tool| GAuth["Google OAuth client\nCalendar + Tasks scopes only"]
    Lazy -->|first call to browser tool| Browser["Playwright persistent context"]

    MailC -.missing config.-> NC1["NotConfiguredError\nwith the exact .env keys to set"]
    GAuth -.missing config.-> NC2[NotConfiguredError]
```

- **Why IMAP/SMTP instead of the Gmail API for mail:** a personal Google
  Cloud project in "Testing" status issues refresh tokens for Gmail's
  *restricted* scope that **expire after 7 days** — Peter would silently
  stop reading your email every week until manually re-authorized. Moving
  to "In Production" for a restricted scope requires a third-party Google
  security audit, unrealistic for a personal project. Plain IMAP with an
  app password sidesteps the OAuth trap entirely for reading mail, at the
  cost of losing Gmail's label/thread richness.
- **Calendar/Tasks stay on OAuth** because those scopes are only
  *sensitive*, not *restricted* — they don't carry the 7-day trap, so a
  narrow, separate OAuth client for just those two scopes is fine.
- **Constructing a client is deferred until the first tool call that needs
  it.** Building an IMAP connection at process startup would mean Peter
  refuses to boot when the wifi is briefly down, and adds seconds to launch
  for integrations a given session might never touch. `NotConfiguredError`
  carries the *exact* fix (which `.env` keys, which CLI flag) rather than a
  bare failure.
- `ServiceContainer.health()` (surfaced by `python -m peter.main --health`)
  walks every provider/integration and reports its real state — this is the
  single command that answers "is everything wired up correctly."

---

### 2.8 Scheduler — `peter/scheduler/jobs.py`

**Job:** alarms, timers, reminders, and the daily briefing must survive the
process being killed and restarted — a reminder that silently disappears on
a crash is worse than no reminder at all.

```mermaid
flowchart LR
    Tool["set_reminder / set_alarm / set_timer\n(time_tools.py)"] --> Sched["APScheduler\n+ SQLite jobstore (same db_path as memory)"]
    Sched -->|job fires, even after a restart| Fire["fire_reminder()\nmodule-level function"]
    Fire --> Speak[Speaks the reminder]
    Fire --> Toast[Shows a toast]
```

- **Job targets must be module-level functions**, never bound methods or
  lambdas — APScheduler serializes a job by its Python *import path* to
  survive a restart, and a bound method or lambda has no stable import path
  to serialize. This is why `fire_reminder()` is a free function, not a
  method on `Scheduler`.
- Alarms, timers, and reminders are three thin wrappers (`add_once`,
  `add_in`, `add_daily`) over the same underlying job store, so "survives
  restart" is one property proven once, not three times.

---

### 2.9 Supervisor loop — `peter/main.py`

**Job:** own every long-lived object, wire the layers together, and make
sure one bad turn never takes down the whole session.

```mermaid
flowchart TD
    Start["python -m peter.main"] --> Cfg["load_config()\nvalidates config.yml + .env"]
    Cfg --> Init["Peter.__init__:\nregistry.load_all_tools()\nServiceContainer + memory + scheduler + audit\nPolicyGate\nBrain (picks provider)"]
    Init --> StartCmd["Peter.start():\nscheduler.start()\nschedule_briefing()"]
    StartCmd --> Mode{"--text or voice?"}
    Mode -->|--text| TextLoop["stdin → handle() → print"]
    Mode -->|voice| VoiceLoop["_voice_tick() loop:\nmic → wake word → STT → handle() → TTS"]

    TextLoop --> Handle
    VoiceLoop --> Handle["Peter.handle(text):\ntry: brain.ask(text)\nexcept PeterError: speak the message\nexcept Exception: speak a generic error, log full traceback"]

    Handle -->|one bad turn| Recover["Loop continues.\nNever the bare `except: pass` of peter_1.0/2.0"]
```

- **Every turn is isolated.** `handle()` catches both known (`PeterError`)
  and unknown exceptions, turns either into something speakable, and lets
  the *loop* continue — this directly replaces peter_1.0/2.0's single
  `except: pass` around the entire program, which either died silently or
  killed the whole assistant with no trace of why.
- **Nothing is a module-level global.** Every service is constructed in
  `Peter.__init__`, held in one `ServiceContainer`, and torn down in
  `shutdown()` — this is what makes the whole thing testable: tests build
  their own container and swap it in instead of fighting shared global
  state.
- `--health`, `--devices`, `--briefing`, `--google-auth` are all short-lived
  commands that build just enough of the container to answer the question
  and exit — they do not start the voice loop.

---

### 2.9b CLI status line — `peter/ui/progress.py`

**Job:** in `--text` mode, show *exactly* what a turn is doing at every
moment — not just "Peter is thinking" for the whole turn regardless of
whether it's calling zero tools or five — without any of that ever
corrupting the confirm prompt or a log line sharing the same terminal.

```mermaid
flowchart TD
    Loop["loop.run_turn()\non_progress(stage, tool_call)"] --> Reporter["ProgressReporter\n(one Status object, reused for the session)"]

    Reporter -->|"stage: thinking"| S1["peterThink spinner (brain, breathing dots)\n'Peter is thinking...'"]
    Reporter -->|"stage: continuing"| S2["aesthetic spinner (filling bar)\n'Peter is putting that together...'"]
    Reporter -->|"stage: tool"| S3["peterTool spinner (turning gear)\ndescribe_tool(name, arguments)\ne.g. '🚀 Opening Notepad...'"]
    Reporter -->|"Brain._on_retry"| S4["clock spinner\n'gemini isn't responding — retrying in 10s...'"]

    subgraph Shared["One rich.console.Console, shared"]
        S1
        S2
        S3
        S4
        RichHandler["logging.RichHandler\n(log lines: cache created, fallback fired...)"]
        PrintSpeaker["PrintSpeaker.say()\nreply rendered in a bordered Panel"]
        Confirm["ConsoleConfirmer.ask()\ny/N prompt, via reporter.suspend()"]
    end
```

- **`describe_tool()` reads the tool call's actual arguments, not just its
  name.** A curated map of ~70 tools each carry an emoji + template
  (`open_app` → `"🚀 Opening {name}"`), filled in from the call's own
  arguments — so the status line says *what* is being opened/sent/deleted,
  not just that something is happening. Any tool not in the map still gets
  a readable fallback (humanized snake_case), so a newly added tool never
  breaks this.
- **The branded spinners are registered into Rich's own spinner table**
  (`rich.spinner.SPINNERS["peterThink"] = {...}`), not vendored — a plain
  dict mutation at import time, keyed by frames of equal length so the
  status line doesn't jitter sideways as it animates (a real failure mode
  of naive emoji-cycling spinners).
- **One Console, shared by the spinner, the logger, and replies — this was
  a real, found-live bug, not a hypothetical.** `RichHandler` originally
  opened its *own* `Console`; two independent writers issuing ANSI redraw
  codes to the same terminal is exactly what produced garbled output like
  `⢃⠨ 🧠 Peter is thinking...[18:09:25] INFO prompt cache created...` on one
  line. `main()` now builds a single `Console` and threads it through
  `configure_logging(console=...)` and `Peter(console=...)`, which is what
  lets Rich suspend-and-redraw correctly around a log line instead of the
  two colliding.
- **Replies render in a bordered `Panel`, not a bare `print()`.** Fixes the
  same class of problem from a different angle: a multi-fact answer ("CPU
  is X, memory is Y, disk is Z...") used to print as one line mixed into
  the log stream; a Panel visually separates it. Along the way this caught
  a second, unrelated Rich landmine: `"[peter]"` looks exactly like markup
  (a style tag named "peter"), so printing it unescaped silently vanished
  instead of showing — worth knowing if any other literal-bracket text ever
  needs to go through this console.
- **The confirm prompt suspends the spinner for exactly its own duration**,
  via an injectable `suspend()` context manager on `ConsoleConfirmer`
  (`reporter.suspend`) — a spinner still animating while `input()` reads
  stdin mangles both the `[y/N]` text and whatever gets typed in response.
  Suspending only around the prompt, not the whole turn, means a turn with
  several confirmations in a row still shows the spinner between them.

---

### 2.10 Proactive features — `peter/meeting_prep.py`, `peter/inbox_digest.py`, `peter/focus.py`

**Job:** speak up without being asked, without ever taking an action that
matters on your behalf and without becoming a nag once it has already said
something.

```mermaid
flowchart TD
    subgraph MP["Meeting prep — poll every lead_minutes/2"]
        MPoll["check_meeting_prep()\nlist_events(now, now+lead_minutes)"] --> MDedup{"event.id already\nannounced?"}
        MDedup -->|no| MNote["memory.related_note(summary + attendees)"]
        MNote --> MSpeak["say(...) + toast"]
        MDedup -->|yes| MSkip[skip]
    end

    subgraph ID["Inbox digest — poll every poll_interval_minutes"]
        IPoll["check_inbox_digest()\ncount_unread() + list_messages(UNSEEN)"] --> IClassify["one-shot model call:\nsender/subject list -> which need a reply"]
        IClassify --> IChanged{"unread count changed\nsince last announcement?"}
        IChanged -->|yes| ISpeak["say(...) + toast"]
        IChanged -->|no| ISkip[skip — do not nag]
    end

    subgraph FM["Focus mode — on demand"]
        FStart["start_focus_session(minutes, label)"] --> FMute["volume.get() -> mute to 0"]
        FMute --> FSchedule["scheduler.add_one_off_job\nargs=[previous_volume, label, started_at]"]
        FSchedule -->|timer fires, even after a restart| FRestore["complete_focus_session()\nrestore volume + log episode + say(...)"]
        FStart -.->|or end_focus_session()| FRestore
    end
```

- **Meeting prep and the inbox digest both poll rather than pre-schedule.**
  A calendar event can be moved or cancelled, and new mail arrives
  continuously — a job scheduled once, in advance, would be checking a fact
  that may no longer be true by the time it fires. Polling re-reads the
  live state every time instead.
- **Dedup is what keeps "proactive" from becoming "annoying."** Meeting prep
  remembers which event ids it has already announced (pruned after 24h); the
  inbox digest remembers the last unread count it spoke and stays quiet
  until that number actually changes. Both dedup stores are in-process only
  — lost on a restart, which just risks one repeated nudge, never a missed
  one.
- **The inbox digest's classification is a real model call, deliberately
  small.** A keyword heuristic cannot tell an HR reminder from a production
  incident; a full conversational turn would cost far more than the task
  needs. `factory.build_provider()` builds a fresh, tool-free provider
  against a plain numbered sender/subject list (never message bodies) using
  whichever vendor is already configured — the same function that builds
  the main conversation's provider, just pointed at a one-shot prompt
  instead. On any failure it degrades to reporting the bare unread count,
  never to drafting or sending anything; it is read-only by construction,
  not by convention.
- **Focus mode's restore survives a restart because the value to restore is
  baked into the scheduled job's own arguments**, in the same persistent
  SQLite jobstore reminders use (§2.8). The in-process `_active` state that
  backs `focus_status()` and an early `end_focus_session()` does *not*
  survive a restart — only the actual system-level restore does, which is
  the half that actually matters.

---

### 2.11 Reaching you off the desk — `peter/telegram_bridge.py`, `peter/integrations/telegram/`

Everything in §2.10 announces itself with a spoken line and a desktop toast.
Both only exist if you are sitting in front of the machine, which makes every
proactive feature worth roughly nothing the moment you walk away. The Telegram
bridge is the multiplier that fixes that, in both directions.

```mermaid
flowchart LR
    subgraph IN["inbound"]
        direction TB
        T1["long poll<br/>getUpdates"] --> T2{"chat in<br/>allowed_chat_ids?"}
        T2 -->|"no"| T3["silence<br/>(logged once)"]
        T2 -->|"yes"| T4["Peter.handle_remote()"]
    end
    subgraph OUT["outbound"]
        direction TB
        N1["notify()"] --> N2["desktop toast"]
        N1 --> N3["telegram.push()"]
    end
    T4 --> B["same Brain,<br/>tools, memory, gate"]
    B --> R["reply to that chat"]
```

Four decisions, all of them security rather than design taste:

- **An unknown chat gets no reply at all.** A bot token is effectively a public
  endpoint — anyone who finds the bot's name can message it. Replying "you are
  not authorised" confirms the bot is alive and worth attacking. Silence does
  not. An empty `allowed_chat_ids` therefore means *nobody*, not *everybody*.
- **The backlog is dropped at startup.** Telegram holds undelivered updates for
  24 hours. Without this, everything sent while Peter was off would execute in
  a burst on restart — including instructions already carried out by hand.
- **Confirmations are declined, not deferred.** A `confirm`-tier tool reads the
  local console or the microphone, neither of which a phone can reach. A remote
  turn installs a `RemoteConfirmer` that declines immediately with its own
  explanation, rather than blocking for the full confirmation timeout and then
  declining anyway. Destructive actions stay at the desk.
- **Turns are serialised.** The bridge runs in its own thread while the mic or
  console may also be producing turns, and the provider owns one conversation
  history. `Peter._turn_lock` is what stops two turns interleaving into it —
  and is also what makes swapping the confirmer per-turn safe.

`notify()` gained a second channel rather than the features gaining a Telegram
dependency: reminders, meeting prep, the inbox digest, focus completion, price
alerts and CI failures all reach the phone without any of them knowing Telegram
exists. Both channels are independently best-effort; a failed push never fails
the job behind it.

The Bot API is reached with `urllib` — no new dependency. `getUpdates` is a
*long* poll, so a 25-second timeout is one held HTTP request that returns the
moment a message arrives, which is both cheaper and more responsive than
polling every second.

**A submodule silently shadowed the client accessor — a real bug, found by
running `--telegram-setup` twice in a row, not by reading the code.**
`peter/integrations/telegram/__init__.py` defines `client(config)`; the
package also had a submodule of the exact same name (`client.py`). Python
binds an imported submodule onto its parent package's namespace under the
submodule's own name — a side effect of the import system, not of anything
this code does deliberately — so the moment `client()`'s own body imported
that submodule (which it does, on first use, to reach `TelegramClient`), the
package's `client` attribute silently flipped from *function* to *module*.
The first call still worked, since the function reference had already been
retrieved before its own body ran; every call after that resolved
`telegram.client` to the module object instead and raised `'module' object is
not callable` — exactly what `--telegram-setup` hit: `.me()` succeeded, then
`find_chat_ids()` failed one line later. The existing tests never caught this
because they patch `telegram.client` directly with a lambda, which replaces
the exact attribute the bug corrupts rather than exercising the real import
path. Fixed by renaming the submodule to `api.py`, which removes the name
collision entirely; a regression test now calls the real, unpatched
`client()` twice in a row.

---

### 2.12 Local capture and transcription — `peter/meeting_notes.py`, `peter/integrations/desktop/recorder.py`

`faster-whisper` was already installed for the wake-word pipeline, which makes
meeting transcription essentially free: it runs on the CPU, the audio never
leaves the machine, and only the final summary is a model call — over text, not
audio.

**Capture order.** WASAPI loopback first (what the speakers are playing — i.e.
everyone *else* on the call), falling back to the microphone. Windows exposes
any output device as a capture device and PortAudio surfaces that through
`sd.WasapiSettings(loopback=True)`; older sounddevice builds have no such
argument, hence a fallback rather than a version pin. The caller is told which
one it got, because "captures your side only" is a meaningful difference to a
person deciding whether the recording was worth making.

**Disk writes are on their own thread.** Blocking inside a PortAudio callback
produces dropouts, and `wave.writeframes` on a slow disk absolutely can block.
The callback pushes to a bounded queue and drops rather than blocks when full —
20 ms of silence in one place beats corrupting everything after it.

**Transcription is deliberately asynchronous.** An hour of audio takes minutes
even on `small.en`. `stop_recording` returns immediately and a daemon thread
transcribes, summarises, writes `.txt` and `.md` next to the audio, records an
episode, then speaks and pushes the result. A tool call that blocks a
conversation for four minutes is not a tool, it is a hang.

That episode is what closes the loop with §2.10: weeks later the meeting-prep
nudge can say *"your last conversation with Priya was about the alerting
thresholds"*, because a real transcript was folded into memory rather than a
recollection.

---

### 2.13 Development state — `peter/integrations/dev/`, `peter/worklog.py`, `peter/ci_watch.py`

Shelling out to `git` and `gh` rather than talking to the GitHub API directly.
That trade is deliberate: `gh auth login` already keeps your credentials in the
OS keychain, so **Peter never sees or stores a GitHub token**, private repos and
enterprise hosts work without extra configuration, and the whole integration is
one subprocess call plus a `--json` flag.

Everything is parsed from machine-readable formats — `git status --porcelain=v2`
and `gh --json` — never from human-readable output, which changes between
versions and localises. Commit lines use a unit separator (`\x1f`) between
fields, because commit subjects contain colons, pipes and dashes constantly.

**The work log is a join, not a memory.** Commits sit in git, meetings in the
calendar, focus sessions and meeting notes in episodes, finished work in the
to-do list. Nothing joined them up. The daily job assembles all four and writes
one episode, so "what was I doing last Tuesday" survives long after that
conversation left the context window. `standup_notes` is the only part that
calls a model, and only to phrase material it is handed — with an explicit
instruction never to invent a task, meeting or blocker.

Every source degrades independently: no git, no calendar, no `gh`, nothing
configured at all — you still get a log of whatever the rest could see.

**The CI watcher is mostly its dedup.** A failing run stays in `gh run list` for
days, so without remembering what has been announced it would report the same
broken build every ten minutes until it was fixed — which is exactly how a
useful alert becomes a muted one. It also *primes* on its first sweep after
startup: it records what is already failing without announcing it, so starting
Peter does not produce a burst of alerts about last week.

---

### 2.14 Watching, seeing, searching — price watches, vision, documents

Three features that share no code but do share a shape: each is a small store
plus one rule about when to speak.

**Price watches** (`peter/price_watch.py`) — Phase 4, and the cheapest large
feature here because the hard parts already existed: §2.6 proved a product page
can be read from its own published structured data, the browser already spaces
requests per domain, and the scheduler already survives restarts. What this adds
is the watch list and `evaluate()` — a pure function of the stored watch and the
fresh reading, which is why the alert rule can be tested exhaustively without a
browser anywhere near it. It fires on a target reached, a fall of at least
`drop_percent`, or a return to stock, and **never twice for the same price** —
only a *further* fall is news. Sweeps stay slow on purpose; the fix for a slow
sweep is fewer watches, not a shorter gap.

**Vision** (`peter/llm/vision.py`) — deliberately *not* part of `LLMProvider`.
That interface models one long conversation with history, caching and tools; an
image question is the opposite — one call, no tools, no history, and an input
you never want re-sent. Bolting it on would leave a megapixel screenshot in the
conversation context for the rest of the session. Images are downscaled first: a
3840px grab costs several times a 1600px one and reads no better, since the
model is reading, not pixel-peeping. An image already small enough is passed
through untouched, because re-encoding a screenshot as JPEG only adds artefacts
to the text being read.

**Documents** (`peter/docs_index.py`) — the same FTS5 idea as §2.5, aimed at
files. In its own database, because it is the one store that can reach hundreds
of megabytes and the one you might want to delete and rebuild. Indexing is
incremental on (size, mtime), so re-indexing a large tree after editing two
files costs two files' work. Chunking splits on paragraph boundaries rather than
a fixed window — a passage that stops mid-sentence retrieves badly and reads
worse when quoted back. Search tries every term, then any term: requiring all of
them returns nothing far too often for a spoken question. `search_docs` is a
SQLite query and free; `ask_docs` spends a model call to turn passages into a
cited answer. Keeping them separate is what keeps the cheap one cheap.

---

### 2.15 The phone — `peter/integrations/phone/adb.py`, `peter/tools/phone_tools.py`

Windows has no API into your handset's messages; Phone Link is closed. The two
routes that work are a companion app you write, or ADB, which most developer
machines already have.

Fifteen tools, five `read` and ten `write`, grown in two passes: read-only
first (SMS, the call log, the screen), then real device control once a phone
was actually connected and staying read-only stopped being the obvious
default. Reading is unrestricted: SMS (`read_sms`, `latest_code`), the call
log (`read_call_log`), and the screen itself (`read_phone_screen`, a
screenshot piped straight into the same vision pipeline §2.4 already built
for the desktop screen, good for reading a QR code or checking what an app is
showing). Acting spans a wider range now than "narrow, on purpose" alone
describes — see below — but the one line that still holds: **there is no
path from Peter to sending a text as you.**

Two parsing details carry all the risk. `adb shell` hands its argument to the
*phone's* shell, which re-splits it — so the device-side command is built as one
string, since passing `--where` and `date>123` as separate argv entries arrives
as two words and fails with an error mentioning neither. And `content query`
output has no escaping, so fields are split on the *next field name* via
lookahead rather than on `", "`, which appears freely inside real message
bodies. Call log rows and the contacts table parse the same way.

**Two more parsing bugs, both found by actually running this against a real
phone — mocked tests never exercise a real device's own quirks.** First:
`content query`'s comma-separated `--projection a,b,c` works for
`content://sms/inbox` on the test device but fails outright for
`content://call_log/calls` ("Invalid column a,b,c", the whole string taken as
one literal column name) and for the contacts provider ("Non-token detected
in 'a,b'", a stricter tokenizer rejecting it even with a space after the
comma) — meaning contact resolution and the entire call log were broken from
the moment they shipped, working only in unit tests whose fakes don't
reproduce a real tokenizer. A colon-separated projection (`a:b:c`) works
identically across all three providers, so that is what every projection
here uses now. The contacts provider had a second, independent gotcha on top:
its friendly `number` column alias does not resolve through the raw `content`
CLI at all ("Invalid column number") even once the separator is fixed — the
real column is `data1`, the generic key-value slot `ContactsContract` uses
for a phone-type row's number.

Second: a message body can contain a literal embedded newline — routine for
a multi-line bank SMS ("Sent Rs.60.00\nFrom HDFC Bank A/C...\nRef 123...").
Splitting `content query`'s whole output on every `\n`, as if a newline
always meant "next row," broke one logical row into several that no longer
matched `_ROW` — truncating the body *and* silently dropping whatever field
came after the break, which for SMS is `date`, so every affected message
read back as 1 January 1970. `_rows()` now splits only on a newline that is
immediately followed by the next `Row: N ` marker, and both `_ROW` and
`_FIELD` run with `re.DOTALL` so a field's captured value can itself span
several lines.

**Contact names are resolved, not stored.** `read_sms` and `read_call_log`
label a sender or caller with a name from `content://com.android.contacts`
when one matches — normalising both sides to the last 10 digits so "+91
90000 00000", "090000-00000" and a plain contacts entry all line up
regardless of formatting. The lookup is best-effort and cached in-process for
ten minutes per (adb path, device serial): a phone with contacts read
blocked, or none saved, degrades to showing the raw number rather than
failing SMS or call log reading, which do not depend on it succeeding.
`find_contact` (behind `call_contact`, below) runs the same lookup the other
direction — name to number — with a two-pass match: the query as a literal
substring of the saved name first, falling back to matching on individual
*words* when that finds nothing, since natural speech adds relationship
words a saved name often doesn't have at all. The case that motivated the
fallback is a real one: a contact saved as bare "Ancy", called "Ancy Mom" out
loud — not a substring match, but the two share the word "ancy".

`latest_code` prefers a message that says it is a code over a bare number,
because the first number in an SMS is very often an order id or an amount. The
code is then read out digit by digit — a speech engine given "123456" says "one
hundred and twenty-three thousand…".

`screenshot_bytes()` is the one function here that does not go through the
shared `_run()` helper: `_run` runs adb in text mode, which on Windows
rewrites line endings and would corrupt a binary PNG capture. It shells out
separately with `exec-out screencap -p`, in binary mode, and does its own
error handling instead.

**Wireless ADB self-heals instead of needing `adb connect` re-run by hand.**
`integrations.phone.wireless_address` ("`<ip>:<port>`", set once after
pairing via Settings → Developer options → Wireless debugging) is USB's
alternative: no cable, but the session is far less durable than USB's —
it doesn't survive the phone leaving and rejoining Wi-Fi, a reboot on either
side, or wireless debugging itself being toggled off and on. Without
handling that, every one of those would silently turn "the phone is
connected" into "run `adb connect` by hand before the next command works,"
which defeats the point of not needing the cable. `_run()` (and,
separately, `screenshot_bytes()` and `_list_remote()`, which bypass `_run()`
for their own reasons) now catch specifically a "no device"/"device
offline" failure — never "unauthorized," which reconnecting cannot fix —
and retry exactly once through `adb connect <wireless_address>` before
raising. USB-only setups (`wireless_address` empty, the default) pay
nothing for this: the check is a single string test before any subprocess
call, not a proactive `adb devices` poll on every command.

**Calls, media, and alarms — real device control, not just reading.**
`make_phone_call` / `call_contact` / `answer_phone_call` / `hang_up_phone_call`
go through the standard `ACTION_CALL` intent and the
`KEYCODE_CALL`/`KEYCODE_ENDCALL` key events — the same key press ends an
active call or rejects a ringing one, matching a physical end-call button.
`play_music_on_phone` /
`pause_music_on_phone` / `skip_track_on_phone` launch Spotify by package name
(`monkey -p ... -c LAUNCHER`, deliberately not a hardcoded activity class,
which is exactly the kind of internal detail that breaks across app updates)
and otherwise send system-level media keys (`KEYCODE_MEDIA_PLAY/PAUSE/NEXT`)
— these are not Spotify-specific once something is already playing; they
control whatever app currently holds the phone's active media session, the
same as a headset button would. `set_phone_alarm` / `stop_phone_alarm` go
through `android.intent.action.SET_ALARM` / `DISMISS_ALARM`, the public
`android.provider.AlarmClock` intents meant for exactly this, so they work
with whatever the phone's default clock app is rather than one OEM's
internals — support for `DISMISS_ALARM` specifically varies by clock app, and
when nothing handles it `am start` fails the ordinary way rather than
Peter pretending it worked. A phone-side "reminder" is implemented as a
labelled alarm, since Android has no separate public reminder intent.

Only `make_phone_call` is pulled back to `confirm` by a standing rule (see the
tool-count table above) — it connects immediately with no on-device
confirmation screen of its own, and dials a number the model transcribed from
speech, which can be misheard. `call_contact` deliberately does *not* inherit
that rule: resolving a name against the phone's own saved contacts, and only
ever dialling when exactly one match comes back, is a different and lower
risk than a raw number — the whole failure mode `confirm` exists to catch
(the wrong digits) can't happen once the target is a saved contact matched
unambiguously. Answering and hanging up stay plain `write` for the same
"confirm would defeat the point" reason as before (an unanswered call goes to
voicemail while you're confirming), or because they're trivially reversible.

**Every one of these embeds free text into a single command string handed to
the phone's own shell — which makes each of them a command-injection surface,
closed off in one shared place rather than per-call.** A phone number, a
Spotify search query, an alarm label, a URL: all are values Peter's own model
chose based on what the user said (or, worse, on page text the model read),
and all end up inside a shell string re-parsed on the device the way §2.15's
opening paragraph already describes for `content query`. The first version of
`open_link_on_phone` (§2.15, earlier revision) escaped embedded double quotes
by hand and wrapped the URL in double quotes — which does not actually stop a
POSIX shell from expanding `$(...)`, a backtick, or `$VAR` *inside* those
double quotes, so a crafted URL like `https://x/$(reboot)` would have executed
on the phone the moment `open_link_on_phone` opened it. Fixed by routing every
free-text value through `_quote()` (a thin wrapper on `shlex.quote`), which
single-quotes instead — single quotes disable all expansion in a POSIX shell,
which is what actually makes embedding arbitrary text safe. `call_number`
additionally validates the number against an allow-list of characters a
phone number can actually contain, before it ever reaches a shell string, as
a second, independent layer — defence in depth rather than trusting one
mechanism to hold.

**`adb_path` is resolved against the project root, not left to depend on the
process's working directory — a real bug, found by actually running it.**
Android Platform Tools has no installer; a common way to get it is unzipping
the download straight into the repo rather than adding it to PATH, and a
relative `adb_path` pointing into that folder worked *only by accident* when
Peter happened to be launched from inside `peter_3.0/` — `shutil.which()` and
`subprocess.run()` both resolve a relative path against the current working
directory, unlike every other path in `config.yml` (`data_dir`,
`browser.profile_dir`, ...), which all go through `Config.resolve()` against
`PROJECT_ROOT`. `_resolved_adb_path()` gives `adb_path` the same treatment,
with one deliberate exception: a bare `"adb"` (no directory component at all)
is left untouched, since that means "search PATH," and resolving it against
the project root would instead look for a literal file named `adb` sitting at
the repo root.

This is the honest end of the payment story from §2.6: Peter can walk a checkout
to the payment screen but can never complete it, and reading the OTP aloud so
you can type it is exactly as far as automation should reach into that.
`open_link_on_phone` closes the other half of the hand-off — it can put the
checkout page itself on your screen, not just tell you a code — but the tap
that finishes the payment is still always yours; the tool only ever opens a
plain `http(s)` URL, rejecting `intent://`/`market://`-style schemes that
`am start -d` would otherwise also accept and that could launch an arbitrary
app component instead of a web page.

---

### 2.16 Subagents — `peter/agent/subagents.py`

Phase 7, and the case that genuinely earns one. Comparing a product across five
sites means five page reads at ~1,500 tokens each. Fed into the main
conversation that is 7,500 tokens of raw page text sitting in history for the
rest of the session, **re-sent on every subsequent turn**, to produce an answer
two sentences long.

So each page gets its own isolated, tool-free model call answering the question
about that page alone, and only those short findings are synthesised. The main
conversation sees a comparison, not five web pages.

**The fan-out is in the model calls, not the fetches.** There is one browser
with one page, and per-domain rate limiting is deliberate, so pages are read
serially. What runs in parallel is the reading-and-extracting of what has
already been fetched — which is where the wall-clock time goes once a model is
involved. Claiming otherwise would be a nice diagram and a lie.

`factory.build_provider` gained a `model` override for this: the subagent's job
is extraction, not reasoning, so a cheaper model is usually the right tool. The
main conversation never uses that override.

---

### 2.17 Cost, kept — `peter/spend.py`

`Brain.usage_summary()` always reported the session total, which vanishes when
the process exits. But every real question about cost spans longer than one
session: is Gemini cheaper *in practice*, did `auto` routing help, what does a
working day cost, is that rise a trend or one expensive afternoon.

Each turn is now appended to a ledger. The per-turn figure is derived by
*subtracting* cumulative counters before and after the turn, because providers
accumulate usage across a session rather than reporting per call.

**Stored in USD, displayed in rupees.** USD is the unit every vendor bills in;
storing the converted figure would freeze each day's exchange rate into history
and make last month's numbers uncomparable with this month's. Conversion happens
at the point a human reads it.

The daily cap is checked *before* a turn, since that is the only moment it can
stop anything — a turn's cost is not knowable until it has been paid for. It
offers `warn` and `block` and deliberately **not** "drop to the cheap model":
Gemini's `auto` routing already picks a model per turn and overwrites it on
every call, so a budget-imposed downgrade would silently not apply on the very
setup most likely to want it. A switch that works on two vendors out of three is
worse than not offering one.

---

### 2.18 Horizontal additions — `peter/expenses.py`, `peter/deliveries.py`, `peter/integrations/weather.py`

Four features added in one pass specifically to reuse infrastructure rather
than start fresh: two lean on the SMS pipeline `§2.15` had already read and
hardened, one is a config-and-tools wrapper around an existing free API, one
reuses a pull-and-transcribe path built for something else entirely.

**Expense and delivery tracking both parse the same SMS stream two different
ways.** `expenses.py`'s `parse_transaction()` looks for an amount plus a
debit/credit verb ("sent", "debited", "credited", "received") and pulls a
counterparty, a bank name and the bank's own reference number where present;
`deliveries.py`'s `parse_shipment()` looks for a shipment-status verb
("shipped", "out for delivery", "delivered") plus a carrier name and an AWB
number. Both are explicit about being heuristic, not authoritative — Indian
bank and courier SMS have no shared format, formats vary bank-to-bank and
carrier-to-carrier, and an unrecognised message is silently skipped rather
than guessed at, which is the same failure-mode choice `_contacts_cache`
made in §2.15 (degrade the feature, don't guess at the data). A message
counted wrong is worse than one not counted at all.

**Two parsing bugs, both caught by testing against real messages captured
earlier this session, not by inventing test fixtures.** A first
`_COUNTERPARTY_FROM` implementation stopped only at a comma or newline; a
real credit SMS ("...from RAVI KUMAR on 20-08-26. Ref No 998877.") has
neither before the date clause, so the match ran to the end of the string
and swallowed the date and reference number into the counterparty name.
Fixed by adding a period and an `on <date>`/`Ref` lookahead to the stop
condition. Separately, `_FUTURE_HINTS` originally included the bare string
`"e-mandate"` to exclude a future-dated mandate notice ("Rs.1000.00 will be
deducted on...") — which also excluded a genuinely completed e-mandate debit
*confirmation*, a real transaction that should count. Narrowed to the
future-tense phrases themselves ("will be deducted", "will be debited",
"scheduled to be", "is due on").

**Delivery status only ever advances, never regresses.** A shipment
produces several SMS over its life — shipped, then out for delivery, then
delivered — arriving in that order most of the time but not guaranteed to.
`DeliveryStore.upsert()` keys on the tracking number when the SMS has one,
and only writes a new status if it outranks (`_STATUS_RANK`) what's already
stored, so a late-arriving "shipped" message after a "delivered" one has
already landed cannot un-deliver a package. Without a tracking number (some
carriers' SMS don't include one), the fallback key is `carrier + day`,
which cannot tell two same-day shipments from the same carrier apart — an
accepted, documented gap rather than a silent one.

Both are on-demand only in this version — `scan_bank_sms` / `scan_delivery_sms`
plus `expense_report` / `pending_deliveries`, no background sweep — since a
ledger silently mis-parsing or double-counting unattended is a worse failure
mode than one that only runs when asked. Both need `integrations.phone.enabled`
as well as their own flag, gated in the registry's `_REQUIRES` the same way
as every other credential-dependent tool group.

**Weather is a thin wrapper: no client class, no state beyond an in-process
geocoding cache.** `peter/integrations/weather.py` is the one integration in
this codebase that isn't a package — every other one (`mail/`, `google/`,
`telegram/`, `phone/`, `dev/`, `desktop/`) holds a stateful connection or has
several files; this is one stateless module making two possible HTTP calls
(geocode, then forecast) via `urllib`, matching the stdlib-only pattern
already established by `telegram/api.py`. Open-Meteo specifically because it
needs no API key — the one integration here that doesn't need a line in
`.env`. A location name is geocoded once and cached for the process
lifetime (`_geocode_cache`, keyed on the lowercased name) — coordinates for
a named place do not go stale within a session, so a second lookup of the
same city is free. `get_weather(location=...)` accepts an ad-hoc override
without touching config, geocoded and cached the same way. Folded into the
morning briefing (`briefing.py`'s `_SECTIONS["weather"]`) behind the same
opt-in-via-`include` and graceful-degradation machinery every other optional
section already uses — an unconfigured location lands in the "not set up"
bucket via `NotConfiguredError`, exactly like an unconfigured `waiting_on`
or `pull_requests` section, not a crash.

**Voice-note transcription needed almost no new code at all.**
`transcribe_phone_voice_note` chains two pipelines that already existed
end-to-end: `adb.pull_latest_file()` (built for `save_phone_screenshot`,
already generic over a list of remote directories and a local destination —
only the directory list changes, to WhatsApp's voice-note folder and common
recorder-app paths) into `meeting_notes.transcribe()` (built for meeting
recordings, taking any audio `Path` and returning text via local
faster-whisper — no meeting-specific logic in the function itself). The
whole tool is two existing calls in sequence with error handling around
each; the only genuinely new surface is the `voice_note_dirs` config list.

---

## Appendix — file map

```
peter_3.0/
├── config/config.yml         # everything non-secret, committed
├── .env                      # secrets only, gitignored
├── peter/
│   ├── main.py                # §2.9 supervisor loop
│   ├── agent/
│   │   ├── brain.py            # §2.2 turn orchestration, §2.17 spend recording
│   │   ├── subagents.py        # §2.16 per-page fan-out for comparisons
│   │   ├── prompts.py          # §2.2.3 frozen system prompt
│   │   └── registry.py         # §2.3 @peter_tool → schema + tier
│   ├── llm/
│   │   ├── vision.py            # §2.14 one-shot image calls, per vendor
│   │   ├── loop.py              # §2.2.1 the one shared tool-call loop
│   │   ├── base.py              # vendor-neutral types
│   │   ├── factory.py           # picks + builds the active provider
│   │   ├── pricing.py           # $/Mtok table, cost estimation
│   │   ├── router.py            # §2.2.4 light/heavy classification (Gemini auto)
│   │   └── providers/           # §2.2.2 anthropic / openai / gemini
│   │       └── gemini_provider.py  # §2.2.4 auto-routing, fallback, explicit cache
│   ├── policy/
│   │   ├── gate.py               # §2.4 allow / confirm / handoff / deny
│   │   └── audit.py              # append-only JSONL trail
│   ├── tools/                   # §2.3 the 110 registered tools
│   ├── memory/store.py          # §2.5 SQLite + FTS5
│   ├── scheduler/jobs.py        # §2.8 APScheduler + SQLite jobstore
│   ├── meeting_prep.py          # §2.10 calendar + memory nudge
│   ├── inbox_digest.py          # §2.10 read-only "needs a reply" scan
│   ├── focus.py                 # §2.10 mute / time-box / restore
│   ├── waiting_on.py            # §2.10 sent mail nobody answered
│   ├── telegram_bridge.py       # §2.11 inbound thread, remote confirmer
│   ├── meeting_notes.py         # §2.12 record → transcribe → summarise
│   ├── worklog.py               # §2.13 the day's join, and the standup
│   ├── ci_watch.py              # §2.13 build-failure watcher (dedup + priming)
│   ├── price_watch.py           # §2.14 watch list + the alert rule
│   ├── docs_index.py            # §2.14 FTS5 over folders, cited answers
│   ├── workspace.py             # §2.14 save / restore open applications
│   ├── spend.py                 # §2.17 the cost ledger and the daily cap
│   ├── expenses.py               # §2.18 bank/UPI SMS -> personal spend ledger
│   ├── deliveries.py             # §2.18 courier SMS -> shipment tracker
│   ├── ui/
│   │   ├── progress.py           # §2.9b CLI status line, branded spinners
│   │   ├── confirm.py            # voice-mode spoken yes/no confirmer
│   │   └── tray.py               # pystray icon, mic-state, confirm toasts
│   ├── integrations/
│   │   ├── mail/                 # §2.7 IMAP/SMTP
│   │   ├── google/               # §2.7 Calendar/Tasks OAuth
│   │   ├── browser/              # §2.6 Playwright + purchase interlock
│   │   ├── telegram/             # §2.11 Bot API over urllib, push()
│   │   ├── dev/                  # §2.13 git + gh, both as subprocesses
│   │   ├── phone/                # §2.15 ADB: SMS, calls, contacts, screen, files
│   │   ├── weather.py            # §2.18 Open-Meteo, no API key, geocode cache
│   │   └── desktop/              # §2.6b apps, bookmarks, YouTube, media, folders
│   │       ├── browsers.py        # open_url + bookmark reading (Firefox/Chromium)
│   │       ├── matching.py        # fuzzy rank() for "open the staging dashboard"
│   │       ├── media.py           # real media-key events
│   │       ├── places.py          # standard Windows folders + configured ones
│   │       ├── youtube.py         # top-result search, no API key
│   │       ├── volume.py          # §2.10 pycaw get/set, shared by focus mode
│   │       └── recorder.py        # §2.12 WASAPI loopback → WAV, off-thread writes
│   ├── voice/                   # §2.1 wake / stt / tts / audio
│   └── core/
│       ├── config.py             # config.yml + .env loader/validator
│       ├── services.py           # the lazy ServiceContainer
│       ├── errors.py             # PeterError hierarchy
│       ├── logging.py            # literal-value secret redaction, shared Console
│       ├── retry.py              # §2.2.4 call_with_retry: backoff + jitter
│       ├── db.py                 # shared SQLite helper: WAL, lock, busy timeout
│       └── notify.py             # §2.11 toast + Telegram, both best-effort
└── tests/                     # one test file per subsystem above
```
