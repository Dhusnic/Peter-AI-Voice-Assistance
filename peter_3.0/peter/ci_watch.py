"""Telling you a build broke, without you having to go and look.

Polls `gh run list` per configured repo and announces a run that failed. The
whole feature is the dedup: a failing run stays in the list for days, so
without remembering what has already been announced this would tell you the
same build broke every ten minutes until you fixed it — which is exactly how a
useful alert becomes one you mute.

Dedup is keyed on the run id and lives in process only. A restart can
therefore re-announce a still-failing run once, which is a fair trade against
persisting state for something whose whole value is being timely.
"""

from __future__ import annotations

import logging
import time

from peter.core.errors import PeterError

log = logging.getLogger(__name__)

# run id -> when it was announced. Pruned by age so a long-running Peter does
# not accumulate every run id it has ever seen.
_announced: dict[str, float] = {}
_PRUNE_AFTER_SECONDS = 7 * 24 * 3600

# True once the first sweep has run. The first sweep only records what is
# already failing without saying anything: starting Peter should not produce a
# burst of alerts about builds that broke last week and that you know about.
_primed = False


def _prune() -> None:
    cutoff = time.time() - _PRUNE_AFTER_SECONDS
    for run_id in [k for k, v in _announced.items() if v < cutoff]:
        del _announced[run_id]


def check_ci() -> None:
    """Scheduler job target. Must stay importable at this exact path."""
    global _primed
    from peter.core.notify import notify
    from peter.core.services import services
    from peter.integrations.dev import gh, repos

    container = services()
    cfg = container.config.integrations.dev
    watch = cfg.ci_watch
    if not (cfg.enabled and watch.enabled and cfg.repos):
        return
    if not gh.available(cfg):
        log.debug("ci watch: gh is not installed, nothing to poll")
        return

    _prune()
    first_sweep = not _primed
    _primed = True

    for repo in repos(cfg):
        try:
            runs = gh.ci_runs(cfg, repo.path, limit=watch.runs_per_repo)
        except PeterError as exc:
            log.info("ci watch: %s unreadable this poll (%s)", repo.name, exc)
            continue
        except Exception:
            log.exception("ci watch: unexpected failure on %s", repo.name)
            continue

        for run in runs:
            if not run.failed or not run.id:
                continue
            if watch.branches and run.branch not in watch.branches:
                continue
            if run.id in _announced:
                continue
            _announced[run.id] = time.time()
            if first_sweep:
                continue  # record it, but do not shout about history

            text = f"{repo.name}: {run.name} failed on {run.branch}."
            log.info("ci watch: %s", text)
            container.say(text)
            notify("Peter — CI failed", f"{text}\n{run.url}")


def schedule_ci_watch(scheduler, config) -> None:
    """Install (or re-install) the CI poll."""
    cfg = config.integrations.dev
    if not (cfg.enabled and cfg.ci_watch.enabled and cfg.repos):
        return
    scheduler.add_interval_job(
        job_id="ci-watch-poll",
        minutes=cfg.ci_watch.poll_interval_minutes,
        func=check_ci,
        name="CI watcher",
    )
    log.info(
        "ci watch: polling %d repo(s) every %d minute(s)",
        len(cfg.repos), cfg.ci_watch.poll_interval_minutes,
    )


def reset_for_tests() -> None:
    global _primed
    _announced.clear()
    _primed = False
