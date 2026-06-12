"""Syndicator command line interface."""

from __future__ import annotations

import logging

import typer

from . import __version__

app = typer.Typer(
    name="syndicator",
    help="Logseq publish pipeline: Hugo site, translations, journey map and social post packages.",
    no_args_is_help=True,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

STATUS_SYMBOLS = {"pending": ".", "exported": "o", "published": "x"}


@app.command()
def version() -> None:
    """Print the syndicator version."""
    typer.echo(f"syndicator {__version__}")


@app.command()
def bootstrap(
    social_published: list[str] = typer.Option(
        None,
        "--social-published",
        help="Slugs already published on social/article channels (default: Renan).",
    ),
) -> None:
    """Initialize per-channel state for all existing posts."""
    from .config import load_config
    from .nodes.bootstrap import bootstrap as run_bootstrap

    cfg = load_config()
    result = run_bootstrap(cfg, social_published or None)
    typer.echo(f"Bootstrapped {result.posts} posts -> {cfg.state_dir}")
    typer.echo(f"  hugo in sync: {len(result.hugo_in_sync)}")
    if result.hugo_stale:
        typer.echo(f"  hugo stale (will regenerate on first run): {', '.join(result.hugo_stale)}")
    typer.echo(f"  social already published: {', '.join(result.social_published) or '-'}")


@app.command()
def status() -> None:
    """Show per-channel status and the catch-up backlog."""
    from .config import ALL_CHANNELS, load_config
    from .state import StateStore

    cfg = load_config()
    states = StateStore(cfg.state_dir).all()
    if not states:
        typer.echo("No state yet — run `syndicator bootstrap` first.")
        raise typer.Exit(1)

    channels = ALL_CHANNELS
    header = f"{'slug':44s} " + " ".join(f"{c[:4]:>4s}" for c in channels)
    typer.echo(header)
    typer.echo("-" * len(header))
    states.sort(key=lambda s: s.date or s.slug)
    for st in states:
        row = " ".join(f"{STATUS_SYMBOLS.get(st.channel(c).status, '?'):>4s}" for c in channels)
        typer.echo(f"{st.slug:44s} {row}")

    typer.echo("\nbacklog (pending):")
    for c in channels:
        pending = [s.slug for s in states if s.channel(c).status == "pending"]
        typer.echo(f"  {c:10s} {len(pending):3d}")
    typer.echo("\nlegend: x published, o exported, . pending")


@app.command()
def done(
    slug: str = typer.Argument(..., help="Post slug, e.g. 2026-05-19_Charly_Superstar"),
    channel: list[str] = typer.Option(
        None, "--channel", "-c", help="Channels to mark (default: all currently exported)."
    ),
) -> None:
    """Mark channels of a post as published after manual posting."""
    from .config import ALL_CHANNELS, load_config
    from .state import StateStore

    cfg = load_config()
    store = StateStore(cfg.state_dir)
    state = store.load(slug)

    targets = channel or [c for c in ALL_CHANNELS if state.channel(c).status == "exported"]
    if not targets:
        typer.echo("Nothing to mark: no exported channels and none given via --channel.")
        raise typer.Exit(1)

    for c in targets:
        if c not in ALL_CHANNELS:
            typer.echo(f"Unknown channel: {c}")
            raise typer.Exit(1)
        store.mark(slug, c, "published")
        typer.echo(f"  {slug} {c} -> published")


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
