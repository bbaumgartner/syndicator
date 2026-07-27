"""CLI: syndicate, redeploy, list, version."""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="syndicator",
        description="Thin local trigger: extract Logseq, upload originals over SFTP, fire n8n webhooks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="Print the syndicator version")

    p_syn = sub.add_parser(
        "syndicate",
        help="Syndicate new online posts (upload source/, /reel, /publish, mark done)",
    )
    p_syn.add_argument("--post", help="Only this post slug (default: all new online posts)")

    p_re = sub.add_parser(
        "redeploy",
        help="Force a site-only rebuild of one post (no social, no marker)",
    )
    p_re.add_argument("--post", required=True, help="Post slug to redeploy")

    sub.add_parser(
        "list",
        help="List online post slugs and syndicated-at dates (if any)",
    )

    args = parser.parse_args(argv)

    if args.command == "version":
        print(f"syndicator {__version__}")
        return 0

    from .config import load_config
    from .trigger import list_syndication, redeploy, syndicate

    cfg = load_config()

    if args.command == "list":
        rows = list_syndication(cfg)
        if rows:
            slug_width = max(len(slug) for slug, _ in rows)
            for slug, syndicated_at in rows:
                if syndicated_at:
                    print(f"  {slug:<{slug_width}}  {syndicated_at}")
                else:
                    print(f"  {slug}")
        return 0

    if args.command == "syndicate":
        report = syndicate(cfg, slug=args.post)
        for slug in report.done:
            print(f"  done      {slug}")
        for slug in report.skipped_marked:
            print(f"  skipped   {slug} (already syndicated)")
        for slug in report.skipped_no_header:
            print(f"  no-header {slug} (add a header:: image)")
        for slug, reason in report.failed:
            print(f"  FAILED    {slug}: {reason}")
        return 1 if report.failed else 0

    if args.command == "redeploy":
        redeploy(cfg, slug=args.post)
        print(f"  redeploy  {args.post} (site rebuild handed off)")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
