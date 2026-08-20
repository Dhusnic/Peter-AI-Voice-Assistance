"""Per-tool-call performance profiling: wall time, split into CPU vs. wait.

Every tool call already passes through `policy/gate.py`, which was already
timing it end-to-end for the audit log. This adds one more measurement at
that same point, for free, on every one of the 136 tools with no change to
any of them:

    wall_ms   total time the call took, start to finish
    cpu_ms    time this thread actually spent executing on a core
              (`time.thread_time()` — deliberately the *thread's* clock, not
              the process's `time.process_time()`, so a busy scheduler job
              or the Telegram poll loop running concurrently on another
              thread never gets blended into this call's number)
    wait_ms   wall_ms - cpu_ms: time this thread was blocked, not running —
              a network round trip, a subprocess (adb, gh), a disk read

That split answers the one question that actually matters for the "should
this be Rust" decision worked through in the architecture notes: a tool
that is mostly `wait_ms` cannot be sped up by a faster language, because it
was never computing in the first place. A tool with a real `cpu_ms` number —
in absolute terms, not just as a share of a tiny wall time — is the only
kind of candidate worth a native rewrite (see `report()`'s threshold below).

**Caveat, stated plainly rather than overclaimed:** `wait_ms` also absorbs
any time this thread spent waiting for the GIL while another thread was
running CPU-bound Python — which is not "I/O wait" in the strict sense, but
still isn't something a faster language for *this* tool would fix, so the
approximation still points at the right answer. On Windows specifically,
`thread_time()`'s resolution is tied to the OS clock tick (commonly ~15ms),
so `cpu_ms` readings for calls faster than that are quantised and noisy —
treat single-digit-millisecond CPU numbers as "small," not as precise.

A tool can additionally opt into a finer breakdown with the `phase()`
context manager below — entirely additive, and only worth doing on the
handful of tools a first pass of `report()` already flagged as worth a
closer look.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from peter.core.db import Db

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS perf_calls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tool       TEXT NOT NULL,
    wall_ms    REAL NOT NULL,
    cpu_ms     REAL NOT NULL,
    wait_ms    REAL NOT NULL,
    phases     TEXT,
    ok         INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS perf_tool ON perf_calls(tool);
CREATE INDEX IF NOT EXISTS perf_created ON perf_calls(created_at);
"""

# Below this many rows inserted, don't bother checking whether a prune is due
# — pruning itself is a full table scan-ish DELETE, not worth it on every call.
_AUTOPRUNE_EVERY = 500


def cpu_time() -> float:
    """CPU seconds consumed by *this thread* so far. See module docstring for
    why thread_time() and not process_time()."""
    try:
        return time.thread_time()
    except (AttributeError, RuntimeError):  # pragma: no cover - rare platform gap
        return time.process_time()


# ---------------------------------------------------------------- phases
# Thread-local so concurrent tool calls (main turn loop + scheduler jobs +
# Telegram bridge, all potentially calling tools on their own threads) never
# see each other's phase timings.
_local = threading.local()


def phase(name: str):
    """Time a named sub-step inside a tool body, for a finer breakdown than
    the automatic CPU/wait split gives you.

    Purely opt-in: a tool that never calls this still gets the automatic
    per-call wall/cpu/wait split for free. Reach for this only on a tool
    `report()` has already flagged as worth a closer look, e.g.:

        with perf.phase("http_request"):
            response = urlopen(...)
        with perf.phase("json_parse"):
            data = json.loads(response.read())

    Multiple calls with the same name in one tool invocation accumulate
    rather than overwrite, so a phase inside a loop still sums correctly.
    """
    return _PhaseTimer(name)


class _PhaseTimer:
    __slots__ = ("name", "started")

    def __init__(self, name: str):
        self.name = name
        self.started = 0.0

    def __enter__(self) -> "_PhaseTimer":
        self.started = time.perf_counter()
        return self

    def __exit__(self, *exc_info) -> None:
        elapsed_ms = (time.perf_counter() - self.started) * 1000
        stack = getattr(_local, "phases", None)
        if stack is None:
            stack = {}
            _local.phases = stack
        stack[self.name] = stack.get(self.name, 0.0) + elapsed_ms


def record_phase(name: str, elapsed_ms: float) -> None:
    """Add a precomputed duration to the current call's phase breakdown.

    `phase()`'s `with` block can only time a span this thread is inside from
    start to finish. Some sub-steps finish asynchronously on another thread
    (TTS's first audio block, played back by the speaker's worker thread) —
    the caller times those itself and reports the result here, on the same
    thread that will later call `take_phases()`, so it still composes into
    one breakdown per call.
    """
    stack = getattr(_local, "phases", None)
    if stack is None:
        stack = {}
        _local.phases = stack
    stack[name] = stack.get(name, 0.0) + elapsed_ms


def reset_phases() -> None:
    """Clear any phases left over from a previous call on this thread.
    Called by the policy gate immediately before a tool body runs."""
    _local.phases = {}


