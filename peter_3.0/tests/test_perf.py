"""Per-tool-call performance profiling: the store, phases, and reports."""

import time

import pytest

from peter import perf
from peter.perf import PerfLog, _percentile


@pytest.fixture
def store(tmp_path) -> PerfLog:
    s = PerfLog(tmp_path / "perf.db")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _reset_thread_local():
    perf.reset_phases()
    yield
    perf.reset_phases()


# --------------------------------------------------------------- percentile
def test_percentile_of_a_single_value():
    assert _percentile([5.0], 95) == 5.0


def test_percentile_of_empty_is_zero():
    assert _percentile([], 50) == 0.0


def test_percentile_matches_known_values():
    values = sorted([10.0, 20.0, 30.0, 40.0, 50.0])
    assert _percentile(values, 50) == 30.0
    assert _percentile(values, 0) == 10.0
    assert _percentile(values, 100) == 50.0


# -------------------------------------------------------------------- phase
def test_phase_accumulates_time():
    with perf.phase("step"):
        time.sleep(0.01)
    phases = perf.take_phases()
    assert phases is not None
    assert phases["step"] >= 5  # ms, generous for scheduler slack


def test_phase_sums_repeated_calls_with_the_same_name():
    with perf.phase("loop"):
        time.sleep(0.005)
    with perf.phase("loop"):
        time.sleep(0.005)
    phases = perf.take_phases()
    assert phases["loop"] >= 6


def test_take_phases_returns_none_when_nothing_was_recorded():
    assert perf.take_phases() is None


def test_take_phases_resets_for_the_next_call():
    with perf.phase("a"):
        pass
    perf.take_phases()
    assert perf.take_phases() is None


def test_reset_phases_clears_leftovers_from_a_previous_call():
    with perf.phase("stale"):
        pass
    perf.reset_phases()
    assert perf.take_phases() is None


# --------------------------------------------------------------------- store
def test_record_and_summary_round_trip(store):
    store.record("check_email", wall_ms=800, cpu_ms=3, wait_ms=797)
    store.record("check_email", wall_ms=840, cpu_ms=4, wait_ms=836)

    stats = store.summary(hours=24)

    assert len(stats) == 1
    s = stats[0]
    assert s.tool == "check_email"
    assert s.calls == 2
    assert s.avg_wall_ms == pytest.approx(820, rel=0.01)
    assert s.errors == 0


def test_summary_excludes_failed_calls_by_default(store):
    store.record("flaky", wall_ms=1, cpu_ms=1, wait_ms=0, ok=False)
    store.record("flaky", wall_ms=500, cpu_ms=5, wait_ms=495, ok=True)

    stats = store.summary(hours=24)

    assert len(stats) == 1
    assert stats[0].calls == 1
    assert stats[0].avg_wall_ms == pytest.approx(500)
    assert stats[0].errors == 1


def test_summary_can_include_errors(store):
    store.record("flaky", wall_ms=1, cpu_ms=1, wait_ms=0, ok=False)
    store.record("flaky", wall_ms=500, cpu_ms=5, wait_ms=495, ok=True)

    stats = store.summary(hours=24, include_errors=True)

    assert stats[0].calls == 2


def test_summary_computes_cpu_share(store):
    store.record("cpu_heavy", wall_ms=100, cpu_ms=90, wait_ms=10)
    stats = store.summary(hours=24)
    assert stats[0].cpu_share == pytest.approx(0.9)


def test_cpu_share_is_clipped_to_one(store):
    # Measurement noise could in principle push cpu_ms slightly above wall_ms.
    store.record("noisy", wall_ms=10, cpu_ms=12, wait_ms=0)
    stats = store.summary(hours=24)
    assert stats[0].cpu_share == 1.0


def test_summary_ignores_calls_outside_the_window(store):
    old = time.time() - 999_999
    store.record("ancient", wall_ms=1, cpu_ms=1, wait_ms=0, when=old)
    assert store.summary(hours=1) == []


def test_summary_sorted_by_total_time_spent(store):
    store.record("rare_but_slow", wall_ms=5000, cpu_ms=10, wait_ms=4990)
    for _ in range(100):
        store.record("frequent_and_fast", wall_ms=1, cpu_ms=1, wait_ms=0)

    stats = store.summary(hours=24)

    # frequent_and_fast: 100 * 1ms = 100ms total; rare_but_slow: 5000ms total.
    assert stats[0].tool == "rare_but_slow"


