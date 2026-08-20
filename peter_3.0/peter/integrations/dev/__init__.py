"""Development-state integrations: git on disk, GitHub through the `gh` CLI.

Shelling out to the two tools you already have installed, rather than talking
to the GitHub API directly. That is a deliberate trade:

    no token to store    `gh auth login` already holds your credentials in the
                         OS keychain, so Peter never sees or stores one
    no API client        one subprocess call and a `--json` flag
    works on anything    private repos, enterprise hosts, SSH remotes — if the
                         CLI can see it, so can Peter

The cost is that `gh` has to be installed and logged in, which is checked for
and reported honestly rather than failing at the moment you ask a question.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Repo:
    """A configured repository: the name you say, and where it lives."""

    name: str
    path: str


def repos(cfg) -> list[Repo]:
    return [Repo(name=n, path=p) for n, p in cfg.repos.items()]


def resolve(cfg, which: str = "") -> Repo | None:
    """Find a configured repo by name, or the default when none is named.

    Matching is forgiving on purpose — this is reached by voice, and "the
    peter repo" should find `peter`.
    """
    configured = repos(cfg)
    if not configured:
        return None

    needle = which.strip().lower()
    if not needle:
        return configured[0]

    for repo in configured:
        if repo.name.lower() == needle:
            return repo
    for repo in configured:
        if needle in repo.name.lower() or repo.name.lower() in needle:
            return repo
    return None
