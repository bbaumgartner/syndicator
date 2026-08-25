# Agent instructions

## n8n workflows

Do not edit files under `n8n/workflows/`. Those JSON files are an export of the live n8n instance, not the working copy. Reading them (for workflow IDs, node names, or current logic) is fine.

When a workflow must change:

1. Inspect and edit the **live** n8n workflow with the n8n MCP tools (`get_workflow_details`, `update_workflow`, `publish_workflow`, and related tools).
2. Publish the workflow if production or parent workflows should pick up the change.
3. Stop. Do not write the JSON back into this repo.

After the live change is confirmed good, the human operator updates the repo with:

```bash
scripts/export-workflows.sh
```

(`bin/syndicator export` runs the same script.) Do not run the export unless asked.

Do not use `bin/syndicator verify` to push a workflow change into n8n. Verify reconciles **from git into n8n** and can overwrite a live MCP edit that is not yet exported.
