"""Syndicator command line interface."""

from __future__ import annotations

import typer

from . import __version__

app = typer.Typer(
    name="syndicator",
    help="Logseq publish pipeline: Hugo site, translations, journey map and social post packages.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the syndicator version."""
    typer.echo(f"syndicator {__version__}")


@app.command()
def check() -> None:
    """Validate configuration and required tools."""
    import shutil

    from .config import load_config

    cfg = load_config()
    problems: list[str] = []

    for label, path in [
        ("saillog_dir", cfg.local.saillog_dir),
        ("journals", cfg.journals_dir),
        ("pages", cfg.pages_dir),
        ("sailingnomads_dir", cfg.local.sailingnomads_dir),
        ("hugo posts dir", cfg.hugo_posts_dir),
    ]:
        status = "ok" if path.exists() else "MISSING"
        if not path.exists():
            problems.append(label)
        typer.echo(f"  {label:20s} {status:8s} {path}")

    for tool in ["ffmpeg", "git"]:
        found = shutil.which(tool)
        if not found:
            problems.append(tool)
        typer.echo(f"  {tool:20s} {'ok' if found else 'MISSING'}")

    import os

    typer.echo(f"  {'OPENAI_API_KEY':20s} {'ok' if os.environ.get('OPENAI_API_KEY') else 'MISSING'}")

    if problems:
        typer.echo(f"\nProblems: {', '.join(problems)}")
        raise typer.Exit(1)
    typer.echo("\nAll good.")


if __name__ == "__main__":
    app()
