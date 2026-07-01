"""Core data model: BlogPost with metadata, blocks and derived sections.

A Logseq blog branch consists of:
- a property block (``type:: blog`` plus metadata)
- an intro text block (used as summary/teaser)
- a sequence of blocks classified as title / media / youtube / text

A *section* ("Abschnitt") is the sequence: optional title, optional list of
media, list of texts. Sections are the unit that becomes one social media post
per platform.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

# Mirrors writer.go getFilename(): unknown/empty language falls back to German.
LANGUAGE_WORD_TO_CODE = {
    "german": "de",
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "italian": "it",
}

LANGUAGE_NAMES = {
    "en": "English",
    "de": "German",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "arrr": "Pirate Speak",
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".wmv", ".flv", ".webm", ".mkv", ".m4v", ".mpg", ".mpeg",
}

MediaKind = Literal["image", "video", "youtube"]
BlockKind = Literal["title", "media", "youtube", "text"]


class Meta(BaseModel):
    date: str = ""
    title: str = ""
    author: str = ""
    header: str = ""  # raw path as written in Logseq (e.g. ../assets/x.jpg)
    summary: str = ""  # explicit summary:: property, rarely used
    status: str = ""
    language: str = ""  # word as written: german / english / ...
    position: str = ""  # GPS "lat,lng" or place name; informational

    @property
    def lang_code(self) -> str:
        return LANGUAGE_WORD_TO_CODE.get(self.language.strip().lower(), "de")


class MediaRef(BaseModel):
    kind: MediaKind
    alt: str = ""
    source_path: Path | None = None  # absolute path for image/video files
    filename: str = ""  # flattened basename, as used in the Hugo bundle
    url: str = ""  # for youtube
    youtube_id: str = ""

    @property
    def exists(self) -> bool:
        return self.source_path is not None and self.source_path.exists()


class Block(BaseModel):
    kind: BlockKind
    raw: str  # block content, base indentation stripped, bullet marker removed
    heading_level: int = 0  # only for title blocks
    media: MediaRef | None = None  # only for media/youtube blocks


class Section(BaseModel):
    title: str | None = None
    media: list[MediaRef] = []
    texts: list[str] = []

    @property
    def is_empty(self) -> bool:
        return self.title is None and not self.media and not self.texts


class BlogPost(BaseModel):
    meta: Meta
    blocks: list[Block]  # content blocks in order, property block excluded
    source_path: Path

    @property
    def slug(self) -> str:
        # Identical rule to createOutputDir() in the old converter.
        return f"{self.meta.date}_{self.meta.title.replace(' ', '_')}"

    @property
    def lang_code(self) -> str:
        return self.meta.lang_code

    @property
    def intro(self) -> str:
        """The intro text block (second block of the branch)."""
        if self.blocks and self.blocks[0].kind == "text":
            return self.blocks[0].raw
        return ""

    @property
    def header_media(self) -> MediaRef | None:
        if not self.meta.header:
            return None
        path = (self.source_path.parent / self.meta.header).resolve()
        ext = path.suffix.lower()
        kind: MediaKind = "video" if ext in VIDEO_EXTENSIONS else "image"
        return MediaRef(kind=kind, alt="featured", source_path=path, filename=path.name)

    @property
    def sections(self) -> list[Section]:
        """Derive sections: optional title, optional media, texts.

        A ``###`` title always starts a new section. Within a titled section,
        further media/text blocks stay together. For untitled stretches, media
        after text starts a new section (photo-group boundaries).
        """
        sections: list[Section] = []
        current = Section()

        def flush() -> None:
            nonlocal current
            if not current.is_empty:
                sections.append(current)
            current = Section()

        content = self.blocks[1:] if (self.blocks and self.blocks[0].kind == "text") else self.blocks
        for block in content:
            if block.kind == "title":
                flush()
                current.title = block.raw.lstrip("#").strip()
            elif block.kind in ("media", "youtube"):
                if current.texts and current.title is None:
                    flush()
                if block.media is not None:
                    current.media.append(block.media)
            else:
                current.texts.append(block.raw)
        flush()
        return sections

    def all_media(self) -> list[MediaRef]:
        media = [b.media for b in self.blocks if b.media is not None]
        header = self.header_media
        return ([header] if header else []) + media


# --- social models ---------------------------------------------------------


PostFormat = Literal["single", "reel", "carousel"]


class PostIntent(BaseModel):
    """One planned social media post (before caption generation)."""

    channel: str
    index: int  # order within the campaign for this channel
    kind: Literal["intro", "section"]
    format: PostFormat = "single"  # reel/carousel when a section yields multiple posts
    section_index: int | None = None  # index into BlogPost.sections
    section_title: str | None = None
    media: list[MediaRef] = []
    suggested_date: str = ""  # ISO date suggestion for manual posting


class SocialDraft(BaseModel):
    """LLM-generated caption for one PostIntent."""

    text: str
    hashtags: list[str] = []
    location: str = ""  # Facebook location tag suggestion; empty when unknown
