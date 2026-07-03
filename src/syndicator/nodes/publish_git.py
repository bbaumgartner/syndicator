"""publish_git node: commit and push the Hugo site repo, wait for the deploy.

Same behavior as the old watch script (git add --all, commit, push), plus a
deploy check that polls the live URL of newly published posts so social
exports only reference links that actually resolve.

The site repo *is* the artifact/State for this node: a dirty working tree or
commits ahead of upstream are pending work. That makes recovery the usual
"run it again" — a run interrupted after commit but before (or during) a
failed push leaves commits ahead of upstream, and the next run pushes them
even when no post changed in that run.
"""

from __future__ import annotations

import logging
import subprocess
import time

from ..config import Config
from ..siteurl import url_is_live

log = logging.getLogger(__name__)

COMMIT_MESSAGE = "automatic change by syndicator"


def _git(cfg: Config, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cfg.local.sailingnomads_dir), *args],
        capture_output=True, text=True,
    )


def has_changes(cfg: Config) -> bool:
    result = _git(cfg, "status", "--porcelain")
    return bool(result.stdout.strip())


def has_unpushed_commits(cfg: Config) -> bool:
    """True when HEAD is ahead of its upstream (commits waiting to be pushed).

    Handles the no-upstream case conservatively: ``git rev-list @{u}..HEAD``
    fails when no upstream is configured, so we log a warning and return False
    rather than crash.
    """
    result = _git(cfg, "rev-list", "--count", "@{u}..HEAD")
    if result.returncode != 0:
        log.warning(
            "cannot determine unpushed commits (no upstream?): %s",
            (result.stderr or result.stdout).strip(),
        )
        return False
    return int(result.stdout.strip() or "0") > 0


def commit_and_push(cfg: Config, message: str = COMMIT_MESSAGE) -> bool:
    """Returns True when a commit was pushed.

    The git repo itself is the artifact/State for this node: a dirty working
    tree or commits ahead of upstream are pending work. So this both commits
    (when the tree is dirty) and pushes (when a commit was just made *or* the
    repo already has unpushed commits from an interrupted earlier run) —
    repairing a committed-but-unpushed state left by a failed push.

    The hash-based state decides what gets re-rendered; the dirty-tree check is
    the final gate on committing: a re-render can be byte-identical to what is
    live (source edits that do not affect the rendered output), and committing
    a clean tree would fail.
    """
    committed = False
    if has_changes(cfg):
        _git(cfg, "add", "--all")
        commit = _git(cfg, "commit", "-m", message)
        if commit.returncode != 0:
            log.error("git commit failed: %s", commit.stderr or commit.stdout)
            return False
        committed = True

    if not committed and not has_unpushed_commits(cfg):
        log.info("site repo clean — nothing to commit or push")
        return False

    push = _git(cfg, "push")
    if push.returncode != 0:
        log.error("git push failed: %s", push.stderr or push.stdout)
        raise RuntimeError("git push failed — resolve manually, then re-run")
    log.info("pushed site changes")
    return True


def wait_for_deploy(cfg: Config, url: str) -> bool:
    """Poll the URL until it responds 200 or the timeout is reached."""
    timeout = cfg.shared.site.deploy_check.timeout_seconds
    poll = cfg.shared.site.deploy_check.poll_seconds
    deadline = time.monotonic() + timeout
    log.info("waiting for deploy of %s (timeout %ds)", url, timeout)
    while time.monotonic() < deadline:
        if url_is_live(url):
            log.info("deploy is live: %s", url)
            return True
        time.sleep(poll)
    log.warning("deploy check timed out for %s", url)
    return False
