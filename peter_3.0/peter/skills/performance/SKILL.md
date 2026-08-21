# performance

Per-tool timing report: busiest tools, native-rewrite candidates
(`peter/perf.py`).

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `performance_report` | read | Spoken summary: busiest tools, and any flagged as worth a native rewrite. |

## Setup

Always registered — no config flag, no credential. Backs
`python -m peter.main --perf-report` (the detailed table) and this tool (the
short spoken version); both read the same `PerfLog` store.

## Design notes & gotchas

- **Purely a reporting layer over data collected with zero per-tool code
  changes.** Every one of the registered tools is timed automatically at
  the exact point `policy/gate.py` was already timing calls for the audit
  log — `_execute()` brackets the call with both `time.perf_counter()`
  (wall clock) and `peter.perf.cpu_time()` (this thread's own CPU seconds,
  via `time.thread_time()`, not `process_time()` — so one tool's number is
  never inflated by unrelated CPU work on another thread at the same
  moment).
- **The wall/CPU/wait split is the entire idea.** `cpu_ms` is what the
  thread actually spent computing; `wait_ms = wall_ms - cpu_ms` is
  everything else — network round trip, a subprocess (`adb`, `gh`), disk,
  or GIL contention with a concurrent scheduler/Telegram thread. `wait_ms`
  is therefore not a pure I/O measurement, but that imprecision doesn't
  matter for this report's purpose: a call that's mostly `wait_ms` for
  *any* reason cannot be sped up by rewriting that tool in a faster
  language, which is the only question this exists to answer.
- **The two-factor bar for "maybe rewrite this" is checked automatically,
  not eyeballed.** A tool is flagged only when its average CPU time is both
  large in absolute terms (`CPU_CANDIDATE_MS = 200`) *and* most of what the
  call actually takes (`CPU_CANDIDATE_SHARE = 0.5`) — a tool that's slow but
  waiting, or fast but CPU-heavy for a few ms either way, clears neither bar
  on purpose.
- `PerfLog` keeps only 30 days by default and prunes itself automatically
  every 500 inserts — unlike `spend.py`'s `SpendLog`, which keeps a year and
  whose `prune()` exists but is never actually called from anywhere (a
  pre-existing gap this module deliberately did not repeat).
- Percentiles (p50/p95) are computed in Python after a per-tool fetch,
  since SQLite has no percentile function and per-tool row counts are small
  enough for this to be simpler and exact rather than clever and
  approximate.
- A tool can opt into a finer breakdown via `perf.phase("name")` (a
  thread-local context manager) to time its own named sub-steps — additive
  on top of the automatic split, worth reaching for only on a tool a first
  report has already flagged. A call that never touches `phase()` costs
  nothing extra.
- **The table starts empty on a fresh install** — it only knows about calls
  made since this was added. The honest answer to "should anything move to
  Rust" is "check back after a week of normal use," not a guess either way.

## Future extension ideas

- No tool clears/resets the perf log on demand — only the automatic 30-day/
  500-insert pruning exists.
- No per-tool opt-out from timing (nor is one obviously needed at this
  scale) — every registered tool is timed unconditionally today.
