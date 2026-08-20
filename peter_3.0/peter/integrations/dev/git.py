"""Reading a git repository by running git.

Read-only, entirely. There is no commit, push, checkout or reset here, and
that is a design decision rather than an oversight: an assistant that can
rewrite your working tree on a misheard sentence is a liability, and the
upside — saving you from typing `git commit` — is not worth it. Everything
here answers questions.

Output is parsed from porcelain/`-z` formats where they exist, because the
human-readable ones change between git versions and localise.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from peter.core.errors import IntegrationError

log = logging.getLogger(__name__)

# Field separator for `git log --format`. A unit separator cannot appear in a
# commit subject, unlike any punctuation you might reach for first.
_SEP = "\x1f"


@dataclass(slots=True)
class Commit:
    sha: str
    when: str
    subject: str
    author: str
    repo: str = ""

    def spoken(self) -> str:
        where = f"[{self.repo}] " if self.repo else ""
        return f"{where}{self.subject} ({self.when})"


@dataclass(slots=True)
class Status:
    branch: str
    modified: int
    untracked: int
    staged: int
    ahead: int
    behind: int

    @property
    def clean(self) -> bool:
        return not (self.modified or self.untracked or self.staged)

    def spoken(self) -> str:
        if self.clean:
            state = "clean"
        else:
            bits = []
            if self.staged:
                bits.append(f"{self.staged} staged")
            if self.modified:
                bits.append(f"{self.modified} modified")
            if self.untracked:
                bits.append(f"{self.untracked} untracked")
            state = ", ".join(bits)
        sync = ""
        if self.ahead:
            sync += f", {self.ahead} ahead"
        if self.behind:
            sync += f", {self.behind} behind"
        return f"on {self.branch}: {state}{sync}"


def available() -> bool:
    return shutil.which("git") is not None


def is_repo(path: str | Path) -> bool:
    directory = Path(path)
    if not directory.is_dir():
        return False
    # Works for worktrees and submodules too, where .git is a file.
    return (directory / ".git").exists()


def run(args: list[str], path: str | Path, timeout: float = 20.0) -> str:
    """Run one git command in `path` and return stdout."""
    if not available():
        raise IntegrationError(
            "git is not installed, or not on PATH", service="git",
            user_action="Install Git for Windows and reopen the terminal.",
        )
    directory = Path(path)
    if not is_repo(directory):
        raise IntegrationError(f"{directory} is not a git repository", service="git")

    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(directory),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise IntegrationError(
            f"git {args[0]} timed out after {timeout:g}s", service="git",
            recoverable=True,
        ) from exc
    except OSError as exc:
        raise IntegrationError(f"could not run git: {exc}", service="git") from exc

    if result.returncode != 0:
        raise IntegrationError(
            f"git {args[0]} failed: {(result.stderr or '').strip()[:200]}",
            service="git",
        )
    return result.stdout


def current_branch(path: str | Path, timeout: float = 20.0) -> str:
    return run(["rev-parse", "--abbrev-ref", "HEAD"], path, timeout).strip()


def author_email(path: str | Path, timeout: float = 20.0) -> str:
    """The repo's configured user.email — the default 'me' for commit filters."""
    try:
        return run(["config", "user.email"], path, timeout).strip()
    except IntegrationError:
        return ""


def status(path: str | Path, timeout: float = 20.0) -> Status:
    """Working-tree state, from `status --porcelain=v2 --branch`."""
    raw = run(["status", "--porcelain=v2", "--branch"], path, timeout)

    branch, ahead, behind = "HEAD", 0, 0
    modified = untracked = staged = 0
    for line in raw.splitlines():
        if line.startswith("# branch.head "):
            branch = line.split(" ", 2)[2].strip()
        elif line.startswith("# branch.ab "):
            # "# branch.ab +2 -1"
            parts = line.split()
            ahead = abs(int(parts[2]))
            behind = abs(int(parts[3]))
        elif line.startswith("?"):
            untracked += 1
        elif line.startswith(("1 ", "2 ")):
            # XY is the two-character staged/unstaged status pair.
            xy = line.split(" ", 2)[1]
            if xy[0] != ".":
                staged += 1
            if xy[1] != ".":
                modified += 1

    return Status(
        branch=branch, modified=modified, untracked=untracked,
        staged=staged, ahead=ahead, behind=behind,
    )


def commits(
    path: str | Path,
    since: str = "1 day ago",
    author: str = "",
    limit: int = 50,
    repo_name: str = "",
    timeout: float = 20.0,
) -> list[Commit]:
    """Commits in a time window, optionally only one author's.

    Args:
        since: Anything `git log --since` accepts: "1 day ago", "yesterday",
            "2026-08-01".
        author: Substring match on the author. Empty means everyone.
    """
    args = [
        "log",
        f"--since={since}",
        f"--max-count={limit}",
        f"--format=%h{_SEP}%ar{_SEP}%an{_SEP}%s",
        "--no-merges",
    ]
    if author:
        args.insert(1, f"--author={author}")

    found = []
    for line in run(args, path, timeout).splitlines():
        parts = line.split(_SEP)
        if len(parts) != 4:
            continue
        sha, when, who, subject = parts
        found.append(
            Commit(sha=sha, when=when, author=who, subject=subject, repo=repo_name)
        )
    return found


def branches_with_recent_work(
    path: str | Path, days: int = 14, timeout: float = 20.0
) -> list[str]:
    """Local branches committed to recently, most recent first."""
    raw = run(
        ["for-each-ref", "--sort=-committerdate", "--format=%(refname:short)%09%(committerdate:relative)",
         "refs/heads/"],
        path, timeout,
    )
    recent = []
    for line in raw.splitlines():
        name, _, when = line.partition("\t")
        if not name:
            continue
        # `for-each-ref` has no --since, and relative dates are the cheapest
        # filter that does not need a second call per branch.
        if any(unit in when for unit in ("second", "minute", "hour", "day", "week")):
            if "week" in when and days < 14:
                continue
            recent.append(name)
    return recent
