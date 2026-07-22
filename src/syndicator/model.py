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

# Source-language words as written in the Logseq diary — bounded edge knowledge
# (mirrors writer.go getFilename(); unknown/empty language falls back to German).
LANGUAGE_WORD_TO_CODE = {
    "german": "de",
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "italian": "it",
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".wmv", ".flv", ".webm", ".mkv", ".m4v", ".mpg", ".mpeg",
}

# Placeholder inserted into a reel's ``section_text`` at the video's position, so
# the LLM knows where the clip sits in the surrounding prose — the describing
# sentence may be written above *or* below the video.
REEL_VIDEO_MARKER = "[VIDEO]"

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

    def videos(self) -> list[MediaRef]:
        """Content videos in document order (one /reel per video)."""
        return [b.media for b in self.blocks if b.media is not None and b.media.kind == "video"]

    def section_for_block(self, media: MediaRef) -> Section | None:
        """The derived section a given media reference belongs to."""
        for section in self.sections:
            if media in section.media:
                return section
        return None

    def section_text_for_video(
        self, video: MediaRef, marker: str = REEL_VIDEO_MARKER
    ) -> str:
        """Surrounding prose for a video, with ``marker`` at its position.

        Collects the video block together with the run of text blocks directly
        adjacent to it (above *and* below), bounded by any title or other media
        block, and renders them in document order with ``marker`` where the
        video sits. Unlike ``section_for_block(...).texts`` — which only ever
        captures the paragraph *after* the video — this keeps the describing
        sentence whether the author wrote it above or below the clip. The intro
        block is treated as a boundary (it already ships as the post summary).
        """
        blocks = self.blocks
        start = 1 if (blocks and blocks[0].kind == "text") else 0
        idx = next(
            (i for i in range(start, len(blocks)) if blocks[i].media is video), None
        )
        if idx is None:
            return marker
        lo = idx
        while lo - 1 >= start and blocks[lo - 1].kind == "text":
            lo -= 1
        hi = idx
        while hi + 1 < len(blocks) and blocks[hi + 1].kind == "text":
            hi += 1
        parts = [marker if i == idx else blocks[i].raw for i in range(lo, hi + 1)]
        return "\n\n".join(parts)
