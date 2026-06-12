# Syndicator Runbook

Operations guide: two-machine setup, cutover from `logseq-to-hugo-converter`,
daily workflows and troubleshooting.

## How everything fits together

```
Logseq (saillog, synced via Syncthing between Mac and Ubuntu server)
   │
   │  syndicator watch (Ubuntu server, systemd) — or manual `syndicator run`
   ▼
detect changed/new posts (per-channel state in saillog/.syndicator/state/)
   ├─ site:   hugo bundles → translations (cached) → journey map → git push → Netlify
   └─ social: sections → captions (LLM) → media crops/reels → export packages
              → saillog/.syndicator/exports/<slug>/review.html  (synced to the Mac)
```

- **State and exports** live in `<saillog>/.syndicator/` and are mirrored by
  Syncthing: generate on the server, review on the Mac, automatic archive.
- **One watcher**: the daemon runs on the server. The Mac is for manual
  commands (`catchup`, `review`, `done`, `run`). A lock file in
  `.syndicator/` prevents simultaneous pipeline runs.
- **Channel status lifecycle**: `pending` → `exported` (package generated) →
  `published` (you posted it manually and ran `syndicator done`).

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

## Cutover from logseq-to-hugo-converter

The state was bootstrapped on 2026-06-12 (all 15 posts: hugo published;
social/substack/medium pending except Renan). Steps to switch the website
pipeline:

1. **Parity check** (Mac or server):

   ```bash
   uv run syndicator parity
   ```

   `DIFF` entries mean the Logseq source changed after the last old-converter
   run (at bootstrap time: `Frühlingspläne_2026` and `Athen`). They will be
   regenerated and re-translated on the first run — that is correct and what
   the old converter would also have done.

2. **Dry run** and inspect what would change:

   ```bash
   uv run syndicator run --dry-run        # bundles land in runs/dry-site/
   ```

3. **Stop the old watcher** (wherever it runs; on the server typically):

   ```bash
   sudo systemctl disable --now logseq-watch-and-convert.service
   ```

4. **First real run**, watch the output (translations for the two stale
   posts cost a few cents):

   ```bash
   uv run syndicator run
   ```

   Then verify https://www.sailingnomads.ch/ (post pages, languages,
   journey map).

5. **Enable the daemon** on the server:

   ```bash
   sudo systemctl enable --now syndicator-watch.service
   journalctl -u syndicator-watch -f
   ```

## Daily workflows

### New blog post

Write in Logseq as usual, set `status:: online`. The server daemon picks it
up (default: 15 min debounce), publishes the site, waits for the Netlify
deploy, then generates the social packages. On the Mac:

```bash
uv run syndicator review            # opens the newest review.html
# post manually on FB/IG/X (copy buttons, adapted media in the package dirs)
uv run syndicator done <slug>       # marks all exported channels as published
```

Substack/Medium stay manual (Narrareach) for now; track them with
`syndicator done <slug> -c substack -c medium`.

### Catch-up (old posts, over the next weeks/months)

```bash
uv run syndicator status            # backlog per channel
uv run syndicator catchup           # oldest pending post → export package
uv run syndicator catchup --post 2026-05-19_Charly_Superstar   # or pick one
uv run syndicator review <slug>
# post manually over the suggested dates, then:
uv run syndicator done <slug>
```

### Useful commands

```bash
uv run syndicator run --post <slug> --force    # re-run one post end to end
uv run syndicator run --site-only              # website only, no social
uv run syndicator catchup --force --post <slug>  # re-export existing packages
uv run syndicator parity                       # fresh render vs live repo
```

## Troubleshooting

- **"pipeline lock held by another machine"**: a run is active on the other
  machine, or it crashed. The lock expires after 1 h; to clear immediately,
  delete `<saillog>/.syndicator/lock.json`.
- **Syncthing conflict files** (`*.sync-conflict-*`) in `.syndicator/state/`:
  rare (one file per post, atomic writes). Keep the newer file, delete the
  conflict copy; worst case re-run `syndicator bootstrap` and re-mark with
  `syndicator done`.
- **Re-translate a post**: `uv run syndicator run --post <slug> --force`
  (re-renders the bundle and re-translates all languages).
- **Caption quality/model**: per-channel `caption_model` in
  `syndicator.yaml`; prompts live in `prompts/caption_*.md`. Costs are
  logged per export in `exports/<slug>/costs.txt` (maintain prices in
  `model_prices`).
- **Watcher loops or never triggers**: check `journalctl -u syndicator-watch`.
  It ignores `.syndicator/`, `.stversions/`, `logseq/bak/` and Syncthing
  temp files by design.
- **git push fails from systemd**: the service user needs non-interactive
  auth for the sailingnomads remote (SSH key without passphrase or
  credential helper).

## Costs (reference, first real export 2026-06-12)

One post (SKS, 5 sections → 18 captions + 9 vision crop calls): ~28k input /
~5.6k output tokens. Translations are cached and only run when the source
changes (5 languages, one body + title call each). Overall well below 1 USD
per post with the default models.
