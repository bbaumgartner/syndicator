"""Tests for the CLI commands (status, done, version) via typer's runner."""

from pathlib import Path

from typer.testing import CliRunner

from syndicator import __version__
from syndicator.cli import app
from syndicator.state import ReviewState, ReviewStore, SocialPostState, caption_children

from conftest import make_cfg

runner = CliRunner()


def _cfg_with_state(tmp_path: Path, monkeypatch, statuses: dict[str, str]):
    cfg = make_cfg(tmp_path)
    monkeypatch.setattr("syndicator.config.load_config", lambda repo_root=None: cfg)
    store = ReviewStore(cfg.pages_dir)
    state = ReviewState(slug="2026-05-19_Charly_Superstar")
    for channel, status in statuses.items():
        state.posts.append(
            SocialPostState(
                channel=channel,
                title="Intro",
                status=status,  # type: ignore[arg-type]
                children=caption_children("Caption", [], []),
            )
        )
    store.save(state)
    return cfg, store


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_status_without_pages_exits_nonzero(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path)
    monkeypatch.setattr("syndicator.config.load_config", lambda repo_root=None: cfg)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "bootstrap" in result.stdout


def test_status_lists_channels_and_backlog(tmp_path: Path, monkeypatch):
    _cfg_with_state(
        tmp_path, monkeypatch, {"facebook": "published", "instagram": "draft"}
    )
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    line = next(
        ln for ln in result.stdout.splitlines() if ln.startswith("2026-05-19_Charly_Superstar")
    )
    # facebook published (x), instagram draft (o), x pending (.)
    assert line.split()[1:] == ["x", "o", "."]
    assert "backlog (pending):" in result.stdout


def test_done_marks_all_draft_channels(tmp_path: Path, monkeypatch):
    cfg, store = _cfg_with_state(
        tmp_path, monkeypatch, {"facebook": "draft", "instagram": "draft"}
    )
    result = runner.invoke(app, ["done", "2026-05-19_Charly_Superstar"])
    assert result.exit_code == 0
    state = store.load("2026-05-19_Charly_Superstar")
    assert state.channel_state("facebook") == "published"
    assert state.channel_state("instagram") == "published"


def test_done_single_channel_only(tmp_path: Path, monkeypatch):
    cfg, store = _cfg_with_state(
        tmp_path, monkeypatch, {"facebook": "draft", "instagram": "draft"}
    )
    result = runner.invoke(
        app, ["done", "2026-05-19_Charly_Superstar", "--channel", "facebook"]
    )
    assert result.exit_code == 0
    state = store.load("2026-05-19_Charly_Superstar")
    assert state.channel_state("facebook") == "published"
    assert state.channel_state("instagram") == "draft"


def test_done_rejects_unknown_channel_and_missing_page(tmp_path: Path, monkeypatch):
    cfg, store = _cfg_with_state(tmp_path, monkeypatch, {"facebook": "draft"})

    result = runner.invoke(
        app, ["done", "2026-05-19_Charly_Superstar", "--channel", "myspace"]
    )
    assert result.exit_code == 1
    assert "Unknown channel" in result.stdout
    # Nothing was persisted by the failed invocation.
    assert store.load("2026-05-19_Charly_Superstar").channel_state("facebook") == "draft"

    result = runner.invoke(app, ["done", "2000-01-01_Nope"])
    assert result.exit_code == 1
    assert "No review page" in result.stdout


def test_done_without_drafts_exits_nonzero(tmp_path: Path, monkeypatch):
    _cfg_with_state(tmp_path, monkeypatch, {"facebook": "published"})
    result = runner.invoke(app, ["done", "2026-05-19_Charly_Superstar"])
    assert result.exit_code == 1
    assert "Nothing to mark" in result.stdout


def test_done_channel_without_blocks_points_to_logseq(tmp_path: Path, monkeypatch):
    _cfg_with_state(tmp_path, monkeypatch, {"facebook": "draft"})
    result = runner.invoke(
        app, ["done", "2026-05-19_Charly_Superstar", "--channel", "x"]
    )
    assert result.exit_code == 1
    assert "no blocks on review page" in result.stdout
