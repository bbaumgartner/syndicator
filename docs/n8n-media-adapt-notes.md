# n8n media adaptation notes

Media adaptation now runs in n8n. The local client uploads immutable originals
under `/syndicator/<slug>/source/` only and posts the new webhook contracts.

## Workflow IDs

| Workflow | ID | Role |
|---|---|---|
| Adapt Feature Image | `8NOGn9jgOoV0fw0u` | Sub-workflow: feature → social headers |
| Adapt Hugo Media | `OGa6Xa8GxkSmA7Cr` | Sub-workflow: Hugo media copy/resize from blocks |
| Adapt Reel Media | `y9TTx7N8Iygn88ry` | Sub-workflow: reel 4:5 adapt only |
| Reel Publish | `zh21miLsQC8Jvua6` | Webhook `/reel` → adapt → extract cover → caption → Postiz |
| Blog Post Publish | `l7HCCWtO1ALC82n6` | Webhook `/publish` → Hugo adapt + social feature adapt |
| Syndicator Error | `O6fHa4LyBB7P71nU` | Shared `errorWorkflow` |

All of the above (except Error) are published; Adapt\* set
`callerPolicy=workflowsFromSameOwner` and `errorWorkflow=O6fHa4LyBB7P71nU`.

## Contracts

Layout conventions (hardcoded in n8n Adapt / Hugo emit):

- source: `/syndicator/<slug>/source/<source_filename>`
- Hugo dest: images keep `source_filename`; videos → `<stem>.mp4`
  under `/syndicator/sailingnomads/content/posts/<slug>/`
- featured: source `header.<ext>` → Hugo `featured.<ext>`
- social headers: `/syndicator/<slug>/header/<platform>.jpg`
- reel: `/syndicator/<slug>/reels/4x5/<index>.mp4`

### `/reel` (client → Reel Publish)

```json
{
  "slug": "...",
  "post": { "title": "...", "url": "...", "summary": "...", "lang_code": "..." },
  "video": { "index": 1, "section_title": "...", "section_text": "...", "alt": "..." },
  "source": { "filename": "clip.mov" }
}
```

Reel Publish calls **Adapt Reel Media** with `{ slug, index, source_filename }`,
merges `video_4x5_sftp` + `video_4x5_local`, then **Extract Cover** once from
the local 4:5 video → Caption FB/IG/X (same cover binary) → each platform
downloads that same video from SFTP → Postiz upload/draft. Covers are never
written to SFTP.

### Adapt Reel Media I/O

- **In:** `slug`, `index`, `source_filename`
- **Out:** `{ video_4x5_sftp, video_4x5_local }`
- **Writes:** `/syndicator/<slug>/reels/4x5/<index>.mp4` only
- **Never** writes under `source/` or `covers/`
- Hardcoded single 4:5 1080×1350 encode (full source duration). Later a
  parallel 9:16 Shorts encode can be added in this workflow; Reel Publish
  would then point YouTube at that path.
- Crop math mirrors `docs/n8n-crop-box.js` / `syndicator.crop_math`
- Plan reference: `docs/n8n-plan-4x5-adapt.js`
- OpenAI image analyze uses `prompts/crop_focus.md` semantics → `{x,y}` 0–1
- Cover frames are extracted in **Reel Publish** from `video_4x5_local`

### `/publish` (client → Blog Post Publish)

```json
{
  "slug": "...",
  "meta": { "...": "..." },
  "post_url": "...",
  "blocks": [
    {
      "kind": "media",
      "media": {
        "kind": "image",
        "source_filename": "photo.png",
        "alt": "..."
      }
    },
    {
      "kind": "media",
      "media": {
        "kind": "video",
        "source_filename": "clip.MOV",
        "alt": "..."
      }
    }
  ],
  "header_source": "header.jpg",
  "flags": { "redeploy": false }
}
```

No Hugo dest names in the body. `source_filename` is the basename under
`source/`. n8n owns renaming (videos → `.mp4`) in Adapt Hugo Media and
Generate Hugo Index MDs. `header_source` is the featured original basename
(`header.<ext>`).

Blog Post Publish calls **Adapt Hugo Media** on the Hugo branch only
(after Assemble, before Generate Hugo Index MDs), and **Adapt Feature Image**
only on the social branch (after Skip Social If Redeploy).

### Adapt Feature Image I/O

- **In:** `slug`, `header_source` (basename, e.g. `header.jpg`)
- **Out:** `{ ok, slug, header: { facebook, instagram, x } }`
- Crop-focus OpenAI once on the feature image → Edit Image →
  `/syndicator/<slug>/header/<platform>.jpg`
  - Instagram: 4:5 crop → 1080×1350 jpeg
  - Facebook / X: max_edge 2048 jpeg
- Empty `header_source` → `{ header: {} }`

### Adapt Hugo Media I/O

- **In:** `slug`, `blocks[]`, `header_source`
- **Out:** `{ ok, slug }`
- Builds jobs from media blocks + featured (`header.<ext>` → `featured.<ext>`):
  - images: SFTP copy source → Hugo dest (same basename)
  - videos: FFmpeg **resize only** to fit inside 700×394; dest `<stem>.mp4`
- **Never** writes under `source/`
- Social has no videos here; only the feature image is cropped (separate workflow)

Social Postiz intros download the adapted header paths. `flags.redeploy`
still skips social via **Skip Social If Redeploy**.

## Node graph (high level)

```
Reel Publish
  Webhook /reel → Respond → Parse
    → Execute Adapt Reel Media → Merge Adapt Into Payload
      → Extract Cover (from video_4x5_local) → Caption FB | IG | X
        → Download Reel (same video_4x5_sftp) → Postiz upload → Draft

Blog Post Publish
  Webhook /publish → Respond
    → Prepare Blocks → Translate → Pirate → Assemble
      → Adapt Hugo Media → Generate Hugo indexes → Stage → Upload indexes
      → Skip Social If Redeploy → Adapt Feature Image
        → Caption → Download header → Postiz
```

## Credentials used

- FTP account (`sftp`)
- OpenAI account (`openAiApi`)
- Postiz account (unchanged on existing draft nodes)

## Blockers / follow-ups

1. **SFTP parent directories:** FTP upload nodes do not auto-create nested
   dirs in all server configs. The local client pre-creates
   `/syndicator/<slug>/{header,reels}/` and the Hugo post dir before webhooks.
2. **`readWriteFile` / ffmpeg temps:** Adapt workflows write under `/files/`
   inside the n8n container (allowlisted volume). Do not use `/tmp` or `$env`
   (`N8N_BLOCK_ENV_ACCESS_IN_NODE`). FFmpeg Studio needs filesystem paths.
3. **OpenAI discriminator warnings** on Blog Post Publish caption/translate
   nodes were pre-existing (missing explicit `operation` in saved params) and
   were not changed by this work; production executions were already using them.
4. **End-to-end test** with a real `/reel` and `/publish` payload against SFTP
   has not been run from this session.
5. JSON exports under `docs/*.json` are node/connection summaries (credentials
   omitted), not full n8n import bundles.
