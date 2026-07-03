"""Hugo front-matter and bundle-format helpers shared by the hugo and translate nodes.

These are edge-format adapters (same spirit as ``siteurl.py``): they encode how
a Hugo leaf bundle represents a post — the per-language index filename, the TOML
string escaping used in the ``+++`` front matter, and how to split a rendered
index file back into its front matter and body. Keeping this knowledge here lets
the translate node consume the bundle artifact the hugo node wrote without
importing the hugo node itself.
"""

from __future__ import annotations

LANGUAGE_FILENAMES = {
    "german": "index.de.md",
    "english": "index.en.md",
    "spanish": "index.es.md",
    "french": "index.fr.md",
    "italian": "index.it.md",
}


def index_filename(language: str) -> str:
    return LANGUAGE_FILENAMES.get(language.strip().lower(), "index.de.md")


def escape_toml(s: str) -> str:
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    s = s.replace("\t", "\\t")
    return s


def split_front_matter(text: str) -> tuple[str, str]:
    """Split a rendered index file into ``(front_matter, body)``.

    ``front_matter`` keeps its surrounding ``+++`` delimiter lines; ``body`` is
    everything after the closing delimiter, with the blank separator removed.
    Text without a leading ``+++`` delimiter yields ``("", text)``.
    """
    if not text.startswith("+++\n"):
        return "", text
    closing = text.find("\n+++", len("+++"))
    if closing == -1:
        return "", text
    fm_end = closing + len("\n+++")
    front = text[:fm_end]
    body = text[fm_end:].lstrip("\n")
    return front, body
