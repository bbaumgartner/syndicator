# Syndicator

Publish pipeline for [sailingnomads.ch](https://www.sailingnomads.ch):
converts blog posts written in a [Logseq](https://logseq.com) diary into

- **Hugo page bundles**, LLM-translated into en/de/es/fr/it + pirate speak,
- the animated **journey map** on the homepage,
- **social media posts** (Facebook, Instagram, X): one per blog *section*,
  with platform-tailored AI captions and platform-adapted media.

Social posts are reviewed **inside Logseq**: the pipeline generates one review
page per blog post listing every social post with caption and media. Post
manually, then flip the block property `status:: draft` to `published` (or run
`syndicator done`). API posting and an agent mode are later phases.

The architecture — pipelines composed of nodes, an orchestrator, CLI and
daemon drivers, state, Logseq as a replaceable edge — is described in
[docs/architecture.md](docs/architecture.md). Read it before extending the
code; this README covers operating the system.

## Setup

One-time, per machine:

```bash
git clone <syndicator-repo> ~/git/syndicator && cd ~/git/syndicator
curl -LsSf https://astral.sh/uv/install.sh | sh     # if uv is missing
uv sync
cp config.local.yaml.example config.local.yaml      # adjust paths!
export OPENAI_API_KEY=sk-...
uv run syndicator check
```

Requirements: `ffmpeg`, `git` (push rights for the sailingnomads clone: SSH
deploy key or credential helper that works non-interactively), `go` (builds
the journeymap binaries once into `bin/`), and the Syncthing-synced saillog
folder.

On the Ubuntu server, which runs the watch daemon, additionally:

```bash
echo 'OPENAI_API_KEY=sk-...' > ~/.config/syndicator.env && chmod 600 ~/.config/syndicator.env
sudo cp deploy/syndicator-watch.service /etc/systemd/system/   # adjust User/paths first
sudo systemctl daemon-reload
sudo systemctl enable --now syndicator-watch.service
```

## Commands

```bash
uv run syndicator check                  # validate config, paths and tools
uv run syndicator status                 # backlog per channel
uv run syndicator bootstrap              # create review pages for existing posts
uv run syndicator run [--post SLUG]      # full pipeline for new/changed posts
uv run syndicator catchup [--post SLUG]  # social posts for the oldest pending post
uv run syndicator done SLUG [--channel]  # mark as published (same as editing the page)
uv run syndicator review [SLUG]          # open the review page in Logseq
uv run syndicator watch                  # daemon mode (normally on the server)
uv run syndicator parity                 # fresh render vs live repo
```

Useful variants:

```bash
uv run syndicator run --post <slug> --force      # re-run one post end to end (re-translates)
uv run syndicator run --site-only                # website only, no social
uv run syndicator run --try-run                  # everything except commit/push
uv run syndicator catchup --force --post <slug>  # redo drafts (published stays untouched)
```

## Daily workflows

### New blog post

Write in Logseq as usual, set `status:: online`. The server daemon picks it
up (15 min debounce), publishes the site, waits for the Netlify deploy, then
generates the social post blocks. Review on the Mac in Logseq:

1. Open the blog post and follow the `syndication::` link (or run
   `uv run syndicator review`).
2. Per social post block: copy the caption from the code fence (hover →
   copy), post manually with the embedded media files
   (`assets/syndicator/<slug>/...`), ideally around the `publishing-date::`.
   For Facebook and Instagram, also tag the location shown in `location::`
   on the block (if present).
3. Flip `status:: draft` to `published` on the block — that's it. The
   pipeline reads it on its next run; fully published channels become
   immutable.

### Catch-up (old posts, over the next weeks/months)

```bash
uv run syndicator status            # backlog per channel
uv run syndicator catchup           # oldest pending post → review page
uv run syndicator review <slug>     # open the page in Logseq
# post manually over the suggested dates, flip status:: per block
```

## Troubleshooting

- **"pipeline lock held by another machine"**: a run is active on the other
  machine, or it crashed. The lock expires after 1 h; to clear immediately,
  delete `<saillog>/.syndicator-lock.json`.
- **Syncthing conflict files** (`*.sync-conflict-*`) on review pages: rare,
  but possible when a status edit on the Mac races a pipeline rewrite on the
  server. Keep the newer file, delete the conflict copy; worst case re-run
  `syndicator bootstrap` and re-mark the published blocks.
- **Edited a caption on the review page?** Fine — it survives pipeline runs
  as long as the blog source does not change. If the source changes, draft
  blocks are regenerated (your edits are replaced); published blocks are
  never touched.
- **Manual notes on a review page** outside the generated blocks can be lost
  on the next rewrite — treat the page as generated output (status edits and
  caption tweaks inside the blocks are preserved).
- **Caption quality/model**: per-channel `caption_model` in
  `syndicator.yaml`; prompts live in `prompts/caption_*.md`.
- **Watcher loops or never triggers**: check `journalctl -u syndicator-watch`.
  It ignores its own write targets (`syndicator___*` pages,
  `assets/syndicator/`), `.stversions/`, `logseq/bak/` and Syncthing temp
  files by design.
- **git push fails from systemd**: the service user needs non-interactive
  auth for the sailingnomads remote (SSH key without passphrase or
  credential helper).
