"""Tests for the site pipeline: detection, bundle+translate runs, git publish,
journeymap wrapper command assembly, watch ignore rules."""

import os
import stat
import subprocess
from pathlib import Path

from syndicator.nodes.extract import scan_blog_posts, source_hash
from syndicator.nodes.journeymap import generate_journey_map
from syndicator.nodes.publish_git import commit_and_push, has_changes
from syndicator.nodes.watch import is_relevant_path
from syndicator.pipeline import run_site_for_post, site_changed_posts
from syndicator.state import ReviewStore, short_hash

from conftest import FakeLLM, make_cfg


def test_site_changed_posts_detection(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    store = ReviewStore(cfg.pages_dir)
    posts = scan_blog_posts(cfg.journals_dir, cfg.pages_dir)

    changed = site_changed_posts(cfg, store)
    assert len(changed) == len(posts)  # nothing processed yet

    post = posts[0]
    state = store.load(post.slug)
    state.hugo_hash = short_hash(source_hash(post))
    store.save(state)
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
    assert store.load(post.slug).hugo_hash == ""
    # ...and re-translates before recording hugo-hash.
    llm2 = FakeLLM()
    assert run_site_for_post(cfg, post, llm2, store) is True
    assert llm2.calls > 0
    assert store.load(post.slug).hugo_hash != ""

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
