"""Tests for the site pipeline: detection, bundle+translate runs, git publish,
journeymap wrapper command assembly, watch ignore rules, run_all orchestration."""

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from syndicator.nodes.extract import scan_blog_posts, source_hash
from syndicator.nodes.journeymap import generate_journey_map
from syndicator.nodes.publish_git import (
    commit_and_push,
    has_changes,
    has_unpushed_commits,
    wait_for_deploy,
)
from syndicator.watch import _Handler, is_relevant_path
from syndicator.nodes.backlink import read_hugo_hash, set_hugo_hash
from syndicator.pipeline import run_all, run_site_for_post, site_changed_posts
from syndicator.state import ReviewStore, short_hash

from conftest import FakeLLM, make_cfg


def test_site_changed_posts_detection(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    store = ReviewStore(cfg.pages_dir)
    posts = scan_blog_posts(cfg.journals_dir, cfg.pages_dir)

    changed = site_changed_posts(cfg, store)
    assert len(changed) == len(posts)  # nothing processed yet

    post = posts[0]
    set_hugo_hash(post, short_hash(source_hash(post)))
    assert len(site_changed_posts(cfg, store)) == len(posts) - 1


def test_run_site_for_post_writes_and_skips(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    store = ReviewStore(cfg.pages_dir)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2026-05-19_Charly_Superstar"]

    llm = FakeLLM()
    assert run_site_for_post(cfg, post, llm, store) is True
    bundle = cfg.hugo_posts_dir / post.slug
    assert (bundle / "index.de.md").exists()
    assert (bundle / "index.en.md").exists()
    assert (bundle / "index.arrr.md").exists()
    assert llm.calls > 0
    # The review page exists and the blog post links to it.
    assert store.exists(post.slug)
    assert "syndication:: [[syndicator/" in post.source_path.read_text(encoding="utf-8")

    # Second run: unchanged -> skipped entirely.
    llm2 = FakeLLM()
    assert run_site_for_post(cfg, post, llm2, store) is False
    assert llm2.calls == 0


def test_run_site_try_run_does_real_work_but_records_no_hugo_state(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    store = ReviewStore(cfg.pages_dir)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2026-05-19_Charly_Superstar"]

    llm = FakeLLM()
    assert run_site_for_post(cfg, post, llm, store, try_run=True) is True
    # Real bundle + translations land in the site repo working tree.
    bundle = cfg.hugo_posts_dir / post.slug
    assert (bundle / "index.de.md").exists()
    assert (bundle / "index.en.md").exists()
    assert llm.calls > 0
    # Hugo state stays unrecorded so the next real run picks the post up again...
    assert read_hugo_hash(post) == ""
    # ...and re-translates before recording hugo-hash.
    llm2 = FakeLLM()
    assert run_site_for_post(cfg, post, llm2, store) is True
    assert llm2.calls > 0
    assert read_hugo_hash(post) != ""

    # Third run: hugo-hash matches — skipped entirely.
    llm3 = FakeLLM()
    assert run_site_for_post(cfg, post, llm3, store) is False
    assert llm3.calls == 0


def _git(cwd: Path, *args: str):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


def test_commit_and_push_with_local_remote(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    site = cfg.local.sailingnomads_dir
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    _git(site, "init", "-q", "-b", "main")
    _git(site, "config", "user.email", "test@example.org")
    _git(site, "config", "user.name", "Test")
    (site / "README.md").write_text("hi", encoding="utf-8")
    _git(site, "add", "-A")
    _git(site, "commit", "-q", "-m", "init")
    _git(site, "remote", "add", "origin", str(remote))
    _git(site, "push", "-q", "-u", "origin", "main")

    assert not has_changes(cfg)
    assert commit_and_push(cfg) is False  # clean repo

    (site / "content" / "posts" / "new.md").write_text("x", encoding="utf-8")
    assert has_changes(cfg)
    assert commit_and_push(cfg) is True
    assert not has_changes(cfg)
    log_remote = subprocess.run(
        ["git", "-C", str(remote), "log", "--oneline"], capture_output=True, text=True
    ).stdout
    assert "automatic change by syndicator" in log_remote


def _init_local_remote(cfg, tmp_path: Path) -> Path:
    """Set up the site repo with a bare local remote, one pushed commit."""
    site = cfg.local.sailingnomads_dir
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    _git(site, "init", "-q", "-b", "main")
    _git(site, "config", "user.email", "test@example.org")
    _git(site, "config", "user.name", "Test")
    (site / "README.md").write_text("hi", encoding="utf-8")
    _git(site, "add", "-A")
    _git(site, "commit", "-q", "-m", "init")
    _git(site, "remote", "add", "origin", str(remote))
    _git(site, "push", "-q", "-u", "origin", "main")
    return remote


def test_has_unpushed_commits(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    site = cfg.local.sailingnomads_dir
    _init_local_remote(cfg, tmp_path)

    # Clean, fully pushed repo: nothing ahead of upstream.
    assert has_unpushed_commits(cfg) is False

    # Commit locally without pushing: HEAD is ahead of upstream.
    (site / "ahead.md").write_text("x", encoding="utf-8")
    _git(site, "add", "-A")
    _git(site, "commit", "-q", "-m", "local only")
    assert has_unpushed_commits(cfg) is True

    # Push: back in sync.
    _git(site, "push", "-q")
    assert has_unpushed_commits(cfg) is False


def test_has_unpushed_commits_no_upstream(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    site = cfg.local.sailingnomads_dir
    _git(site, "init", "-q", "-b", "main")
    _git(site, "config", "user.email", "test@example.org")
    _git(site, "config", "user.name", "Test")
    (site / "README.md").write_text("hi", encoding="utf-8")
    _git(site, "add", "-A")
    _git(site, "commit", "-q", "-m", "init")
    # No upstream configured — must return False, not crash.
    assert has_unpushed_commits(cfg) is False


def test_commit_and_push_repairs_unpushed_commit(tmp_path: Path):
    """A committed-but-unpushed state (e.g. left by an earlier failed push) is
    pushed on the next commit_and_push even though the working tree is clean."""
    cfg = make_cfg(tmp_path)
    site = cfg.local.sailingnomads_dir
    remote = _init_local_remote(cfg, tmp_path)

    # Simulate the leftover state: commit locally, do not push.
    (site / "content" / "posts").mkdir(parents=True, exist_ok=True)
    (site / "content" / "posts" / "new.md").write_text("x", encoding="utf-8")
    _git(site, "add", "-A")
    _git(site, "commit", "-q", "-m", "automatic change by syndicator")
    assert not has_changes(cfg)  # clean working tree...
    assert has_unpushed_commits(cfg)  # ...but a commit is waiting.

    assert commit_and_push(cfg) is True
    assert not has_unpushed_commits(cfg)
    log_remote = subprocess.run(
        ["git", "-C", str(remote), "log", "--oneline"], capture_output=True, text=True
    ).stdout
    assert "automatic change by syndicator" in log_remote


def test_journeymap_wrapper_with_fake_binaries(tmp_path: Path):
    cfg = make_cfg(tmp_path)

    fake_jm = tmp_path / "fake_journeymap"
    fake_jm.write_text('#!/bin/sh\necho "{\\"positions\\": []}" > "$2"\n', encoding="utf-8")
    fake_am = tmp_path / "fake_animatemap"
    fake_am.write_text('#!/bin/sh\ntouch "$2"\n', encoding="utf-8")
    for f in (fake_jm, fake_am):
        os.chmod(f, os.stat(f).st_mode | stat.S_IEXEC)

    cfg.local.journeymap_bin = str(fake_jm)
    cfg.local.animatemap_bin = str(fake_am)

    assert generate_journey_map(cfg) is True
    assert (cfg.local.sailingnomads_dir / "data" / "journey.json").exists()
    assert (cfg.local.sailingnomads_dir / "static" / "journey-map.mp4").exists()


def _patch_run_all_externals(monkeypatch):
    """Silence LLM, git, deploy polling and journey map for run_all tests."""
    calls = SimpleNamespace(commits=0, deploys=[], journeymaps=0)

    monkeypatch.setattr("syndicator.pipeline.make_llm", lambda cfg: FakeLLM())
    monkeypatch.setattr("syndicator.siteurl.url_is_live", lambda url: True)

    def fake_journeymap(cfg):
        calls.journeymaps += 1
        return True

    def fake_commit(cfg, message=None):
        calls.commits += 1
        return True

    def fake_wait(cfg, url):
        calls.deploys.append(url)
        return True

    monkeypatch.setattr("syndicator.nodes.journeymap.generate_journey_map", fake_journeymap)
    monkeypatch.setattr("syndicator.nodes.publish_git.commit_and_push", fake_commit)
    monkeypatch.setattr("syndicator.nodes.publish_git.wait_for_deploy", fake_wait)
    return calls


def test_run_all_full_cycle(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path)
    calls = _patch_run_all_externals(monkeypatch)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}

    run_all(cfg)

    # Site: every post rendered, hash recorded, one commit, deploy per new post.
    for slug, post in posts.items():
        assert (cfg.hugo_posts_dir / slug / "index.arrr.md").exists()
        assert read_hugo_hash(post) == short_hash(source_hash(post))
    assert calls.journeymaps == 1
    assert calls.commits == 1
    assert len(calls.deploys) == len(posts)

    # Social: new posts got draft blocks on their review pages.
    store = ReviewStore(cfg.pages_dir)
    state = store.load("2026-05-19_Charly_Superstar")
    assert state.channel_state("facebook") == "draft"
    assert state.channel_state("x") == "draft"

    # Lock is released.
    assert not cfg.lock_path.exists()

    # Second run: nothing changed, nothing committed.
    run_all(cfg)
    assert calls.commits == 1
    assert calls.journeymaps == 1


def test_run_all_reprocesses_edited_post_and_stale_drafts(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path)
    calls = _patch_run_all_externals(monkeypatch)
    run_all(cfg)
    assert calls.commits == 1

    store = ReviewStore(cfg.pages_dir)
    slug = "2026-05-19_Charly_Superstar"
    old_hashes = {p.source_hash for p in store.load(slug).posts_for("facebook")}

    # Edit the blog source: change a content block.
    journal = cfg.journals_dir / "2026_05_19.md"
    journal.write_text(
        journal.read_text(encoding="utf-8").replace(
            "Er wird auch oft fotografiert", "Er wird auch sehr oft fotografiert"
        ),
        encoding="utf-8",
    )

    run_all(cfg)
    assert calls.commits == 2
    # Not a new post: no second deploy wait for it beyond the first run.
    assert len(calls.deploys) == 7
    # Stale facebook drafts regenerated from the new source.
    new_hashes = {p.source_hash for p in store.load(slug).posts_for("facebook")}
    assert new_hashes != old_hashes


def test_run_all_repairs_unpushed_commit_on_next_run(tmp_path: Path, monkeypatch):
    """Run 1 renders posts then fails to push; run 2 with NO source changes
    still runs the repair push because the repo is left committed-but-unpushed.
    """
    cfg = make_cfg(tmp_path)
    calls = _patch_run_all_externals(monkeypatch)

    # Run 1: render everything, then the push fails (RuntimeError aborts the run).
    def failing_commit(cfg, message=None):
        calls.commits += 1
        raise RuntimeError("git push failed — resolve manually, then re-run")

    monkeypatch.setattr("syndicator.nodes.publish_git.commit_and_push", failing_commit)
    with pytest.raises(RuntimeError):
        run_all(cfg, site_only=True)
    assert calls.commits == 1
    assert calls.journeymaps == 1
    # Posts were rendered and their hugo-hash recorded, so run 2 sees no
    # changed posts — the in-run flag alone would never push again.
    assert site_changed_posts(cfg, ReviewStore(cfg.pages_dir)) == []
    assert not cfg.lock_path.exists()  # lock released despite the failure

    # Simulate the leftover artifact/State: clean working tree, but a commit
    # sits ahead of upstream (what a failed push leaves behind). run_all reads
    # both from .nodes.publish_git at function level, so patch there.
    monkeypatch.setattr("syndicator.nodes.publish_git.has_changes", lambda cfg: False)
    monkeypatch.setattr(
        "syndicator.nodes.publish_git.has_unpushed_commits", lambda cfg: True
    )

    recorded: list[bool] = []

    def recording_commit(cfg, message=None):
        calls.commits += 1
        recorded.append(True)
        return True

    monkeypatch.setattr("syndicator.nodes.publish_git.commit_and_push", recording_commit)

    # Run 2: no source change, yet the repair push runs (journeymap too).
    run_all(cfg, site_only=True)
    assert recorded == [True]
    assert calls.commits == 2
    assert calls.journeymaps == 2


def test_run_all_try_run_records_no_state_and_skips_push(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path)
    calls = _patch_run_all_externals(monkeypatch)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    slug = "2026-05-19_Charly_Superstar"

    run_all(cfg, slugs=[slug], try_run=True)

    assert calls.commits == 0
    assert calls.deploys == []
    assert (cfg.hugo_posts_dir / slug / "index.de.md").exists()
    assert read_hugo_hash(posts[slug]) == ""  # picked up again by the next real run
    # Social blocks exist (draft), exported without link verification.
    store = ReviewStore(cfg.pages_dir)
    assert store.load(slug).channel_state("facebook") == "draft"


def test_watch_handler_filters_and_uses_dest_path():
    seen: list[str] = []
    handler = _Handler(seen.append)

    handler.on_any_event(SimpleNamespace(is_directory=False, src_path="/g/journals/x.md", dest_path=None))
    handler.on_any_event(SimpleNamespace(is_directory=True, src_path="/g/journals", dest_path=None))
    handler.on_any_event(
        SimpleNamespace(is_directory=False, src_path="/g/journals/.syncthing.x.md.tmp", dest_path=None)
    )
    # Syncthing finalizes downloads via rename: temp source, relevant destination.
    handler.on_any_event(
        SimpleNamespace(is_directory=False, src_path="/g/journals/y.md.tmp", dest_path="/g/journals/y.md")
    )
    handler.on_any_event(
        SimpleNamespace(is_directory=False, src_path="/g/pages/syndicator___2026-01-01_T.md", dest_path=None)
    )

    assert seen == ["/g/journals/x.md", "/g/journals/y.md"]


def test_wait_for_deploy_success_and_timeout(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path)

    monkeypatch.setattr("syndicator.nodes.publish_git.url_is_live", lambda url: True)
    assert wait_for_deploy(cfg, "https://example.org/x/") is True

    cfg.shared.site.deploy_check.timeout_seconds = 0
    monkeypatch.setattr("syndicator.nodes.publish_git.url_is_live", lambda url: False)
    assert wait_for_deploy(cfg, "https://example.org/x/") is False


def test_watch_ignore_rules():
    assert is_relevant_path("/saillog/journals/2026_06_10.md")
    assert is_relevant_path("/saillog/assets/photo.jpg")
    assert is_relevant_path("/saillog/pages/Renan.md")
    # Own write targets: review pages and adapted media.
    assert not is_relevant_path("/saillog/pages/syndicator___2026-04-08_Segeln.md")
    assert not is_relevant_path("/saillog/assets/syndicator/2026-04-08_Segeln/facebook/00-intro/foto.jpg")
    assert not is_relevant_path("/saillog/.syndicator-lock.json")
    # Legacy data dir (until deleted at cutover).
    assert not is_relevant_path("/saillog/.syndicator/state/x.json")
    assert not is_relevant_path("/saillog/.stversions/journals/old.md")
    assert not is_relevant_path("/saillog/logseq/bak/journals/x.md")
    assert not is_relevant_path("/saillog/journals/.syncthing.2026_06_10.md.tmp")
    assert not is_relevant_path("/saillog/journals/2026_06_10.md.tmp")
    assert not is_relevant_path("/saillog/journals/.hidden.md")
