"""Saving and restoring a set of open applications.

Restoring launches executables, so the tests that matter are the ones about
what is *not* launched: shell processes on the ignore list, things already
running, and paths that no longer exist.
"""

from types import SimpleNamespace

import pytest

from peter import workspace
from peter.workspace import App, Snapshot, WorkspaceStore, restore


@pytest.fixture
def store(tmp_path):
    s = WorkspaceStore(tmp_path / "workspaces.db")
    yield s
    s.close()


def snapshot(name="migration", apps=(("D:/apps/code.exe", "code"),), urls=()):
    return Snapshot(
        name=name,
        apps=[App(exe=exe, name=label, title=f"{label} - window") for exe, label in apps],
        urls=list(urls),
    )


def workspace_config(**kwargs):
    base = dict(
        enabled=True, max_apps=25,
        ignore_executables=["explorer.exe", "python.exe"],
    )
    base.update(kwargs)
    return SimpleNamespace(integrations=SimpleNamespace(workspace=SimpleNamespace(**base)))


# ------------------------------------------------------------------- storing
def test_a_snapshot_round_trips(store):
    store.save(snapshot(apps=(("D:/a.exe", "a"), ("D:/b.exe", "b"))))

    loaded = store.get("migration")

    assert [a.name for a in loaded.apps] == ["a", "b"]
    assert loaded.name == "migration"


def test_saving_the_same_name_replaces_it(store):
    store.save(snapshot(apps=(("D:/a.exe", "a"),)))
    store.save(snapshot(apps=(("D:/b.exe", "b"),)))

    assert len(store.all()) == 1
    assert store.get("migration").apps[0].name == "b"


def test_names_are_matched_case_insensitively(store):
    store.save(snapshot(name="Migration"))
    assert store.get("MIGRATION") is not None


def test_a_partial_name_finds_the_workspace(store):
    """Reached by voice: "my migration setup" has to find `migration`."""
    store.save(snapshot(name="migration"))
    assert store.get("my migration setup") is not None


def test_an_unknown_name_finds_nothing(store):
    store.save(snapshot(name="migration"))
    assert store.get("something else") is None


def test_urls_survive_the_round_trip(store):
    store.save(snapshot(urls=("https://example.com/dashboard",)))
    assert store.get("migration").urls == ["https://example.com/dashboard"]


def test_deleting_a_workspace(store):
    store.save(snapshot())
    assert store.delete("migration") is True
    assert store.all() == []


def test_deleting_something_that_is_not_there(store):
    assert store.delete("nothing") is False


def test_the_spoken_form_names_the_apps(store):
    text = snapshot(apps=(("D:/a.exe", "code"), ("D:/b.exe", "chrome"))).spoken()
    assert "2 app(s)" in text
    assert "code, chrome" in text


def test_the_spoken_form_mentions_pages():
    assert "1 page(s)" in snapshot(urls=("https://x",)).spoken()


# ----------------------------------------------------------------- capturing
def test_visible_windows_become_apps(monkeypatch):
    """The enumeration is the whole capture — everything else is filtering."""
    windows = {1: ("Code - main.py", 100), 2: ("", 200), 3: ("Explorer", 300)}
    processes = {100: "D:/apps/code.exe", 200: "D:/apps/hidden.exe",
                 300: "C:/Windows/explorer.exe"}

    _fake_win32(monkeypatch, windows, processes)

    apps = workspace.visible_apps(workspace_config().integrations.workspace)

    assert [a.name for a in apps] == ["code"]


def test_the_same_program_with_two_windows_is_captured_once(monkeypatch):
    windows = {1: ("Code - a.py", 100), 2: ("Code - b.py", 101)}
    processes = {100: "D:/apps/code.exe", 101: "D:/apps/code.exe"}

    _fake_win32(monkeypatch, windows, processes)

    assert len(workspace.visible_apps(workspace_config().integrations.workspace)) == 1


def test_capture_is_bounded_by_max_apps(monkeypatch):
    windows = {i: (f"Window {i}", i) for i in range(1, 20)}
    processes = {i: f"D:/apps/app{i}.exe" for i in range(1, 20)}

    _fake_win32(monkeypatch, windows, processes)

    cfg = workspace_config(max_apps=3).integrations.workspace
    assert len(workspace.visible_apps(cfg)) == 3


