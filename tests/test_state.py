"""Tests for the state store and bootstrap node."""

from pathlib import Path

import pytest

from syndicator.nodes.bootstrap import bootstrap
from syndicator.nodes.extract import scan_blog_posts, source_hash
from syndicator.nodes.hugo import write_bundle
from syndicator.state import PipelineLock, StateStore

from conftest import make_cfg


def test_state_roundtrip_and_mark(tmp_path: Path):
    store = StateStore(tmp_path / "state")
    state = store.load("2026-01-01_Test")
    assert state.channel("facebook").status == "pending"

    store.save(state)
    store.mark("2026-01-01_Test", "facebook", "exported", source_hash="sha256:abc")
    reloaded = store.load("2026-01-01_Test")
    assert reloaded.channel("facebook").status == "exported"
    assert reloaded.channel("facebook").source_hash == "sha256:abc"
    assert reloaded.channel("x").status == "pending"
    # No stray temp files from atomic writes.
    assert list((tmp_path / "state").glob("*.tmp")) == []


def test_bootstrap_marks_hugo_and_renan(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}

    # Simulate the live site: render bundles for all posts except one (stale case:
    # Athen gets a bundle with modified content).
    for slug, post in posts.items():
        out = write_bundle(post, cfg.hugo_posts_dir)
        if slug == "2026-06-03_Athen":
            idx = out / "index.de.md"
            idx.write_text(idx.read_text(encoding="utf-8") + "\nmanual drift\n", encoding="utf-8")
        else:
            # Simulate existing translations.
            (out / "index.fr.md").write_text("dummy", encoding="utf-8")

    result = bootstrap(cfg)
    assert result.posts == len(posts)
    assert "2026-06-03_Athen" in result.hugo_stale
    assert "2026-05-19_Charly_Superstar" in result.hugo_in_sync

    store = StateStore(cfg.state_dir)
    renan = store.load("2024-06-14_Renan")
    assert renan.channel("hugo").status == "published"
    assert renan.channel("facebook").status == "published"
    assert renan.channel("substack").status == "published"

    charly = store.load("2026-05-19_Charly_Superstar")
    assert charly.channel("hugo").status == "published"
    assert charly.channel("hugo").source_hash == source_hash(posts["2026-05-19_Charly_Superstar"])
    assert charly.channel("facebook").status == "pending"
    assert charly.translations.get("fr") == charly.channel("hugo").source_hash
    # No translation recorded for languages without live files.
    assert "es" not in charly.translations

    athen = store.load("2026-06-03_Athen")
    assert athen.channel("hugo").status == "published"
    assert athen.channel("hugo").source_hash == ""  # stale -> regenerate on first run
    assert athen.translations == {}


def test_bootstrap_is_idempotent_and_keeps_progress(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    posts = scan_blog_posts(cfg.journals_dir, cfg.pages_dir)
    for post in posts:
        write_bundle(post, cfg.hugo_posts_dir)

    bootstrap(cfg)
    store = StateStore(cfg.state_dir)
    store.mark("2026-05-19_Charly_Superstar", "facebook", "published")
    bootstrap(cfg)
    assert store.load("2026-05-19_Charly_Superstar").channel("facebook").status == "published"


def test_pipeline_lock(tmp_path: Path):
    lock = PipelineLock(tmp_path)
    with lock:
        assert (tmp_path / "lock.json").exists()
        # Same host can re-acquire (re-entrant for our purposes).
        assert lock.acquire()
    assert not (tmp_path / "lock.json").exists()


def test_pipeline_lock_blocks_other_host(tmp_path: Path, monkeypatch):
    lock = PipelineLock(tmp_path)
    assert lock.acquire()

    import syndicator.state as state_mod

    monkeypatch.setattr(state_mod.socket, "gethostname", lambda: "other-host")
    other = PipelineLock(tmp_path)
    assert not other.acquire()
    with pytest.raises(RuntimeError):
        other.__enter__()
