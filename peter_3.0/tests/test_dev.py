"""Git, GitHub and the CI watcher.

The parsing is the risky part: `git status --porcelain=v2` and `gh --json` are
both stable interfaces, but only if read correctly. These tests pin the exact
shapes both produce, so a wrong assumption fails here rather than by silently
reporting a clean tree that is not clean.
"""

import subprocess
from types import SimpleNamespace

import pytest

from peter import ci_watch
from peter.core.errors import IntegrationError
from peter.integrations.dev import Repo, gh, git, repos, resolve

# --------------------------------------------------------------- resolving
def dev_config(**kwargs):
    base = dict(
        enabled=True, repos={"peter": "D:/peter", "work": "D:/work"},
        git_author="", git_timeout_seconds=5.0, gh_path="gh",
        gh_timeout_seconds=5.0,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_no_repo_named_gives_the_first_one():
    assert resolve(dev_config()).name == "peter"


def test_a_repo_is_found_by_exact_name():
    assert resolve(dev_config(), "work").path == "D:/work"


def test_a_repo_is_found_by_partial_name():
    """Reached by voice: "the peter repo" has to find `peter`."""
    assert resolve(dev_config(), "the peter repo").name == "peter"


def test_an_unknown_repo_resolves_to_nothing():
    assert resolve(dev_config(), "nothing like this") is None


def test_no_configured_repos_resolves_to_nothing():
    assert resolve(dev_config(repos={})) is None


def test_repos_are_listed_in_configured_order():
    assert [r.name for r in repos(dev_config())] == ["peter", "work"]


# ------------------------------------------------------------ git parsing
CLEAN = "# branch.oid abc\n# branch.head main\n# branch.upstream origin/main\n"

DIRTY = (
    "# branch.oid abc\n"
    "# branch.head feature/digest\n"
    "# branch.ab +2 -1\n"
    "1 .M N... 100644 100644 100644 aaa bbb peter/main.py\n"
    "1 M. N... 100644 100644 100644 ccc ddd peter/spend.py\n"
    "1 MM N... 100644 100644 100644 eee fff peter/brain.py\n"
    "? peter/new_file.py\n"
    "? notes.md\n"
)


def fake_git(monkeypatch, output="", returncode=0, stderr=""):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=returncode, stdout=output, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(git, "available", lambda: True)
    monkeypatch.setattr(git, "is_repo", lambda p: True)
    return calls


def test_a_clean_tree_is_reported_as_clean(monkeypatch):
    fake_git(monkeypatch, CLEAN)
    state = git.status("D:/peter")

    assert state.branch == "main"
    assert state.clean is True
    assert "on main: clean" == state.spoken()


def test_a_dirty_tree_counts_staged_modified_and_untracked(monkeypatch):
    fake_git(monkeypatch, DIRTY)
    state = git.status("D:/peter")

    assert state.branch == "feature/digest"
    assert state.staged == 2      # "M." and "MM"
    assert state.modified == 2    # ".M" and "MM"
    assert state.untracked == 2
    assert state.clean is False


def test_ahead_and_behind_are_read_from_the_branch_line(monkeypatch):
    fake_git(monkeypatch, DIRTY)
    state = git.status("D:/peter")

    assert (state.ahead, state.behind) == (2, 1)
    assert "2 ahead" in state.spoken()
    assert "1 behind" in state.spoken()


def test_commits_are_parsed_from_the_unit_separated_format(monkeypatch):
    sep = "\x1f"
    fake_git(monkeypatch, (
        f"abc123{sep}2 hours ago{sep}Dhusnic{sep}Add the spend ledger\n"
        f"def456{sep}yesterday{sep}Dhusnic{sep}Fix the digest classifier\n"
    ))

    found = git.commits("D:/peter", repo_name="peter")

    assert len(found) == 2
    assert found[0].sha == "abc123"
    assert found[0].subject == "Add the spend ledger"
    assert found[0].spoken() == "[peter] Add the spend ledger (2 hours ago)"


def test_a_commit_subject_containing_the_separator_shape_is_not_split(monkeypatch):
    """Commit subjects contain colons, pipes and dashes constantly — which is
    exactly why the format uses a unit separator instead."""
    sep = "\x1f"
    fake_git(monkeypatch, f"abc{sep}now{sep}D{sep}fix: handle a | b - c\n")

    assert git.commits("D:/peter")[0].subject == "fix: handle a | b - c"


def test_an_author_filter_is_passed_to_git(monkeypatch):
    calls = fake_git(monkeypatch, "")
    git.commits("D:/peter", author="me@example.com")

    args = calls[0][0]
    assert "--author=me@example.com" in args


def test_no_author_filter_means_no_author_flag(monkeypatch):
    calls = fake_git(monkeypatch, "")
    git.commits("D:/peter", author="")
    assert not any(a.startswith("--author") for a in calls[0][0])


def test_a_failing_git_command_becomes_an_integration_error(monkeypatch):
    fake_git(monkeypatch, returncode=128, stderr="fatal: not a git repository")

    with pytest.raises(IntegrationError, match="not a git repository"):
        git.status("D:/peter")


def test_a_git_timeout_is_recoverable(monkeypatch):
    monkeypatch.setattr(git, "available", lambda: True)
    monkeypatch.setattr(git, "is_repo", lambda p: True)

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=5)

    monkeypatch.setattr(subprocess, "run", timeout)

    with pytest.raises(IntegrationError) as caught:
        git.status("D:/peter")
    assert caught.value.recoverable is True