def take_phases() -> dict[str, float] | None:
    """Pop whatever phases this call recorded, or None if it recorded none.
    Called by the policy gate immediately after a tool body returns."""
    stack = getattr(_local, "phases", None)
    if not stack:
        return None
    _local.phases = {}
    return stack


# ------------------------------------------------------------------- store
@dataclass(slots=True)
class ToolPerf:
    tool: str
    calls: int
    avg_wall_ms: float
    p50_wall_ms: float
    p95_wall_ms: float
    max_wall_ms: float
    avg_cpu_ms: float
    avg_wait_ms: float
    cpu_share: float  # avg_cpu_ms / avg_wall_ms, clipped to [0, 1]
    errors: int


class PerfLog:
    def __init__(self, db_path: Path):
        self.db = Db(db_path, _SCHEMA)
        self._inserts = 0

    def close(self) -> None:
        self.db.close()

    def record(
        self,
        tool: str,
        wall_ms: float,
        cpu_ms: float,
        wait_ms: float,
        *,
        phases: dict[str, float] | None = None,
        ok: bool = True,
        when: float | None = None,
    ) -> None:
        self.db.execute(
            """INSERT INTO perf_calls
                   (tool, wall_ms, cpu_ms, wait_ms, phases, ok, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                tool, max(0.0, wall_ms), max(0.0, cpu_ms), max(0.0, wait_ms),
                json.dumps(phases) if phases else None,
                1 if ok else 0,
                when if when is not None else time.time(),
            ),
        )
        self._inserts += 1
        if self._inserts % _AUTOPRUNE_EVERY == 0:
            try:
                self.prune()
            except Exception:
                log.debug("perf autoprune failed", exc_info=True)

    # ---------------------------------------------------------------- reading
    def summary(self, hours: float = 24 * 7, *, include_errors: bool = False) -> list[ToolPerf]:
        """Per-tool aggregates over the last `hours`, tool with the most
        wall-clock time spent last. Failed calls are excluded by default —
        an error that returns in a millisecond would otherwise drag a
        tool's average down and make it look faster than it actually runs.
        """
        since = time.time() - hours * 3600
        clause = "created_at >= ?" + ("" if include_errors else " AND ok = 1")
        rows = self.db.query(
            f"SELECT tool, wall_ms, cpu_ms, wait_ms FROM perf_calls WHERE {clause}",
            (since,),
        )

        by_tool: dict[str, list] = {}
        for r in rows:
            by_tool.setdefault(r["tool"], []).append(r)
        # Shown regardless of include_errors: even when errored calls are
        # mixed into the averages, it's still worth knowing how many of them
        # there were.
        error_counts = self._error_counts(since)

        results = []
        for tool, tool_rows in by_tool.items():
            walls = sorted(r["wall_ms"] for r in tool_rows)
            avg_wall = sum(walls) / len(walls)
            avg_cpu = sum(r["cpu_ms"] for r in tool_rows) / len(tool_rows)
            avg_wait = sum(r["wait_ms"] for r in tool_rows) / len(tool_rows)
            results.append(ToolPerf(
                tool=tool,
                calls=len(tool_rows),
                avg_wall_ms=avg_wall,
                p50_wall_ms=_percentile(walls, 50),
                p95_wall_ms=_percentile(walls, 95),
                max_wall_ms=walls[-1],
                avg_cpu_ms=avg_cpu,
                avg_wait_ms=avg_wait,
                cpu_share=min(1.0, avg_cpu / avg_wall) if avg_wall > 0 else 0.0,
                errors=error_counts.get(tool, 0),
            ))
        results.sort(key=lambda t: t.avg_wall_ms * t.calls, reverse=True)
        return results

    def _error_counts(self, since: float) -> dict[str, int]:
        rows = self.db.query(
            "SELECT tool, COUNT(*) AS n FROM perf_calls "
            "WHERE created_at >= ? AND ok = 0 GROUP BY tool",
            (since,),
        )
        return {r["tool"]: r["n"] for r in rows}

    def phase_breakdown(self, tool: str, hours: float = 24 * 7) -> list[tuple[str, float, int]]:
        """(phase name, average ms, sample count) for one tool, slowest
        phase first — only over calls that actually recorded phases."""
        since = time.time() - hours * 3600
        rows = self.db.query(
            "SELECT phases FROM perf_calls "
            "WHERE tool = ? AND created_at >= ? AND phases IS NOT NULL",
            (tool, since),
        )
        totals: dict[str, float] = {}
        counts: dict[str, int] = {}
        for r in rows:
            try:
                phases = json.loads(r["phases"])
            except (TypeError, ValueError):
                continue
            for name, ms in phases.items():
                totals[name] = totals.get(name, 0.0) + ms
                counts[name] = counts.get(name, 0) + 1
        return sorted(
            ((name, totals[name] / counts[name], counts[name]) for name in totals),
            key=lambda t: t[1], reverse=True,
        )

    def prune(self, keep_days: float = 30) -> int:
        """Drop rows older than `keep_days`. Every tool call writes a row,
        so unlike spend.py's year-long retention, this needs a much shorter
        window to stay small."""
        cutoff = time.time() - keep_days * 86400
        return self.db.execute(
            "DELETE FROM perf_calls WHERE created_at < ?", (cutoff,)
        ).rowcount


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolated percentile. `sorted_values` must already be sorted."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * (pct / 100)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] * (hi - rank) + sorted_values[hi] * (rank - lo)


# ------------------------------------------------------------------ reports
# The two-factor bar from the architecture notes: a tool only earns a second
# look if it is BOTH spending a real amount of absolute CPU time AND that
# time is most of what the call takes — slow-but-waiting is not a candidate,
# and fast-but-CPU-heavy (a few ms) is not worth the PyO3 packaging cost either.
CPU_CANDIDATE_MS = 200.0
CPU_CANDIDATE_SHARE = 0.5


def _fmt_ms(ms: float) -> str:
    return f"{ms / 1000:.2f}s" if ms >= 1000 else f"{ms:.0f}ms"


def spoken_summary(hours: float = 24 * 7, top: int = 5) -> str:
    """Short, speakable summary for the performance_report tool: the
    busiest tools by total time spent, and anything that clears the
    native-rewrite bar."""
    from peter.core.services import services

    stats = services().require_perf().summary(hours)
    if not stats:
        return f"No tool calls recorded in the last {hours:.0f} hour(s)."

    lines = [f"Last {hours:.0f}h: {sum(s.calls for s in stats)} tool call(s) "
             f"across {len(stats)} tool(s)."]

    busiest = stats[:top]
    lines.append("Busiest by total time: " + "; ".join(
        f"{s.tool} ({s.calls}x, avg {_fmt_ms(s.avg_wall_ms)})" for s in busiest
    ))

    candidates = [s for s in stats
                  if s.avg_cpu_ms >= CPU_CANDIDATE_MS and s.cpu_share >= CPU_CANDIDATE_SHARE]
    if candidates:
        lines.append("Worth a native rewrite: " + "; ".join(
            f"{s.tool} (avg {_fmt_ms(s.avg_cpu_ms)} CPU, {s.cpu_share:.0%} of its own time)"
            for s in candidates
        ))
    else:
        lines.append("Nothing crosses the CPU-bound bar this period — no rewrite candidates.")
    return "\n".join(lines)


def full_report(hours: float = 24 * 7) -> str:
    """The detailed table, for `python -m peter.main --perf-report`."""
    from peter.core.services import services

    store = services().require_perf()
    stats = store.summary(hours)
    if not stats:
        return f"No tool calls recorded in the last {hours:.0f} hour(s)."

    total_calls = sum(s.calls for s in stats)
    lines = [f"Peter performance -- last {hours:.0f}h "
             f"({total_calls} calls across {len(stats)} tools)\n"]

    header = (f"{'Tool':<26}{'Calls':>7}{'Avg':>9}{'P50':>9}{'P95':>9}"
              f"{'Max':>9}{'CPU%':>7}{'Wait':>9}{'Err':>5}")
    lines.append(header)
    lines.append("-" * len(header))
    for s in stats:
        lines.append(
            f"{s.tool:<26}{s.calls:>7}{_fmt_ms(s.avg_wall_ms):>9}"
            f"{_fmt_ms(s.p50_wall_ms):>9}{_fmt_ms(s.p95_wall_ms):>9}"
            f"{_fmt_ms(s.max_wall_ms):>9}{s.cpu_share:>7.0%}"
            f"{_fmt_ms(s.avg_wait_ms):>9}{s.errors:>5}"
        )

    candidates = [s for s in stats
                  if s.avg_cpu_ms >= CPU_CANDIDATE_MS and s.cpu_share >= CPU_CANDIDATE_SHARE]
    lines.append("")
    lines.append(
        f"Candidates for a native (Rust/PyO3) rewrite "
        f"-- avg CPU >= {_fmt_ms(CPU_CANDIDATE_MS)} AND >= {CPU_CANDIDATE_SHARE:.0%} of wall time:"
    )
    if candidates:
        for s in candidates:
            lines.append(
                f"  {s.tool}  avg CPU {_fmt_ms(s.avg_cpu_ms)} "
                f"({s.cpu_share:.0%} of {_fmt_ms(s.avg_wall_ms)} wall)"
            )
    else:
        lines.append("  None this period -- nothing here justifies leaving pure Python yet.")

    for s in stats:
        phases = store.phase_breakdown(s.tool, hours)
        if phases:
            lines.append(f"\n{s.tool} -- phase breakdown:")
            for name, avg_ms, count in phases:
                lines.append(f"  {name:<20}{_fmt_ms(avg_ms):>9}  ({count} sample(s))")

    return "\n".join(lines)
