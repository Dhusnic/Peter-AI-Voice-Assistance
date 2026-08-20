"""Development-state tools: the repo, the pull requests, the builds.

All read-only. There is no commit, push, merge or branch tool here on purpose
— the value of "what is waiting on me" is high and the cost of getting it
wrong is nil, whereas an assistant that rewrites your working tree on a
misheard sentence is a liability with very little upside.
"""

from __future__ import annotations

from peter.agent.registry import peter_tool
from peter.core.errors import PeterError
from peter.core.services import services
from peter.integrations.dev import gh, git, repos, resolve


def _dev_config():
    return services().config.integrations.dev


def _no_repos() -> str:
    return (
        "No repositories are configured. Add them under integrations.dev.repos "
        "in config.yml, as name: path."
    )


@peter_tool(tier="read")
def list_repos() -> str:
    """List the repositories Peter knows about, and the branch each is on."""
    cfg = _dev_config()
    if not cfg.repos:
        return _no_repos()

    lines = []
    for repo in repos(cfg):
        try:
            state = git.status(repo.path, cfg.git_timeout_seconds)
            lines.append(f"{repo.name} — {state.spoken()}")
        except PeterError as exc:
            lines.append(f"{repo.name} — unreadable: {exc}")
    return "\n".join(lines)


@peter_tool(tier="read")
def git_status(repo: str = "") -> str:
    """Report a repository's branch, uncommitted changes, and sync state.

    Args:
        repo: Which configured repository. Empty means the first one.
    """
    cfg = _dev_config()
    found = resolve(cfg, repo)
    if found is None:
        return _no_repos() if not cfg.repos else f"I do not know a repo called {repo!r}."

    try:
        return f"{found.name} is {git.status(found.path, cfg.git_timeout_seconds).spoken()}."
    except PeterError as exc:
        return exc.spoken()


@peter_tool(tier="read")
def recent_commits(repo: str = "", days: int = 1, mine_only: bool = True) -> str:
    """List recent commits in a repository.

    Args:
        repo: Which configured repository. Empty means all of them.
        days: How far back to look.
        mine_only: True to show only your own commits, matched on the repo's
            configured git email.
    """
    cfg = _dev_config()
    if not cfg.repos:
        return _no_repos()

    chosen = repos(cfg) if not repo.strip() else (
        [resolve(cfg, repo)] if resolve(cfg, repo) else []
    )
    if not chosen:
        return f"I do not know a repo called {repo!r}."

    since = f"{max(1, days)} day ago" if days == 1 else f"{max(1, days)} days ago"
    lines = []
    for entry in chosen:
        author = ""
        if mine_only:
            author = cfg.git_author or git.author_email(entry.path, cfg.git_timeout_seconds)
        try:
            found = git.commits(
                entry.path, since=since, author=author, repo_name=entry.name,
                timeout=cfg.git_timeout_seconds,
            )
        except PeterError as exc:
            lines.append(f"{entry.name}: {exc}")
            continue
        lines += [commit.spoken() for commit in found]

    if not lines:
        whose = "you have" if mine_only else "there have been"
        return f"No commits in the last {days} day(s) — {whose} not committed anything."
    return "\n".join(lines)


@peter_tool(tier="read")
def my_pull_requests() -> str:
    """List pull requests waiting on you: reviews requested, and your own open PRs.

    This is the "what is on my plate" question. Covers every repository your
    GitHub account can see, not just the configured ones.
    """
    cfg = _dev_config()
    try:
        reviews = gh.review_requests(cfg)
        mine = gh.my_open_prs(cfg)
    except PeterError as exc:
        return exc.spoken()

    if not reviews and not mine:
        return "Nothing is waiting on you — no review requests and no open PRs."

    blocks = []
    if reviews:
        blocks.append(
            f"{len(reviews)} waiting for your review:\n"
            + "\n".join(f"- {pr.spoken()} (by {pr.author})" for pr in reviews)
        )
    if mine:
        blocks.append(
            f"{len(mine)} of your own open:\n"
            + "\n".join(f"- {pr.spoken()}" for pr in mine)
        )
    return "\n\n".join(blocks)


@peter_tool(tier="read")
def ci_status(repo: str = "", branch: str = "") -> str:
    """Report the latest CI runs for a repository.

    Args:
        repo: Which configured repository. Empty means the first one.
        branch: Restrict to one branch. Empty means all branches.
    """
    cfg = _dev_config()
    found = resolve(cfg, repo)
    if found is None:
        return _no_repos() if not cfg.repos else f"I do not know a repo called {repo!r}."

    try:
        runs = gh.ci_runs(cfg, found.path, limit=8, branch=branch)
    except PeterError as exc:
        return exc.spoken()

    if not runs:
        return f"No CI runs found for {found.name}."

    failures = [r for r in runs if r.failed]
    lines = [f"{found.name}:"] + [f"- {run.spoken()}" for run in runs[:6]]
    if failures:
        lines.append(f"{len(failures)} of the last {len(runs)} failed.")
    return "\n".join(lines)


@peter_tool(tier="read")
def work_log(days: int = 1) -> str:
    """Report what actually got done: commits, meetings, focus sessions, to-dos.

    Args:
        days: How far back to look. 1 is today.
    """
    from peter.worklog import build_worklog

    entry = build_worklog(days)
    if entry.empty:
        return f"Nothing recorded in the last {days} day(s)."
    return f"{entry.spoken()}\n\n{entry.as_text()}"


@peter_tool(tier="read")
def standup_notes(days: int = 1) -> str:
    """Write today's standup — yesterday, today, blockers — from real activity.

    Built from commits, calendar, focus sessions and to-dos, so it reflects
    what happened rather than what you remember happening.

    Args:
        days: How far back "yesterday" reaches. 1 is the previous day; use 3
            after a weekend.
    """
    from peter.worklog import standup

    return standup(days)
