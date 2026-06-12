# Syndicator

Publish pipeline for [sailingnomads.ch](https://www.sailingnomads.ch): converts blog posts
written in a [Logseq](https://logseq.com) diary into

- **Hugo page bundles** (multilingual, replaces `logseq-to-hugo-converter`),
- **LLM translations** (en/de/es/fr/it + pirate speak),
- the animated **journey map** on the homepage,
- and **social media post packages** (Facebook, Instagram, X): one post per blog
  *section*, with platform-tailored AI captions and platform-adapted media.

V1 delivers social posts as reviewable export packages (`review.html`) for manual
posting; API posting and an agent mode are later phases. See
`docs/runbook.md` for operations.

## Architecture in one paragraph

A lightweight, file-based pipeline (no workflow framework): pure-code nodes for
parsing/media/publishing, LLM nodes defined by a prompt template in `prompts/`
plus a model from `syndicator.yaml`. Intermediate artifacts live in `runs/`
(local scratch); final state and exports live in `<saillog>/.syndicator/`,
which Syncthing mirrors between the Mac and the Ubuntu server. State is tracked
per post *and* per channel (hugo, facebook, instagram, x, substack, medium),
which drives the catch-up backlog for old posts.

## Setup

```bash
uv sync
cp config.local.yaml.example config.local.yaml   # adjust paths for this machine
export OPENAI_API_KEY=sk-...
uv run syndicator check
```

## Commands

```bash
uv run syndicator check                  # validate config, paths and tools
uv run syndicator status                 # backlog per channel
uv run syndicator bootstrap              # initialize per-channel state for existing posts
uv run syndicator run [--post SLUG]      # full pipeline for new/changed posts
uv run syndicator catchup [--post SLUG]  # social packages for the oldest pending post
uv run syndicator done SLUG [--channel]  # mark as published after manual posting
uv run syndicator review                 # open the latest review.html
uv run syndicator watch                  # daemon mode (normally on the server)
```