def test_git_missing_entirely_says_what_to_install(monkeypatch):
    monkeypatch.setattr(git, "available", lambda: False)

    with pytest.raises(IntegrationError) as caught:
        git.status("D:/peter")
    assert "Install Git" in caught.value.user_action


def test_a_directory_that_is_not_a_repo_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(git, "available", lambda: True)

    with pytest.raises(IntegrationError, match="not a git repository"):
        git.status(tmp_path)


def test_author_email_degrades_to_empty_when_unset(monkeypatch):
    fake_git(monkeypatch, returncode=1)
    assert git.author_email("D:/peter") == ""


# -------------------------------------------------------------- gh parsing
def fake_gh(monkeypatch, payload, returncode=0, stderr=""):
    import json

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            returncode=returncode, stdout=json.dumps(payload), stderr=stderr
        )

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(gh, "available", lambda cfg: True)
    return calls


def test_search_results_carry_the_repository_name(monkeypatch):
    fake_gh(monkeypatch, [{
        "number": 42, "title": "Add the digest", "url": "https://x/42",
        "repository": {"nameWithOwner": "dhusnic/peter"},
        "author": {"login": "someone"}, "isDraft": False,
    }])

    found = gh.review_requests(dev_config())

    assert found[0].repo == "dhusnic/peter"
    assert found[0].spoken() == "dhusnic/peter #42: Add the digest"


def test_a_draft_pull_request_is_marked_as_one(monkeypatch):
    fake_gh(monkeypatch, [{
        "number": 7, "title": "WIP", "repository": {"nameWithOwner": "x/y"},
        "author": {"login": "me"}, "isDraft": True, "url": "",
    }])
    assert "(draft)" in gh.my_open_prs(dev_config())[0].spoken()


def test_a_missing_field_does_not_break_parsing(monkeypatch):
    """`gh` omits fields rather than nulling them, which is easy to trip on."""
    fake_gh(monkeypatch, [{"number": 1}])

    found = gh.my_open_prs(dev_config())

    assert found[0].title == ""
    assert found[0].repo == ""


def test_ci_runs_are_mapped_with_their_conclusion(monkeypatch):
    fake_gh(monkeypatch, [
        {"name": "tests", "status": "completed", "conclusion": "failure",
         "headBranch": "main", "url": "https://x/1", "databaseId": 111},
        {"name": "tests", "status": "in_progress", "conclusion": None,
         "headBranch": "main", "url": "https://x/2", "databaseId": 222},
    ])

    runs = gh.ci_runs(dev_config(), "D:/peter")

    assert runs[0].failed is True
    assert runs[0].spoken() == "tests on main: failure"
    assert runs[1].failed is False
    assert runs[1].spoken() == "tests on main: in progress"


def test_a_branch_filter_is_passed_to_gh(monkeypatch):
    calls = fake_gh(monkeypatch, [])
    gh.ci_runs(dev_config(), "D:/peter", branch="main")
    assert "--branch=main" in calls[0]


def test_gh_not_being_logged_in_says_what_to_run(monkeypatch):
    fake_gh(monkeypatch, [], returncode=1, stderr="gh: not logged into any hosts")

    with pytest.raises(IntegrationError) as caught:
        gh.my_open_prs(dev_config())
    assert "gh auth login" in caught.value.user_action


def test_gh_missing_says_where_to_get_it(monkeypatch):
    monkeypatch.setattr(gh, "available", lambda cfg: False)

    with pytest.raises(IntegrationError) as caught:
        gh.my_open_prs(dev_config())
    assert "cli.github.com" in caught.value.user_action


def test_gh_returning_non_json_is_an_integration_error(monkeypatch):
    monkeypatch.setattr(gh, "available", lambda cfg: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="not json at all", stderr=""
    ))

    with pytest.raises(IntegrationError, match="not JSON"):
        gh.my_open_prs(dev_config())


# ------------------------------------------------------------- the ci watch
@pytest.fixture(autouse=True)
def _reset_ci():
    ci_watch.reset_for_tests()
    yield
    ci_watch.reset_for_tests()


