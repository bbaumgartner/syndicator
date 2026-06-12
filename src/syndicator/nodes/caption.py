"""caption node: LLM-generated, platform-tailored captions per post intent.

One prompt template per channel (prompts/caption_<channel>.md); model per
channel from config. Output is a validated SocialDraft; the final post text
(link, hashtags) is assembled deterministically by compose_post_text().
"""

from __future__ import annotations

import json
import re

from jinja2 import Environment, FileSystemLoader

from ..config import REPO_ROOT, ChannelConfig, Config
from ..llm import LLMClient
from ..model import LANGUAGE_NAMES, BlogPost, PostIntent, SocialDraft

URL_RE = re.compile(r"https?://\S+")
TCO_LINK_LEN = 23  # X wraps every URL into a 23-char t.co link
X_HASHTAG_RESERVE = 25


def _jinja(cfg: Config) -> Environment:
    # Prompts ship with the code, independent of the configured data paths.
    return Environment(loader=FileSystemLoader(REPO_ROOT / "prompts"), keep_trailing_newline=True)


def x_text_budget(ch_cfg: ChannelConfig) -> int:
    max_chars = ch_cfg.max_chars or 280
    return max_chars - (TCO_LINK_LEN + 2) - X_HASHTAG_RESERVE


def _post_outline(post: BlogPost) -> list[dict]:
    outline = []
    for i, section in enumerate(post.sections):
        outline.append(
            {
                "index": i,
                "title": section.title,
                "text": "\n\n".join(section.texts),
                "media_count": len(section.media),
            }
        )
    return outline


def _intent_part(post: BlogPost, intent: PostIntent) -> dict:
    if intent.kind == "intro":
        return {"kind": "intro", "title": post.meta.title, "text": post.intro}
    section = post.sections[intent.section_index or 0]
    return {
        "kind": "section",
        "title": section.title,
        "text": "\n\n".join(section.texts),
    }


def _media_descriptions(intent: PostIntent) -> list[dict]:
    return [{"kind": m.kind, "filename": m.filename, "alt": m.alt} for m in intent.media]


def _youtube_links(post: BlogPost, intent: PostIntent) -> list[str]:
    if intent.kind == "intro":
        return []
    section = post.sections[intent.section_index or 0]
    return [m.url for m in section.media if m.kind == "youtube" and m.url]


def _sanitize(draft: SocialDraft) -> SocialDraft:
    text = URL_RE.sub("", draft.text).strip()

    hashtags = []
    for tag in draft.hashtags:
        tag = tag.strip().replace(" ", "")
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = f"#{tag}"
        hashtags.append(tag)

    return SocialDraft(text=text, hashtags=hashtags)


def generate_caption(
    post: BlogPost,
    intent: PostIntent,
    cfg: Config,
    llm: LLMClient,
) -> SocialDraft:
    ch_cfg = cfg.shared.channels[intent.channel]
    language = LANGUAGE_NAMES.get(ch_cfg.language, ch_cfg.language)

    template = _jinja(cfg).get_template(f"caption_{intent.channel}.md")
    system = template.render(
        site_title=cfg.shared.site.title,
        base_url=cfg.shared.site.base_url,
        language_name=language,
        text_budget=x_text_budget(ch_cfg),
    )

    context = {
        "blog_post_title": post.meta.title,
        "blog_post_date": post.meta.date,
        "intro": post.intro,
        "outline": _post_outline(post),
        "write_about_this_part": _intent_part(post, intent),
        "attached_media": _media_descriptions(intent),
        "youtube_links": _youtube_links(post, intent),
    }
    user = json.dumps(context, ensure_ascii=False, indent=1)

    draft = llm.complete_structured(
        node=f"caption_{intent.channel}",
        model=ch_cfg.caption_model,
        system=system,
        user_content=user,
        schema=SocialDraft,
    )
    draft = _sanitize(draft)

    if intent.channel == "x":
        draft = _enforce_x_budget(draft, ch_cfg, system, user, llm)

    return draft


def _enforce_x_budget(
    draft: SocialDraft, ch_cfg: ChannelConfig, system: str, user: str, llm: LLMClient
) -> SocialDraft:
    budget = x_text_budget(ch_cfg)
    if len(draft.text) <= budget:
        return draft

    retry_user = (
        user
        + f"\n\nYour previous text was {len(draft.text)} characters; the hard limit is {budget}."
        + f" Rewrite it shorter:\n{draft.text}"
    )
    shorter = llm.complete_structured(
        node="caption_x",
        model=ch_cfg.caption_model,
        system=system,
        user_content=retry_user,
        schema=SocialDraft,
    )
    shorter = _sanitize(shorter)
    if shorter.text and len(shorter.text) <= budget:
        return SocialDraft(text=shorter.text, hashtags=shorter.hashtags or draft.hashtags)

    return SocialDraft(
        text=draft.text[: budget - 1].rstrip() + "…",
        hashtags=draft.hashtags,
    )


def compose_post_text(draft: SocialDraft, intent: PostIntent, ch_cfg: ChannelConfig,
                      url: str, youtube_links: list[str]) -> str:
    """Assemble the final, copy-paste-ready post text."""
    hashtags = " ".join(draft.hashtags)

    if ch_cfg.link_mode == "bio":
        parts = [draft.text]
        if hashtags:
            parts.append(hashtags)
        return "\n\n".join(parts)

    if intent.channel == "x":
        tail = " ".join(filter(None, [hashtags, url]))
        return f"{draft.text}\n\n{tail}" if tail else draft.text

    parts = [draft.text]
    parts.extend(youtube_links)
    if url:
        parts.append(url)
    if hashtags:
        parts.append(hashtags)
    return "\n\n".join(parts)
