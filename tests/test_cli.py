"""Tests for the v2 CLI (version, syndicate, redeploy) via typer's runner."""

from pathlib import Path

from typer.testing import CliRunner

from syndicator import __version__
from syndicator.cli import app
from syndicator.pipeline import SyndicateReport

from conftest import make_cfg

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_syndicate_reports_outcomes(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path)
    monkeypatch.setattr("syndicator.config.load_config", lambda repo_root=None: cfg)

    report = SyndicateReport(
        done=["2026-05-19_Charly_Superstar"],
        skipped_marked=["2026-06-03_Athen"],
        skipped_no_header=["2026-05-28_Lefkada"],
    )
    seen = {}

    def fake_syndicate(config, slug=None):
        seen["slug"] = slug
        return report

    monkeypatch.setattr("syndicator.pipeline.syndicate", fake_syndicate)
    result = runner.invoke(app, ["syndicate"])
    assert result.exit_code == 0
    assert seen["slug"] is None
    assert "done      2026-05-19_Charly_Superstar" in result.stdout
    assert "already syndicated" in result.stdout
    assert "add a header" in result.stdout


def test_syndicate_post_option_passes_slug(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path)
    monkeypatch.setattr("syndicator.config.load_config", lambda repo_root=None: cfg)
    seen = {}

    def fake_syndicate(config, slug=None):
        seen["slug"] = slug
        return SyndicateReport(done=[slug])

    monkeypatch.setattr("syndicator.pipeline.syndicate", fake_syndicate)
    result = runner.invoke(app, ["syndicate", "--post", "2026-06-03_Athen"])
    assert result.exit_code == 0
    assert seen["slug"] == "2026-06-03_Athen"


def test_syndicate_failure_exits_nonzero(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path)
    monkeypatch.setattr("syndicator.config.load_config", lambda repo_root=None: cfg)
    monkeypatch.setattr(
        "syndicator.pipeline.syndicate",
        lambda config, slug=None: SyndicateReport(failed=[("2026-06-03_Athen", "boom")]),
    )
    result = runner.invoke(app, ["syndicate"])
    assert result.exit_code == 1
    assert "FAILED    2026-06-03_Athen: boom" in result.stdout


def test_redeploy_requires_post(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path)
    monkeypatch.setattr("syndicator.config.load_config", lambda repo_root=None: cfg)
    result = runner.invoke(app, ["redeploy"])
    assert result.exit_code != 0  # --post is required


def test_redeploy_invokes_pipeline(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path)
    monkeypatch.setattr("syndicator.config.load_config", lambda repo_root=None: cfg)
    seen = {}
    monkeypatch.setattr(
        "syndicator.pipeline.redeploy",
        lambda config, slug: seen.setdefault("slug", slug),
    )
    result = runner.invoke(app, ["redeploy", "--post", "2026-06-03_Athen"])
    assert result.exit_code == 0
    assert seen["slug"] == "2026-06-03_Athen"