@pytest.fixture
def watching(container, monkeypatch):
    said = []
    container.speaker = SimpleNamespace(say=lambda t: said.append(t))
    container.config.integrations.dev.repos = {"peter": "D:/peter"}
    monkeypatch.setattr(gh, "available", lambda cfg: True)
    return SimpleNamespace(said=said, container=container)


def runs(*specs):
    return [
        gh.Run(name=n, status="completed", conclusion=c, branch=b, url="u", id=str(i))
        for i, (n, c, b) in enumerate(specs, start=1)
    ]


def test_the_first_sweep_records_history_without_announcing_it(watching, monkeypatch):
    """Starting Peter must not produce a burst of alerts about last week."""
    monkeypatch.setattr(gh, "ci_runs", lambda *a, **k: runs(("tests", "failure", "main")))

    ci_watch.check_ci()

    assert watching.said == []


def test_a_new_failure_after_priming_is_announced(watching, monkeypatch):
    monkeypatch.setattr(gh, "ci_runs", lambda *a, **k: [])
    ci_watch.check_ci()  # priming sweep, nothing failing

    monkeypatch.setattr(gh, "ci_runs", lambda *a, **k: runs(("tests", "failure", "main")))
    ci_watch.check_ci()

    assert len(watching.said) == 1
    assert "tests failed on main" in watching.said[0]


def test_the_same_failure_is_not_announced_twice(watching, monkeypatch):
    monkeypatch.setattr(gh, "ci_runs", lambda *a, **k: [])
    ci_watch.check_ci()
    monkeypatch.setattr(gh, "ci_runs", lambda *a, **k: runs(("tests", "failure", "main")))

    ci_watch.check_ci()
    ci_watch.check_ci()
    ci_watch.check_ci()

    assert len(watching.said) == 1


def test_a_successful_run_is_never_announced(watching, monkeypatch):
    monkeypatch.setattr(gh, "ci_runs", lambda *a, **k: [])
    ci_watch.check_ci()
    monkeypatch.setattr(gh, "ci_runs", lambda *a, **k: runs(("tests", "success", "main")))

    ci_watch.check_ci()

    assert watching.said == []


def test_a_branch_filter_excludes_other_branches(watching, monkeypatch):
    watching.container.config.integrations.dev.ci_watch.branches = ["main"]
    monkeypatch.setattr(gh, "ci_runs", lambda *a, **k: [])
    ci_watch.check_ci()
    monkeypatch.setattr(
        gh, "ci_runs", lambda *a, **k: runs(("tests", "failure", "some-branch"))
    )

    ci_watch.check_ci()

    assert watching.said == []


def test_an_unreachable_github_is_swallowed_not_raised(watching, monkeypatch):
    def boom(*a, **k):
        raise IntegrationError("offline", service="github", recoverable=True)

    monkeypatch.setattr(gh, "ci_runs", boom)

    ci_watch.check_ci()  # must not raise

    assert watching.said == []


def test_no_gh_installed_means_no_polling(watching, monkeypatch):
    monkeypatch.setattr(gh, "available", lambda cfg: False)
    monkeypatch.setattr(gh, "ci_runs", lambda *a, **k: pytest.fail("should not poll"))

    ci_watch.check_ci()


def test_a_disabled_watcher_does_nothing(watching, monkeypatch):
    watching.container.config.integrations.dev.ci_watch.enabled = False
    monkeypatch.setattr(gh, "ci_runs", lambda *a, **k: pytest.fail("should not poll"))

    ci_watch.check_ci()


def test_no_repos_configured_means_no_polling(watching, monkeypatch):
    watching.container.config.integrations.dev.repos = {}
    monkeypatch.setattr(gh, "ci_runs", lambda *a, **k: pytest.fail("should not poll"))

    ci_watch.check_ci()


def test_the_ci_watch_uses_a_stable_job_id(config):
    calls = []
    scheduler = SimpleNamespace(add_interval_job=lambda **kw: calls.append(kw))
    config.integrations.dev.repos = {"peter": "D:/peter"}

    ci_watch.schedule_ci_watch(scheduler, config)
    ci_watch.schedule_ci_watch(scheduler, config)

    assert calls[0]["job_id"] == calls[1]["job_id"] == "ci-watch-poll"


def test_nothing_is_scheduled_without_repos(config):
    calls = []
    scheduler = SimpleNamespace(add_interval_job=lambda **kw: calls.append(kw))
    config.integrations.dev.repos = {}

    ci_watch.schedule_ci_watch(scheduler, config)

    assert calls == []


def test_repo_is_a_plain_pair():
    assert Repo(name="peter", path="D:/peter").path == "D:/peter"
