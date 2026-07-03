"""caption node: LLM-generated, platform-tailored captions per post intent.

One prompt template per channel (prompts/caption_<channel>.md); model per
channel from config. Output is a validated SocialDraft; the final post text
(link, hashtags) is assembled deterministically by compose_post_text().

Character-budget behavior (LLM retry + tail-dropping composition) is
triggered by ``ch_cfg.max_chars`` being set, not by channel name — X is
simply the only built-in channel that sets it today.
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
INSTAGRAM_BIO_CTA = "Read more by following the link in our bio."


def _jinja(cfg: Config) -> Environment:
    # Prompts ship with the code, independent of the configured data paths.
    return Environment(loader=FileSystemLoader(REPO_ROOT / "prompts"), keep_trailing_newline=True)


def text_budget(ch_cfg: ChannelConfig) -> int:
    max_chars = ch_cfg.max_chars or 280
    return max_chars - (TCO_LINK_LEN + 2) - X_HASHTAG_RESERVE


def _caption_context(post: BlogPost, intent: PostIntent) -> dict:
    """Minimal LLM context: full text only for the target part, titles elsewhere."""
    sections = post.sections
    ctx: dict = {
        "blog_post_title": post.meta.title,
        "section_titles": [s.title for s in sections if s.title],
        "write_about_this_part": _intent_part(post, intent),
        "attached_media": _media_descriptions(intent),
        "youtube_links": youtube_links(post, intent),
    }
    if intent.kind == "section":
        ctx["section_index"] = intent.section_index
        ctx["section_count"] = len(sections)
        ctx["post_format"] = intent.format
    if post.meta.position:
        ctx["position_hint"] = post.meta.position
    return ctx


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


def youtube_links(post: BlogPost, intent: PostIntent) -> list[str]:
    if intent.kind == "intro":
        return []
    section = post.sections[intent.section_index or 0]
    return [m.url for m in section.media if m.kind == "youtube" and m.url]


def _sanitize(draft: SocialDraft) -> SocialDraft:
    text = URL_RE.sub("", draft.text).strip()

    hashtags = []
    for tag in draft.hashtags:
        tag = tag.strip().replace(" ", "")
        if not tag.startswith("#"):
            tag = f"#{tag}"
        if tag == "#" or tag in hashtags:
            continue
        hashtags.append(tag)

    location = URL_RE.sub("", draft.location).strip()[:80]

    return SocialDraft(text=text, hashtags=hashtags, location=location)


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
        text_budget=text_budget(ch_cfg),
    )

    user = json.dumps(_caption_context(post, intent), ensure_ascii=False, indent=1)

    draft = llm.complete_structured(
        node=f"caption_{intent.channel}",
        model=ch_cfg.caption_model,
        system=system,
        user_content=user,
        schema=SocialDraft,
    )
    draft = _sanitize(draft)

    if ch_cfg.max_chars is not None:
        draft = _enforce_text_budget(draft, ch_cfg, system, user, llm, cfg, intent.channel)

    return draft


def _enforce_text_budget(
    draft: SocialDraft,
    ch_cfg: ChannelConfig,
    system: str,
    user: str,
    llm: LLMClient,
    cfg: Config,
    channel: str,
) -> SocialDraft:
    budget = text_budget(ch_cfg)
    if len(draft.text) <= budget:
        return draft

    retry_user = user + _jinja(cfg).get_template("caption_shorten.md").render(
        previous_length=len(draft.text),
        budget=budget,
        previous_text=draft.text,
    )
    shorter = llm.complete_structured(
        node=f"caption_{channel}",
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
        parts = [draft.text, INSTAGRAM_BIO_CTA]
        if hashtags:
            parts.append(hashtags)
        return "\n\n".join(parts)

    if ch_cfg.max_chars is not None:
        return _compose_budgeted_text(draft, ch_cfg, url)

    parts = [draft.text]
    parts.extend(youtube_links)
    if url:
        parts.append(url)
    if hashtags:
        parts.append(hashtags)
    return "\n\n".join(parts)


def _post_length(text: str, tags: list[str], url: str) -> int:
    """Effective character count; every URL is wrapped into a t.co-length link."""
    tail_lengths = [len(t) for t in tags] + ([TCO_LINK_LEN] if url else [])
    if not tail_lengths:
        return len(text)
    return len(text) + 2 + sum(tail_lengths) + len(tail_lengths) - 1


def _compose_budgeted_text(draft: SocialDraft, ch_cfg: ChannelConfig, url: str) -> str:
    """Character-budgeted post: text, blank line, hashtags and link.

    The hashtag reserve in the text budget is only a suggestion to the LLM;
    enforce the hard platform limit here by dropping trailing hashtags (the
    link always survives — clicks on it are the point of the post).
    """
    max_chars = ch_cfg.max_chars or 280
    tags = list(draft.hashtags)
    while tags and _post_length(draft.text, tags, url) > max_chars:
        tags.pop()
    tail = " ".join(filter(None, [" ".join(tags), url]))
    return f"{draft.text}\n\n{tail}" if tail else draft.text