def test_percentiles_reflect_the_spread(store):
    for ms in (10, 20, 30, 40, 100):
        store.record("varied", wall_ms=ms, cpu_ms=1, wait_ms=ms - 1)

    s = store.summary(hours=24)[0]

    assert s.p50_wall_ms == pytest.approx(30, rel=0.01)
    assert s.max_wall_ms == 100


def test_phase_breakdown_averages_across_calls(store):
    store.record("browser_search", wall_ms=100, cpu_ms=5, wait_ms=95,
                  phases={"http_request": 80, "json_parse": 10})
    store.record("browser_search", wall_ms=120, cpu_ms=5, wait_ms=115,
                  phases={"http_request": 100, "json_parse": 12})

    breakdown = store.phase_breakdown("browser_search", hours=24)
    by_name = {name: avg for name, avg, count in breakdown}

    assert by_name["http_request"] == pytest.approx(90)
    assert by_name["json_parse"] == pytest.approx(11)
    assert breakdown[0][0] == "http_request"  # slowest phase first


def test_phase_breakdown_empty_when_nothing_recorded_phases(store):
    store.record("plain_tool", wall_ms=10, cpu_ms=1, wait_ms=9)
    assert store.phase_breakdown("plain_tool") == []


def test_prune_drops_old_rows_only(store):
    store.record("recent", wall_ms=1, cpu_ms=1, wait_ms=0)
    store.record("old", wall_ms=1, cpu_ms=1, wait_ms=0, when=time.time() - 40 * 86400)

    removed = store.prune(keep_days=30)

    assert removed == 1
    remaining = store.summary(hours=24 * 365)
    assert {s.tool for s in remaining} == {"recent"}


def test_autoprune_runs_after_enough_inserts(store, monkeypatch):
    calls = []
    monkeypatch.setattr(store, "prune", lambda **kw: calls.append(1) or 0)

    for _ in range(perf._AUTOPRUNE_EVERY):
        store.record("t", wall_ms=1, cpu_ms=1, wait_ms=0)

    assert calls == [1]


# ------------------------------------------------------------------ reports
def test_spoken_summary_reports_busiest_tools(container, tmp_path):
    container.perf = PerfLog(tmp_path / "spoken.db")
    container.perf.record("check_email", wall_ms=800, cpu_ms=3, wait_ms=797)
    container.perf.record("browser_search", wall_ms=2400, cpu_ms=40, wait_ms=2360)

    text = perf.spoken_summary(hours=24)

    assert "check_email" in text
    assert "browser_search" in text


def test_spoken_summary_with_no_data_says_so(container, tmp_path):
    container.perf = PerfLog(tmp_path / "empty.db")
    assert "No tool calls recorded" in perf.spoken_summary(hours=24)


def test_spoken_summary_flags_cpu_bound_candidates(container, tmp_path):
    container.perf = PerfLog(tmp_path / "cpu.db")
    container.perf.record("heavy_calc", wall_ms=300, cpu_ms=280, wait_ms=20)

    text = perf.spoken_summary(hours=24)

    assert "Worth a native rewrite" in text
    assert "heavy_calc" in text


def test_spoken_summary_says_nothing_crosses_the_bar_when_io_bound(container, tmp_path):
    container.perf = PerfLog(tmp_path / "io.db")
    container.perf.record("check_email", wall_ms=800, cpu_ms=3, wait_ms=797)

    assert "no rewrite candidates" in perf.spoken_summary(hours=24)


def test_full_report_includes_the_table_header(container, tmp_path):
    container.perf = PerfLog(tmp_path / "full.db")
    container.perf.record("check_email", wall_ms=800, cpu_ms=3, wait_ms=797)

    text = perf.full_report(hours=24)

    assert "Tool" in text and "Calls" in text
    assert "check_email" in text


def test_full_report_includes_phase_breakdown_when_present(container, tmp_path):
    container.perf = PerfLog(tmp_path / "phased.db")
    container.perf.record("browser_search", wall_ms=100, cpu_ms=5, wait_ms=95,
                          phases={"http_request": 80})

    text = perf.full_report(hours=24)

    assert "phase breakdown" in text
    assert "http_request" in text


# -------------------------------------------------------------------- tool
def test_performance_report_tool_delegates(container, tmp_path):
    from peter.agent import registry

    registry.reset_for_tests()
    from peter.skills.performance import tools as perf_tools  # noqa: F401

    container.perf = PerfLog(tmp_path / "tool.db")
    container.perf.record("check_email", wall_ms=800, cpu_ms=3, wait_ms=797)

    result = registry.get_record("performance_report").raw_fn()

    assert "check_email" in result
