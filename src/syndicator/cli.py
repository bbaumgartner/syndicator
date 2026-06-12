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
def catchup(
    post: str = typer.Option(None, "--post", help="Slug to process (default: oldest pending)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="No LLM calls, no link checks."),
    force: bool = typer.Option(False, "--force", help="Re-export even already exported/published channels."),
    no_verify_links: bool = typer.Option(False, "--no-verify-links", help="Skip live URL verification."),
) -> None:
    """Generate social post packages for the oldest pending post (catch-up backlog)."""
    from .config import load_config
    from .pipeline import find_post, next_catchup_post, run_social_for_post
    from .state import StateStore

    cfg = load_config()
    if post:
        blog_post = find_post(cfg, post)
    else:
        blog_post = next_catchup_post(cfg, StateStore(cfg.state_dir))
        if blog_post is None:
            typer.echo("Catch-up backlog is empty — nothing to do.")
            raise typer.Exit(0)

    typer.echo(f"Processing {blog_post.slug} ...")
    export_dir = run_social_for_post(
        cfg, blog_post, dry_run=dry_run, force=force, verify_links=not no_verify_links
    )
    if export_dir is None:
        raise typer.Exit(0)
    typer.echo(f"\nExport: {export_dir}")
    typer.echo(f"Review: {export_dir / 'review.html'}  (syndicator review)")


@app.command()
def review(
    slug: str = typer.Argument(None, help="Post slug (default: most recent export)."),
) -> None:
    """Open the review page of an export in the browser."""
    import subprocess
    import sys

    from .config import load_config

    cfg = load_config()
    if slug:
        page = cfg.exports_dir / slug / "review.html"
        if not page.exists():
            typer.echo(f"No review page at {page}")
            raise typer.Exit(1)
    else:
        pages = sorted(cfg.exports_dir.glob("*/review.html"), key=lambda p: p.stat().st_mtime)
        if not pages:
            typer.echo("No exports yet — run `syndicator catchup` first.")
            raise typer.Exit(1)
        page = pages[-1]

    typer.echo(f"Opening {page}")
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.run([opener, str(page)], check=False)


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
