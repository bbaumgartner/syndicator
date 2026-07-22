# Syndicator

Publish pipeline for [sailingnomads.ch](https://www.sailingnomads.ch).

**v2 (n8n migration).** Syndicator is now a **thin local trigger**. It is the
only component that reads the private [Logseq](https://logseq.com) diary. It
extracts `type:: blog` + `status:: online` posts, adapts their media locally
(ffmpeg + Pillow), uploads everything to an SFTP staging area, and fires two
n8n webhooks. Translation, the Hugo render, captions and the social drafts run
in **three stateless n8n workflows**. Hugo-ready output mirrors the repository
under `/syndicator/sailingnomads/`, from where the owner fetches it into the
site checkout and commits by hand (the push deploys the site). Social posts land
as **Postiz drafts**; the human edits captions and schedules them in the
Postiz calendar.

```
Logseq  →  syndicate (local)  →  SFTP staging  →  n8n  →  Postiz (drafts)
                                       ↓ /sailingnomads/
                               fetch + git push  →  GitHub (site deploy)
```

- **1 intro post per blog post** per platform (header image + English summary).
- **1 reel per video** in the post (English caption from the cover frame).
- **Link placement:** Facebook captions include the blog URL; Instagram and X
  do not (IG can't link in-post, X links hurt reach) — bio CTA instead.
- Languages on the site: `en` / `de` / `es` / `fr` / `it` + pirate (`arrr`).
- Platforms at launch: **Facebook page + Instagram + X**, all via Postiz.

The concepts and boundaries are described in
[docs/architecture.md](docs/architecture.md). This README
covers operating the local trigger.

## Setup (local trigger)

One-time, per machine (Mac first; stays Linux-compatible for the server later):

```bash
git clone git@github.com:bbaumgartner/syndicator.git ~/git/syndicator && cd ~/git/syndicator
curl -LsSf https://astral.sh/uv/install.sh | sh     # if uv is missing
uv sync
cp config.local.yaml.example config.local.yaml      # adjust paths + sftp_key!
```

Requirements:

- `ffmpeg` and `go` (the journeymap/animatemap tools are built/run from the
  converter repo referenced in `config.local.yaml`).
- The Syncthing-synced Logseq graph (`saillog_dir`).
- A local clone of the Hugo site repo with push access — the site commit is a
  manual step in that checkout.
- An SSH key for the chrooted `sftp` staging user (`sftp_key`), reachable at
  the host in `syndicator.yaml` (`sftp.host`).
- The two n8n production webhook URLs, filled into `syndicator.yaml`
  (`webhooks.publish_url` / `webhooks.reel_url`) once the workflows are active.

No `OPENAI_API_KEY` is needed locally except for the crop-focus vision model
used by media adaptation (`media.crop_focus`); translation and captions now run
in n8n with the OpenAI credential stored there.

## Commands

Only two commands remain:

```bash
uv run syndicator syndicate                 # all new online posts (no marker yet)
uv run syndicator syndicate --post <slug>   # only that post
uv run syndicator redeploy --post <slug>    # site-only rebuild (re-translates), no drafts
uv run syndicator version
```

`syndicate` per invocation: generate + upload the global journey map once, then
per post adapt media → SFTP upload → one `/reel` per video → `/publish` → write
the `syndicated-at::` marker once every webhook returned `{"status":"accepted"}`.
Already-marked posts are skipped (re-running would create duplicate drafts). A
`status:: online` post **without a `header::` image is refused** before any work
(reported and skipped in batch; the others continue) — the site build requires a
featured image and the intro posts are built from the header crops.

`redeploy` ignores the marker and re-runs the site only (`flags.redeploy: true`
→ n8n overwrites the Hugo files in the mirrored staging tree, but creates no
drafts).

The second half of a publish is **manual by design**. The complete Hugo tree is
already laid out on SFTP like the repository:

```text
/syndicator/sailingnomads/
├── content/posts/<slug>/
│   ├── index.de.md
│   ├── index.en.md
│   ├── …
│   └── <all post media>
└── static/journey-map.mp4
```

Fetch `/syndicator/sailingnomads/` recursively into the existing
`/Users/benno/git/sailingnomads/` checkout, review `git diff`, then commit and
push. There is no manifest or rearranging step.

## Daily workflow

### New blog post

1. Write in Logseq as usual, add a `header::` image, set `status:: online`.
2. Run `uv run syndicator syndicate` (optionally `--post <slug>`).
3. Once n8n has finished (a few minutes), fetch
   `/syndicator/sailingnomads/` into the local `sailingnomads` checkout,
   review, commit and push — that takes the site live. Intro + reel **drafts**
   appear in the **Postiz calendar**.
4. In Postiz: edit captions if needed, tag locations, and **schedule** each
   draft. Nothing reaches a platform until you schedule it there.

### Cutover (once)

Before the first batch `syndicate`, **hand-seed** `syndicated-at::` on every
already-published `status:: online` post so they are not re-drafted.

## Failure & recovery

The `syndicated-at::` marker means **handed off**, not **published** (the n8n
workflows respond early and run async). Recovery depends on the failure class:

- **Handoff failure** (a webhook never returns `accepted` after 3 local
  retries): no marker is written, so the next `syndicate` re-runs the post.
  Duplicates are harmless (the site staging is idempotent; delete duplicate
  drafts by hand).
- **Site async failure** (render / translate in n8n): run
  `redeploy --post <slug>` — it ignores the marker and overwrites that post in
  the mirrored Hugo staging tree.
- **Social async failure** (Postiz drafts): no CLI path by design. Re-run the
  failed execution in n8n (the failure email carries the execution URL) or fix
  the draft directly in Postiz.

The `Syndicator Error` n8n workflow emails every production failure (workflow
name, error, execution URL); that URL is the entry point for manual recovery.

## Troubleshooting

- **Webhook not accepted / timeouts:** check the n8n workflow is active and the
  URL in `syndicator.yaml` matches the production webhook.
- **SFTP upload fails:** verify `sftp_key`, `sftp.host`/`sftp.user`, and that
  the `/syndicator/` base dir exists in the chroot (see the migration doc §8).
- **Post refused for missing header:** add a `header::` image and re-run.
- **Caption/model or translation quality:** the cloud prompts and model names
  live on the n8n OpenAI nodes (authoritative for the cloud steps); the local
  `prompts/` + `syndicator.yaml` only cover the local crop-focus model.
- **Staging area fills up:** nobody deletes automatically; purge the SFTP
  `/syndicator/` tree periodically by hand (e.g. a cron `find -mtime` job).
