# memory

Long-term memory: durable facts and standing preferences, backed by SQLite +
FTS5 (`peter/memory/store.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `remember_fact` | write | Store a durable fact under a stable key (overwrites on reuse). |
| `recall` | read | Keyword-search stored facts. |
| `forget_fact` | write | Delete a fact permanently. |
| `set_preference` | write | Record a standing instruction about how Peter should behave. |
| `list_preferences` | read | List every preference in effect. |
| `forget_preference` | write | Remove a preference. |

## Setup

Always registered — core infrastructure, not an optional integration. No
config flag gates the module itself; `MemoryConfig.embeddings`
(`EmbeddingsConfig`) tunes the *hybrid* retrieval layer underneath it
(`enabled`, `top_k_facts` default 5, `similarity_threshold` default 0.15,
`top_k_contextual_preferences`) but keyword search alone still works with no
embedding model present.

## Design notes & gotchas

- **These are plain registered tools, not the SDK's built-in file-based
  memory tool — on purpose.** Routing every memory write through the same
  `@peter_tool` → policy gate → audit log path every other tool uses means a
  memory write is a `write`-tier action like any other: an assistant that
  silently rewrites what it believes about you is worse than one that asks.
- **Recall is automatic — `recall` is the exception path, not the normal
  one.** Every turn, `Brain._build_user_content()` runs its own keyword
  search over the user's message and silently prepends whatever facts share
  a token with it. The `recall` tool's docstring says to only call it when
  something wasn't already injected — e.g. "what do you know about me?".
  Don't treat `recall` as the primary way facts reach the model.
- **Hybrid retrieval, not keyword-only.** `peter/memory/embeddings.py` adds a
  local, brute-forced cosine-similarity index on top of the FTS5 search —
  measured on 130 stored facts, FTS5 alone recalled 3/10 paraphrased
  questions; hybrid at `similarity_threshold=0.15` recalls 10/10 while
  injecting *fewer* facts per turn (3.9 vs. keyword-only's flat 8). Both
  halves are unioned, not one replacing the other — embeddings catch
  paraphrase ("how do I get to work" → "route 70 bus"), keywords catch exact
  ids/numbers a sentence model has no useful vector for. No new dependency:
  `onnxruntime`/`tokenizers`/`numpy` were already installed for the voice
  pipeline; this costs one ~23MB model file. Fully optional — no model file
  means `available()` is False and everything falls back to the FTS5 path
  that was always there.
- **Preferences carry a scope**, `always` (injected unconditionally — the
  default) or `contextual` (retrieved like a fact). A preference stored
  before scopes existed has no row and is treated as `always`, so old
  databases keep prior behaviour unchanged.
- **A correction can promote itself into a preference/fact automatically** —
  see `peter/agent/learning.py` (§2.5b in ARCHITECTURE.md). A keyword
  pre-filter (`looks_like_correction()`) catches something like "no, always
  keep it short" and triggers one isolated, cheap model call that decides
  whether it generalises; if not, the documented default is `NOTHING` — it
  prefers silence over learning the wrong lesson permanently. This is a
  separate code path from these tools, not something the model calls
  directly, but it writes through the exact same `set_preference`/
  `remember_fact` storage.
- `max_preferences` (`LearningConfig`, default 25) is a **refusal** limit,
  not an eviction limit — at the cap, Peter declines to learn something new
  rather than silently dropping one you set on purpose.
- Speech is tokenized before reaching FTS5's query parser, since raw
  transcribed speech can contain characters that are meaningful *syntax* to
  FTS5 and would otherwise fail to parse.

## Future extension ideas

- `recall` has no way to list facts by prefix/category, only free-text
  search — fine at a personal assistant's scale, would need an index
  rethink at real volume.
- No tool surfaces which facts were actually injected on a given turn for
  debugging — would help diagnose a "why did it say that" question, though
  the audit log (`data/audit.jsonl`) captures tool calls, not injected
  context.
