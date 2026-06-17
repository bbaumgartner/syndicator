# Syndicator Runbook

Operations guide: two-machine setup, cutover to Logseq review pages,
daily workflows and troubleshooting.

## How everything fits together

```
Logseq (saillog, synced via Syncthing between Mac and Ubuntu server)
   │
   │  syndicator watch (Ubuntu server, systemd) — or manual `syndicator run`
   ▼
detect changed/new posts (state on review pages: saillog/pages/syndicator___<slug>.md)
   ├─ site:   hugo bundles → translations (cached) → journey map → git push → Netlify
   └─ social: sections → captions (LLM) → media crops/reels → assets/syndicator/<slug>/
              → review page syndicator/<slug> in Logseq  (synced to the Mac)
```

- **State and review** live on one generated Logseq page per blog post
  (`syndicator/<slug>`): one block per social post carries caption, media and
  `status::`; the blog post links to its review page via a `syndication::`
  property and carries ``hugo-hash::`` for site pipeline state. Adapted media
  live in `assets/syndicator/<slug>/` — everything is part of the graph and
  mirrored by Syncthing.
- **One watcher**: the daemon runs on the server. It ignores its own write
  targets (`syndicator___*` pages, `assets/syndicator/`), so pipeline writes
  never re-trigger it. The Mac is for reviewing in Logseq and for manual
  commands (`catchup`, `run`). A lock file (`<saillog>/.syndicator-lock.json`)
  prevents simultaneous pipeline runs.
- **Status lifecycle**: a channel is `pending` (no blocks yet) → `draft`
  (blocks generated; regenerated automatically if the post source changes) →
  `published` (every block published; immutable from then on). Individual
  blocks you set to `published` are frozen too: they are never regenerated,
  their media directories are never touched.

## One-time setup on a machine

```bash
git clone <syndicator-repo> ~/git/syndicator && cd ~/git/syndicator
curl -LsSf https://astral.sh/uv/install.sh | sh     # if uv is missing
uv sync
cp config.local.yaml.example config.local.yaml      # adjust paths!
uv run syndicator check
```

Requirements: `ffmpeg`, `git` (push rights for the sailingnomads clone:
SSH deploy key or credential helper that works non-interactively), `go`
(builds the journeymap binaries once into `bin/`), the Syncthing-synced
saillog folder, and `OPENAI_API_KEY` in the environment.

On the Ubuntu server additionally:

```bash
echo 'OPENAI_API_KEY=sk-...' > ~/.config/syndicator.env && chmod 600 ~/.config/syndicator.env
sudo cp deploy/syndicator-watch.service /etc/systemd/system/   # adjust User/paths first
sudo systemctl daemon-reload
```

## Cutover from `.syndicator/` to Logseq review pages

State used to live in `<saillog>/.syndicator/` (state JSONs, export packages,
`review.html`). It now lives on review pages inside the graph. Steps to switch:

1. **Stop the watcher** on the server and deploy the new code on both machines:

   ```bash
   sudo systemctl stop syndicator-watch.service
   git -C ~/git/syndicator pull && cd ~/git/syndicator && uv sync
   ```

2. **Bootstrap the review pages** (either machine; reconstructs hugo and
   translation state from the live site repo, no LLM cost):

   ```bash
   uv run syndicator bootstrap
   uv run syndicator status
   ```

   This creates one `syndicator/<slug>` page per post and adds the
   `syndication::` property to every blog post.

3. **Re-export posts that had drafts** in the old system (the old draft state
   is not migrated; check the old `.syndicator/state/` JSONs for
   `"status": "draft"` if unsure):

   ```bash
   uv run syndicator catchup --post 2025-09-13_SKS
   ```

4. **Verify in Logseq**: open a blog post, follow the `syndication::` link,
   check captions/media on the review page.

5. **Delete the legacy data dir** and restart the watcher:

   ```bash
   rm -rf <saillog>/.syndicator
   sudo systemctl start syndicator-watch.service
   ```

## Daily workflows

### New blog post

Write in Logseq as usual, set `status:: online`. The server daemon picks it
up (default: 15 min debounce), publishes the site, waits for the Netlify
deploy, then generates the social post blocks. Review on the Mac in Logseq:

1. Open the blog post and follow the `syndication::` link (or run
   `uv run syndicator review`).
2. Per social post block: copy the caption from the code fence (hover →
   copy), post manually with the embedded media files
   (`assets/syndicator/<slug>/...`), ideally around the `publishing-date::`.
3. Flip `status:: draft` to `published` on the block — that's it. The
   pipeline reads it on its next run; fully published channels become
   immutable.

`uv run syndicator done <slug>` still exists as a shortcut to mark all draft
social blocks at once.

### Catch-up (old posts, over the next weeks/months)

```bash
uv run syndicator status            # backlog per channel
uv run syndicator catchup           # oldest pending post → review page
uv run syndicator catchup --post 2026-05-19_Charly_Superstar   # or pick one
uv run syndicator review <slug>     # open the page in Logseq
# post manually over the suggested dates, flip status:: per block
```

### Useful commands

```bash
uv run syndicator run --post <slug> --force    # re-run one post end to end
uv run syndicator run --site-only              # website only, no social
uv run syndicator catchup --force --post <slug>  # redo drafts (published blocks stay untouched)
uv run syndicator parity                       # fresh render vs live repo
```

## Troubleshooting

- **"pipeline lock held by another machine"**: a run is active on the other
machine, or it crashed. The lock expires after 1 h; to clear immediately,
delete `<saillog>/.syndicator-lock.json`.
- **Syncthing conflict files** (`*.sync-conflict-*`) on review pages: rare
(one page per post, atomic writes), but possible when a status edit on the
Mac races a pipeline rewrite on the server. Keep the newer file, delete the
conflict copy; worst case re-run `syndicator bootstrap` and re-mark the
published blocks.
- **Edited a caption on the review page?** Fine — it survives pipeline runs
as long as the blog source does not change. If the source changes, draft
blocks are regenerated (your edits are replaced); published blocks are
never touched.
- **Manual notes on a review page** outside the generated blocks can be lost
on the next rewrite — treat the page as generated output (status edits and
caption tweaks inside the blocks are preserved).
- **Re-translate a post**: `uv run syndicator run --post <slug> --force`
(re-renders the bundle and re-translates all languages).
- **Caption quality/model**: per-channel `caption_model` in
`syndicator.yaml`; prompts live in `prompts/caption_*.md`.
- **Watcher loops or never triggers**: check `journalctl -u syndicator-watch`.
It ignores `syndicator___*` pages, `assets/syndicator/`, `.stversions/`,
`logseq/bak/` and Syncthing temp files by design.
- **git push fails from systemd**: the service user needs non-interactive
auth for the sailingnomads remote (SSH key without passphrase or
credential helper).
