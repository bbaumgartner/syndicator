"""publish_git node: commit and push the Hugo site repo, wait for the deploy.

Same behavior as the old watch script (git add --all, commit, push), plus a
deploy check that polls the live URL of newly published posts so social
exports only reference links that actually resolve.
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


def commit_and_push(cfg: Config, message: str = COMMIT_MESSAGE) -> bool:
    """Returns True when a commit was pushed.

    The hash-based state decides what gets re-rendered; this git check is
    only the final gate: a re-render can be byte-identical to what is live
    (source edits that do not affect the rendered output), and committing a
    clean tree would fail.
    """
    if not has_changes(cfg):
        log.info("site repo clean — nothing to commit")
        return False

    _git(cfg, "add", "--all")
    commit = _git(cfg, "commit", "-m", message)
    if commit.returncode != 0:
        log.error("git commit failed: %s", commit.stderr or commit.stdout)
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
