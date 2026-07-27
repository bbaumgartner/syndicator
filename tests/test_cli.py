"""Tests for the CLI (version, syndicate, redeploy)."""

from pathlib import Path

from syndicator import __version__
from syndicator.cli import main
from syndicator.trigger import SyndicateReport

from conftest import make_cfg


def test_version(capsys):
    assert main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_syndicate_reports_outcomes(tmp_path: Path, monkeypatch, capsys):
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

    monkeypatch.setattr("syndicator.trigger.syndicate", fake_syndicate)
    assert main(["syndicate"]) == 0
    assert seen["slug"] is None
    out = capsys.readouterr().out
    assert "done      2026-05-19_Charly_Superstar" in out
    assert "already syndicated" in out
    assert "add a header" in out


def test_syndicate_post_option_passes_slug(tmp_path: Path, monkeypatch, capsys):
    cfg = make_cfg(tmp_path)
    monkeypatch.setattr("syndicator.config.load_config", lambda repo_root=None: cfg)
    seen = {}

    def fake_syndicate(config, slug=None):
        seen["slug"] = slug
        return SyndicateReport(done=[slug])

    monkeypatch.setattr("syndicator.trigger.syndicate", fake_syndicate)
    assert main(["syndicate", "--post", "2026-06-03_Athen"]) == 0
    assert seen["slug"] == "2026-06-03_Athen"


def test_syndicate_failure_exits_nonzero(tmp_path: Path, monkeypatch, capsys):
    cfg = make_cfg(tmp_path)
    monkeypatch.setattr("syndicator.config.load_config", lambda repo_root=None: cfg)
    monkeypatch.setattr(
        "syndicator.trigger.syndicate",
        lambda config, slug=None: SyndicateReport(failed=[("2026-06-03_Athen", "boom")]),
    )
    assert main(["syndicate"]) == 1
    assert "FAILED    2026-06-03_Athen: boom" in capsys.readouterr().out


def test_redeploy_requires_post(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path)
    monkeypatch.setattr("syndicator.config.load_config", lambda repo_root=None: cfg)
    try:
        main(["redeploy"])
        raised = False
    except SystemExit as exc:
        raised = True
        assert exc.code != 0
    assert raised


def test_redeploy_invokes_pipeline(tmp_path: Path, monkeypatch, capsys):
    cfg = make_cfg(tmp_path)
    monkeypatch.setattr("syndicator.config.load_config", lambda repo_root=None: cfg)
    seen = {}
    monkeypatch.setattr(
        "syndicator.trigger.redeploy",
        lambda config, slug: seen.setdefault("slug", slug),
    )
    assert main(["redeploy", "--post", "2026-06-03_Athen"]) == 0
    assert seen["slug"] == "2026-06-03_Athen"
