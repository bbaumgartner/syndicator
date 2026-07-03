# Agent Instructions

Syndicator is the publish pipeline for sailingnomads.ch: it turns blog posts
written in a Logseq diary into a multilingual Hugo site, a journey map, and
social media post packages that are reviewed in Logseq.

## Documentation — where to look

1. `docs/architecture.md` — the concepts (Artifact, Node, Pipeline,
   Orchestrator, Driver, State, Edge) and the quality goals. Read it before
   any structural change and describe changes in its vocabulary
   ("X is a pure Node").
2. `README.md` — operating the system: setup, commands, daily workflows,
   troubleshooting.
3. Module docstrings — concrete behavior and file formats (e.g. `state.py`
   for the review-page format, `extract.py` for source parsing).

## Commands

```bash
uv run pytest -q        # tests: fast, offline, the LLM is faked
uv run ruff check       # lint
uv run syndicator ...   # the CLI (commands listed in README.md)
```

## Hard rules

- Published social posts are immutable: never regenerate them or touch their
  media, regardless of flags.
- The Logseq graph is a private diary. Write only into the pipeline's owned
  targets: `pages/syndicator___*.md`, `assets/syndicator/`, and the
  `syndication::` / `hugo-hash::` properties on blog property blocks. Only
  content marked as a public blog post may reach an LLM or an export.
- All LLM access goes through `llm.py`; prompts live in `prompts/`; model
  names live in `syndicator.yaml`. No inline prompts, no hardcoded models.
- No workflow framework: pipelines stay plain Python in `pipeline.py`.
- If a change introduces a new concept, not just a new instance of an
  existing one (Node, channel, language), update `docs/architecture.md`
  first.