def test_capture_without_pywin32_returns_nothing_rather_than_raising(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_win32(name, *args, **kwargs):
        if name.startswith("win32"):
            raise ImportError("no pywin32 here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_win32)

    assert workspace.visible_apps(workspace_config().integrations.workspace) == []


def _fake_win32(monkeypatch, windows, processes):
    """Install fake win32gui/win32process/psutil modules."""
    import sys

    win32gui = SimpleNamespace(
        IsWindowVisible=lambda h: True,
        GetWindowText=lambda h: windows[h][0],
        EnumWindows=lambda visit, extra: [visit(h, extra) for h in windows],
    )
    win32process = SimpleNamespace(
        GetWindowThreadProcessId=lambda h: (0, windows[h][1])
    )

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def exe(self):
            return processes[self.pid]

    psutil = SimpleNamespace(Process=FakeProcess, process_iter=lambda attrs: [])

    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    monkeypatch.setitem(sys.modules, "win32process", win32process)
    monkeypatch.setitem(sys.modules, "psutil", psutil)


# ----------------------------------------------------------------- restoring
@pytest.fixture
def launcher(monkeypatch, tmp_path):
    launched = []
    monkeypatch.setattr(workspace, "_launch", lambda exe: launched.append(exe))
    monkeypatch.setattr(workspace, "_running_executables", lambda: set())
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    return launched


def test_restoring_launches_what_is_missing(launcher):
    result = restore(snapshot(apps=(("D:/apps/code.exe", "code"),)), workspace_config())

    assert launcher == ["D:/apps/code.exe"]
    assert "Opened code" in result


def test_something_already_running_is_not_launched_twice(monkeypatch, launcher):
    monkeypatch.setattr(workspace, "_running_executables", lambda: {"code.exe"})

    result = restore(snapshot(apps=(("D:/apps/code.exe", "code"),)), workspace_config())

    assert launcher == []
    assert "already running" in result


def test_ignored_executables_are_never_relaunched(launcher):
    """Restoring a saved python.exe would relaunch Peter itself."""
    result = restore(
        snapshot(apps=(("C:/py/python.exe", "python"), ("D:/apps/code.exe", "code"))),
        workspace_config(),
    )

    assert launcher == ["D:/apps/code.exe"]
    assert "python" not in result


def test_an_executable_that_no_longer_exists_is_reported(monkeypatch, launcher):
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)

    result = restore(snapshot(apps=(("D:/gone/old.exe", "old"),)), workspace_config())

    assert launcher == []
    assert "Could not open old" in result


def test_one_failing_launch_does_not_stop_the_rest(monkeypatch, launcher):
    def launch(exe):
        if "bad" in exe:
            raise OSError("access denied")
        launcher.append(exe)

    monkeypatch.setattr(workspace, "_launch", launch)

    result = restore(
        snapshot(apps=(("D:/bad.exe", "bad"), ("D:/good.exe", "good"))),
        workspace_config(),
    )

    assert launcher == ["D:/good.exe"]
    assert "Opened good" in result
    assert "Could not open bad" in result


def test_saved_pages_are_reopened(monkeypatch, launcher):
    """Patched on the package, not in sys.modules: `from package import name`
    reads the parent package's attribute once the package is imported, so a
    sys.modules entry would be ignored in any run where something imported it
    first."""
    from peter.integrations import desktop

    opened = []
    monkeypatch.setattr(
        desktop, "browsers",
        SimpleNamespace(open_url=lambda url, **kw: opened.append(url)),
        raising=False,
    )

    restore(snapshot(apps=(), urls=("https://example.com/x",)), workspace_config())

    assert opened == ["https://example.com/x"]


def test_restoring_an_already_open_workspace_says_so(monkeypatch, launcher):
    monkeypatch.setattr(workspace, "_running_executables", lambda: {"code.exe"})
    snapshot_with_one = snapshot(apps=(("D:/apps/code.exe", "code"),))

    result = restore(snapshot_with_one, workspace_config())

    assert "already running" in result


def test_restoring_an_empty_workspace_says_nothing_was_needed(launcher):
    assert "already open" in restore(snapshot(apps=()), workspace_config())


def test_restore_is_bounded_by_max_apps(launcher):
    many = tuple((f"D:/app{i}.exe", f"app{i}") for i in range(10))
    restore(snapshot(apps=many), workspace_config(max_apps=2))
    assert len(launcher) == 2
