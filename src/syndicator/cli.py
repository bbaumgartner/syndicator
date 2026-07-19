"""Syndicator command line interface (v2).

Only two commands remain after the n8n migration: ``syndicate`` and
``redeploy`` (§5). The daemon and all review/state commands are gone.
"""

from __future__ import annotations

import logging

import typer

from . import __version__

app = typer.Typer(
    name="syndicator",
    help="Thin local trigger: extract the Logseq diary, adapt media, upload over "
    "SFTP and fire the n8n publish/reel webhooks.",
    no_args_is_help=True,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@app.command()
def version() -> None:
    """Print the syndicator version."""
    typer.echo(f"syndicator {__version__}")


@app.command()
def syndicate(
    post: str = typer.Option(None, "--post", help="Only this post slug (default: all new online posts)."),
) -> None:
    """Syndicate new online posts: adapt media, upload, fire /reel + /publish, mark done.

    With no ``--post``: every ``status:: online`` blog post without a
    ``syndicated-at::`` marker. Already-marked posts are skipped (re-running would
    create duplicate drafts). A post without a ``header::`` image is refused
    (reported and skipped in batch, others continue).
    """
    from .config import load_config
    from .pipeline import syndicate as run_syndicate

    cfg = load_config()
    report = run_syndicate(cfg, slug=post)

    for slug in report.done:
        typer.echo(f"  done      {slug}")
    for slug in report.skipped_marked:
        typer.echo(f"  skipped   {slug} (already syndicated)")
    for slug in report.skipped_no_header:
        typer.echo(f"  no-header {slug} (add a header:: image)")
    for slug, reason in report.failed:
        typer.echo(f"  FAILED    {slug}: {reason}")
    if report.failed:
        raise typer.Exit(1)


@app.command()
def redeploy(
    post: str = typer.Option(..., "--post", help="Post slug to redeploy (site only)."),
) -> None:
    """Force a site-only redeploy of one post (re-render + re-translate + commit).

    No social drafts, no marker changes. Use this to recover from a site async
    failure or to push a site-only content edit.
    """
    from .config import load_config
    from .pipeline import redeploy as run_redeploy

    cfg = load_config()
    run_redeploy(cfg, slug=post)
    typer.echo(f"  redeploy  {post} (site rebuild handed off)")


if __name__ == "__main__":
    app()
