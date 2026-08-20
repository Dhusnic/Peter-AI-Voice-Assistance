"""GitHub, through the `gh` CLI.

Everything asks for `--json`, so nothing here parses human-readable output —
`gh`'s prose formatting changes between releases and is localised; its JSON
field names are a documented interface.

Two shapes of query, and the difference matters for what you can ask:

    `gh search prs`   works across every repo you can see, no directory needed.
                      This is what answers "what is waiting on me" globally.
    `gh run list`     needs a repository, so it runs inside a configured repo
                      directory and reports on that repo's CI only.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from peter.core.errors import IntegrationError

log = logging.getLogger(__name__)


@dataclass(slots=True)
class PullRequest:
    number: int
    title: str
    repo: str
    author: str
    url: str
    draft: bool = False
    updated: str = ""

    def spoken(self) -> str:
        draft = " (draft)" if self.draft else ""
        return f"{self.repo} #{self.number}: {self.title}{draft}"


@dataclass(slots=True)
class Run:
    name: str
    status: str          # queued | in_progress | completed
    conclusion: str      # success | failure | cancelled | ""
    branch: str
    url: str
    id: str = ""
    started: str = ""

    @property
    def failed(self) -> bool:
        return self.conclusion in ("failure", "timed_out", "startup_failure")

    def spoken(self) -> str:
        state = self.conclusion or self.status.replace("_", " ")
        return f"{self.name} on {self.branch}: {state}"


def available(cfg) -> bool:
    """Is the CLI installed? Says nothing about whether it is logged in."""
    return shutil.which(cfg.gh_path) is not None


def run_json(args: list[str], cfg, cwd: str | Path | None = None) -> Any:
    """Run one `gh` command expecting JSON on stdout."""
    if not available(cfg):
        raise IntegrationError(
            "the GitHub CLI (gh) is not installed", service="github",
            user_action="Install it from cli.github.com, then run: gh auth login",
        )

    try:
        result = subprocess.run(
            [cfg.gh_path, *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=cfg.gh_timeout_seconds,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise IntegrationError(
            f"gh timed out after {cfg.gh_timeout_seconds:g}s",
            service="github", recoverable=True,
        ) from exc
    except OSError as exc:
        raise IntegrationError(f"could not run gh: {exc}", service="github") from exc

    if result.returncode != 0:
        message = (result.stderr or "").strip()[:300]
        if "auth" in message.lower() or "logged" in message.lower():
            raise IntegrationError(
                f"gh is not authenticated: {message}", service="github",
                user_action="Run: gh auth login",
            )
        raise IntegrationError(f"gh failed: {message}", service="github")

    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise IntegrationError(
            f"gh returned something that is not JSON: {exc}", service="github"
        ) from exc


# ------------------------------------------------------------ pull requests
_PR_FIELDS = "number,title,repository,author,url,isDraft,updatedAt"


def _to_pr(raw: dict) -> PullRequest:
    repository = raw.get("repository") or {}
    author = raw.get("author") or {}
    return PullRequest(
        number=int(raw.get("number", 0)),
        title=raw.get("title", "") or "",
        # `gh search` nests the repo name; `gh pr list` does not include it at all.
        repo=repository.get("nameWithOwner") or repository.get("name") or "",
        author=author.get("login", "") or "",
        url=raw.get("url", "") or "",
        draft=bool(raw.get("isDraft")),
        updated=raw.get("updatedAt", "") or "",
    )


def review_requests(cfg, limit: int = 20) -> list[PullRequest]:
    """Open PRs where your review has been requested, across every repo."""
    raw = run_json(
        ["search", "prs", "--review-requested=@me", "--state=open",
         f"--limit={limit}", f"--json={_PR_FIELDS}"],
        cfg,
    )
    return [_to_pr(item) for item in raw or []]


def my_open_prs(cfg, limit: int = 20) -> list[PullRequest]:
    """Your own open PRs, across every repo."""
    raw = run_json(
        ["search", "prs", "--author=@me", "--state=open",
         f"--limit={limit}", f"--json={_PR_FIELDS}"],
        cfg,
    )
    return [_to_pr(item) for item in raw or []]


def assigned_issues(cfg, limit: int = 20) -> list[PullRequest]:
    """Issues assigned to you. Same shape as a PR for reporting purposes."""
    raw = run_json(
        ["search", "issues", "--assignee=@me", "--state=open",
         f"--limit={limit}", "--json=number,title,repository,author,url,updatedAt"],
        cfg,
    )
    return [_to_pr(item) for item in raw or []]


# ---------------------------------------------------------------- CI runs
def ci_runs(cfg, repo_path: str | Path, limit: int = 10, branch: str = "") -> list[Run]:
    """Recent workflow runs for the repository in `repo_path`."""
    args = [
        "run", "list", f"--limit={limit}",
        "--json=name,status,conclusion,headBranch,url,databaseId,startedAt",
    ]
    if branch:
        args.append(f"--branch={branch}")

    raw = run_json(args, cfg, cwd=repo_path)
    return [
        Run(
            name=item.get("name", "") or "workflow",
            status=item.get("status", "") or "",
            conclusion=item.get("conclusion", "") or "",
            branch=item.get("headBranch", "") or "",
            url=item.get("url", "") or "",
            id=str(item.get("databaseId", "")),
            started=item.get("startedAt", "") or "",
        )
        for item in raw or []
    ]
