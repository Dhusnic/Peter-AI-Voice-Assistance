"""peter.skills.system.tools.search_files.

Only search_files is covered here — see the module docstring in
peter/skills/system/tools.py for why it changed: a whole-home search used to
take 13+ seconds walking .venv/node_modules/extension-cache trees, and a
single unreadable subtree (a broken junction, a >260-char path) aborted the
entire search and discarded every hit already found.
"""

from peter.agent import registry


def _search_files(**kwargs):
    registry.reset_for_tests()
    from peter.skills.system import tools  # noqa: F401

    return registry.get_record("search_files").raw_fn(**kwargs)


def test_finds_a_matching_file(tmp_path):
    (tmp_path / "report.pdf").write_text("x")
    (tmp_path / "other.txt").write_text("x")

    result = _search_files(directory=str(tmp_path), pattern="*.pdf", max_results=50)

    assert "report.pdf" in result
    assert "other.txt" not in result


def test_matching_is_case_insensitive(tmp_path):
    (tmp_path / "Report.PDF").write_text("x")

    result = _search_files(directory=str(tmp_path), pattern="*.pdf", max_results=50)

    assert "Report.PDF" in result


def test_nested_matches_are_found(tmp_path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "deep.txt").write_text("x")

    result = _search_files(directory=str(tmp_path), pattern="*.txt", max_results=50)

    assert "deep.txt" in result


def test_no_matches_reports_that_plainly(tmp_path):
    result = _search_files(directory=str(tmp_path), pattern="*.nope", max_results=50)
    assert "Nothing under" in result
    assert "*.nope" in result


def test_a_non_directory_is_reported_without_walking_anything(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")

    result = _search_files(directory=str(f), pattern="*", max_results=50)

    assert "is not a directory" in result


def test_stops_at_max_results(tmp_path):
    for i in range(10):
        (tmp_path / f"note{i}.txt").write_text("x")

    result = _search_files(directory=str(tmp_path), pattern="*.txt", max_results=3)

    assert len(result.splitlines()) == 3


def test_heavy_dev_directories_are_pruned_entirely(tmp_path):
    """.venv/node_modules/__pycache__ etc. are never descended into — the
    fix for the 13.8s whole-home search that spent nearly all of it walking
    exactly these kinds of trees."""
    for junk_dir in (".venv", "node_modules", "__pycache__", ".git"):
        d = tmp_path / junk_dir
        d.mkdir()
        (d / "match.txt").write_text("x")
    (tmp_path / "real.txt").write_text("x")

    result = _search_files(directory=str(tmp_path), pattern="*.txt", max_results=50)

    assert result == str(tmp_path / "real.txt")


def _fake_clock(monkeypatch, system_tools, *, timeout_after_directory: int):
    """A deterministic stand-in for time.monotonic(): the deadline-setup call
    reads 0, and every per-directory budget check after the Nth one reads
    past any real budget. Real elapsed time is too fast and too coarse on
    modern hardware to reliably trip a "budget already expired" branch from
    a real clock in a unit test, so the clock itself is faked instead."""
    calls = {"n": 0}

    def monotonic():
        calls["n"] += 1
        return 0.0 if calls["n"] <= timeout_after_directory else 1e9

    monkeypatch.setattr(system_tools.time, "monotonic", monotonic)


def test_time_budget_stops_the_search_and_says_so(tmp_path, monkeypatch):
    """No match sits at the root, and the budget expires right after os.walk
    finishes reading it, before it can descend into the subdirectory that
    has one — the honest "stopped" message, not a silent empty result
    indistinguishable from "genuinely nothing"."""
    from peter.skills.system import tools as system_tools

    # 1st call: deadline setup. 2nd call: budget check after the root
    # directory (no match there) is processed — this is the one that trips.
    _fake_clock(monkeypatch, system_tools, timeout_after_directory=1)
    sub = tmp_path / "a"
    sub.mkdir()
    (sub / "findme.txt").write_text("x")

    result = _search_files(directory=str(tmp_path), pattern="*.txt", max_results=50)

    assert "stopped after" in result
    assert "findme.txt" not in result


def test_time_budget_still_returns_whatever_was_already_found(tmp_path, monkeypatch):
    """The budget is checked after finishing the current directory's own
    files, not before — a match sitting right at the root must survive even
    when the budget is already exhausted by the time it's checked."""
    from peter.skills.system import tools as system_tools

    _fake_clock(monkeypatch, system_tools, timeout_after_directory=1)
    (tmp_path / "findme.txt").write_text("x")

    result = _search_files(directory=str(tmp_path), pattern="*.txt", max_results=50)

    assert "findme.txt" in result
    assert "stopped after" in result


def test_an_unreadable_subtree_does_not_abort_the_whole_search(tmp_path, monkeypatch):
    """os.walk's onerror must swallow a broken-subtree OSError and keep
    going, unlike the old rglob-based version, which propagated the first
    OSError anywhere in the tree straight out of the loop and discarded
    every hit already found before it."""
    (tmp_path / "findme.txt").write_text("x")

    import os as os_module

    real_walk = os_module.walk

    def flaky_walk(top, onerror=None, **kwargs):
        if onerror is not None:
            onerror(OSError("simulated broken junction"))
        yield from real_walk(top, onerror=onerror, **kwargs)

    from peter.skills.system import tools as system_tools

    monkeypatch.setattr(system_tools.os, "walk", flaky_walk)

    result = _search_files(directory=str(tmp_path), pattern="*.txt", max_results=50)

    assert "findme.txt" in result
