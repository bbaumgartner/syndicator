# Syndicator

Publish pipeline for [sailingnomads.ch](https://www.sailingnomads.ch): converts blog posts
written in a [Logseq](https://logseq.com) diary into

- **Hugo page bundles** (multilingual, replaces `logseq-to-hugo-converter`),
- **LLM translations** (en/de/es/fr/it + pirate speak),
- the animated **journey map** on the homepage,
- and **social media posts** (Facebook, Instagram, X): one post per blog
  *section*, with platform-tailored AI captions and platform-adapted media.

Social posts are reviewed **inside Logseq**: the pipeline generates one review
page per blog post (`syndicator/<slug>` under `pages/`) listing every social
post with caption and media. After posting manually, flip the block property
`status:: draft` to `published` directly in Logseq (or use `syndicator done`).
API posting and an agent mode are later phases. See `docs/runbook.md` for
operations.

## Architecture in one paragraph

A lightweight, file-based pipeline (no workflow framework): pure-code nodes for
parsing/media/publishing, LLM nodes defined by a prompt template in `prompts/`
plus a model from `syndicator.yaml`. All state lives as Logseq properties on
the generated review pages (`<saillog>/pages/syndicator___<slug>.md`); adapted
media land in `<saillog>/assets/syndicator/`. Both are part of the graph, which
Syncthing mirrors between the Mac and the Ubuntu server. The blog post itself
gets a `syndication:: [[syndicator/<slug>]]` property linking to its review
page. State is tracked per post *and* per channel (hugo, facebook, instagram,
x, substack, medium) — with per-post-block status for the social channels —
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
uv run syndicator bootstrap              # create review pages for existing posts
uv run syndicator run [--post SLUG]      # full pipeline for new/changed posts
uv run syndicator catchup [--post SLUG]  # social posts for the oldest pending post
uv run syndicator done SLUG [--channel]  # mark as published (same as editing the page)
uv run syndicator review [SLUG]          # open the review page in Logseq
uv run syndicator watch                  # daemon mode (normally on the server)
```
