"""translate node: LLM translation of the rendered post body.

Behavior port of the old cmd/translate: identical prompts, temperatures
(0.3 / 0.9 for pirate speak), positional asset-reference restoration,
per-language disclaimer, summary = first paragraph of the translated body,
pirate speak keeps the original title.

Called only when ``hugo-hash`` on the blog property block differs from the
current source hash (see ``run_site_for_post``); there is no separate translation
cache.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..config import REPO_ROOT, Config
from ..llm import LLMClient
from ..model import LANGUAGE_NAMES, BlogPost
from .hugo import build_content, escape_toml, transform_content

log = logging.getLogger(__name__)

MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
SHORTCODE_SRC_RE = re.compile(r"\{\{<[^}]*\ssrc=\"([^\"]+)\"[^}]*>\}\}")

DISCLAIMERS = {
    "en": "---\n\n*This blog post has been automatically translated by a Large Language Model.",
    "de": "---\n\n*Dieser Blogbeitrag wurde automatisch von einem Large Language Model übersetzt.",
    "es": "---\n\n*Esta publicación de blog ha sido traducida automáticamente por un Large Language Model.",
    "fr": "---\n\n*Cet article de blog a été traduit automatiquement par un Large Language Model.",
    "it": "---\n\n*Questo post del blog è stato tradotto automaticamente da un Large Language Model.",
    "arrr": "---\n\n*Arrr, this here blog post be rewritten in the tongue o' pirates by a Large Language Model, ye scallywag!*",
}


def restore_asset_references(source: str, translated: str) -> str:
    """Copy image targets and shortcode src values from source, positionally."""
    source_paths = [m.group(2) for m in MARKDOWN_IMAGE_RE.finditer(source)]
    if source_paths:
        idx = 0

        def replace_image(m: re.Match[str]) -> str:
            nonlocal idx
            if idx >= len(source_paths):
                return m.group(0)
            restored = f"![{m.group(1)}]({source_paths[idx]})"
            idx += 1
            return restored

        translated = MARKDOWN_IMAGE_RE.sub(replace_image, translated)

    src_paths = [m.group(1) for m in SHORTCODE_SRC_RE.finditer(source)]
    if src_paths:
        idx2 = 0

        def replace_src(m: re.Match[str]) -> str:
            nonlocal idx2
            if idx2 >= len(src_paths):
                return m.group(0)
            replaced = m.group(0).replace(f'src="{m.group(1)}"', f'src="{src_paths[idx2]}"', 1)
            idx2 += 1
            return replaced

        translated = SHORTCODE_SRC_RE.sub(replace_src, translated)

    return translated


def extract_first_paragraph(content: str) -> str:
    """First paragraph: lines until a blank line / heading / horizontal rule."""
    collected: list[str] = []
    for line in content.split("\n"):
        trimmed = line.strip()
        if not collected and not trimmed:
            continue
        if collected and not trimmed:
            break
        if trimmed.startswith("#"):
            break
        if trimmed in ("---", "***", "___"):
            break
        collected.append(line)
    return " ".join(collected).strip()


def disclaimer_for(lang: str) -> str:
    return DISCLAIMERS.get(lang, DISCLAIMERS["en"])


def _system_prompt(source_lang: str, target_lang: str) -> str:
    env = Environment(loader=FileSystemLoader(REPO_ROOT / "prompts"), keep_trailing_newline=False)
    if target_lang == "arrr":
        return env.get_template("translate_pirate.md").render()
    return env.get_template("translate.md").render(
        source_name=LANGUAGE_NAMES.get(source_lang, source_lang),
        target_name=LANGUAGE_NAMES.get(target_lang, target_lang),
    )


def _temperature(cfg: Config, target_lang: str) -> float:
    if target_lang == "arrr":
        return cfg.shared.translate.pirate_temperature
    return cfg.shared.translate.temperature


def translate_text(llm: LLMClient, cfg: Config, text: str, source_lang: str, target_lang: str) -> str:
    return llm.complete_text(
        node=f"translate_{target_lang}",
        model=cfg.shared.translate.model,
        system=_system_prompt(source_lang, target_lang),
        user=text,
        temperature=_temperature(cfg, target_lang),
    )


def translated_index_content(
    post: BlogPost,
    body: str,
    title: str,
    summary: str,
) -> str:
    """Hugo index file content with the translated front matter and body
    (same field order and escaping as the old translate writer)."""
    return (
        "+++\n"
        f'date = "{escape_toml(post.meta.date)}"\n'
        f'lastmod = "{escape_toml(post.meta.date)}"\n'
        "draft = false\n"
        f'title = "{escape_toml(title)}"\n'
        f'summary = "{escape_toml(summary)}"\n'
        "[params]\n"
        f'  author = "{escape_toml(post.meta.author)}"\n'
        "+++\n\n"
        f"{body}\n"
    )


def translation_target_langs(cfg: Config, post: BlogPost) -> list[str]:
    return [lang for lang in cfg.shared.languages.supported if lang != post.lang_code]


def translate_bundle(
    post: BlogPost,
    cfg: Config,
    llm: LLMClient,
    bundle_dir: Path,
) -> list[str]:
    """Translate the post into all target languages.

    Writes index.<lang>.md next to the source-language index file.
    Returns the list of languages translated.
    """
    source_lang = post.lang_code
    source_body = transform_content(build_content(post))

    translated_langs: list[str] = []
    for lang in translation_target_langs(cfg, post):
        out_file = bundle_dir / f"index.{lang}.md"
        log.info("%s: translating to %s ...", post.slug, lang)
        body = translate_text(llm, cfg, source_body, source_lang, lang)
        body = restore_asset_references(source_body, body)
        body = body + "\n\n" + disclaimer_for(lang)

        # Pirate speak keeps the original title (avoids comically long results).
        title = post.meta.title
        if lang != "arrr" and post.meta.title:
            title = translate_text(llm, cfg, post.meta.title, source_lang, lang).strip()

        summary = extract_first_paragraph(body)

        out_file.write_text(translated_index_content(post, body, title, summary), encoding="utf-8")
        translated_langs.append(lang)

    return translated_langs
