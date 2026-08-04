# Syndicator

**Syndicate** interface: accept blog posts from any editor, stage originals on
SFTP, adapt media, render a Hugo tree, and create social drafts (via n8n + Postiz).

This repository documents the public contract and hosts the **pyautoflip**
sidecar used for reel reframing. It does **not** read Logseq or any other
editor format. Logseq users should use
[logseq-blogger](https://github.com/bbaumgartner/logseq-blogger), which
implements the logseq-blog input format and calls this interface.

```
Any blog editor  →  SFTP source/ + webhooks  →  n8n (adapt + publish)  →  Postiz
                              ↓ hugo-site/
                      fetch + git push  →  Hugo site deploy
```

- **1 intro post per blog post** per platform (header image + English summary).
- **1 reel per video** in the post (English caption from the cover frame).
- Platforms: **Facebook + Instagram + X** via Postiz (as configured in n8n).

---

## The syndicate interface

Callers invoke syndicate by (1) uploading immutable originals over SFTP, then
(2) POSTing JSON to the Blog Post Publish and Reel Publish webhooks. n8n
responds with HTTP 2xx as soon as the request is accepted (`onReceived`) and
continues asynchronously.

### Invocation order

1. Upload all files for the post under `<base>/<slug>/source/`.
2. For each local video: `POST` **Reel Publish** (zero or more calls).
3. `POST` **Blog Post Publish** once.

Do not fire webhooks before the referenced source files exist on SFTP.

### SFTP layout

Base directory is typically `/syndicator` (chrooted SFTP user).

**Client writes (immutable originals):**

```text
<base>/<slug>/source/
├── header.<ext>          # featured image (required for publish)
├── photo.jpg             # body assets by basename
└── clip.mp4
```

| Path | Role |
|------|------|
| `<base>/<slug>/source/<filename>` | Original media uploaded by the caller |
| `<base>/<slug>/source/header.<ext>` | Featured/header image (basename convention) |

**n8n writes (downstream; not a client write):**

```text
<base>/hugo-site/
└── content/posts/<slug>/
    ├── index.<lang>.md
    ├── …
    └── <post media>
```

Operators fetch `/syndicator/hugo-site/` into their Hugo site checkout, review,
commit, and push. Social derivatives (header crops, reel encodings) may also
appear under `<base>/<slug>/` as produced by Adapt workflows.

Auth: key-only SFTP for a chrooted staging user.

### Blog Post Publish

`POST` JSON to the configured publish webhook URL (path `/webhook/publish`).

Any HTTP 2xx means accepted. Response body is ignored. Callers typically retry
a few times with backoff on transport/HTTP errors.

```json
{
  "slug": "2024-06-14_Renan",
  "meta": {
    "title": "Renan",
    "date": "2024-06-14",
    "language": "english",
    "lang_code": "en",
    "author": "Benno",
    "summary": "Short summary…",
    "position": "38.98000,1.430000"
  },
  "post_url": "https://example.org/posts/2024-06-14_renan/",
  "blocks": [
    {"kind": "text", "raw": "First paragraph…"},
    {"kind": "title", "raw": "### The Idea", "heading_level": 3},
    {
      "kind": "media",
      "media": {
        "kind": "image",
        "source_filename": "photo.jpg",
        "alt": "photo"
      }
    },
    {
      "kind": "youtube",
      "media": {"kind": "youtube", "youtube_id": "FAIZtHHsbSM"}
    },
    {
      "kind": "media",
      "media": {
        "kind": "video",
        "source_filename": "clip.mp4",
        "alt": "clip"
      }
    }
  ],
  "header_source": "header.jpg",
  "flags": {"redeploy": false}
}
```

| Field | Meaning |
|-------|---------|
| `slug` | Post id; must match the SFTP directory name under `<base>/` |
| `meta.*` | Title, date, language word + `lang_code`, author, summary, position |
| `post_url` | Canonical URL for the post (caller-computed) |
| `blocks[]` | Ordered content; kinds `title`, `text`, `youtube`, `media` |
| `blocks[].media.source_filename` | Basename already present under `…/source/` |
| `header_source` | Basename of the header file under `…/source/` (e.g. `header.jpg`) |
| `flags.redeploy` | `false` = full publish (site + social drafts); `true` = site-only rebuild, no drafts |

### Reel Publish

`POST` JSON to the configured reel webhook URL (path `/webhook/reel`).
One call per local video. The file named in `source.filename` must already exist
under `<base>/<slug>/source/`.

```json
{
  "slug": "2024-06-14_Renan",
  "post": {
    "title": "Renan",
    "url": "https://example.org/posts/2024-06-14_renan/",
    "summary": "Short summary…",
    "lang_code": "en"
  },
  "video": {
    "index": 1,
    "section_title": "The Player",
    "section_text": "Prose with a [VIDEO] marker at the clip…",
    "alt": "dingy.mp4"
  },
  "source": {"filename": "dingy.mp4"}
}
```

| Field | Meaning |
|-------|---------|
| `video.index` | 1-based index of this video in the post |
| `video.section_title` | Nearest section heading, if any |
| `video.section_text` | Caption context; conventionally includes a `[VIDEO]` marker |
| `source.filename` | Basename under `…/source/` |

### Effects

After acceptance, n8n asynchronously:

- Adapts stills and reels (Edit Image / OpenAI crop-focus; pyautoflip for saliency reframing)
- Translates and writes Hugo bundles under `<base>/hugo-site/content/posts/<slug>/`
- Creates **Postiz drafts** for intro posts and reels (unless `flags.redeploy: true`)

There is **no editor-side marker** in this interface. Callers (e.g. logseq-blogger)
own their own “already handed off” state.

---

## Server-side pieces in this repo

### pyautoflip sidecar

HTTP wrapper around saliency-aware video reframing for the n8n Adapt Reel Media
workflow. See [`services/pyautoflip/README.md`](services/pyautoflip/README.md)
for the `/health` and `/reframe` API and how to wire it into the n8n compose
stack. This is an implementation detail of reel adapt, not part of the public
webhook contract above.

### Operating notes

- Activate the Blog Post Publish and Reel Publish n8n workflows and point
  callers at the production webhook URLs.
- Staging is not garbage-collected automatically; purge `/syndicator/`
  periodically (e.g. `find -mtime`).
- Align the n8n Hugo output directory with this contract: `<base>/hugo-site/`
  (not a site-specific name).

## Related

- [logseq-blogger](https://github.com/bbaumgartner/logseq-blogger) — Logseq
  `type:: blog` client of this interface
