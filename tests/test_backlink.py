"""Tests for the syndication:: backlink insertion into blog sources."""

from pathlib import Path

from syndicator.nodes.backlink import ensure_syndication_link
from syndicator.nodes.extract import extract_posts, source_hash

from conftest import make_cfg


def _online_post(path: Path):
    return [p for p in extract_posts(path) if p.meta.status == "online"][0]


def test_journal_insert_is_idempotent_and_hash_stable(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    journal = cfg.journals_dir / "2026_04_08.md"
    post = _online_post(journal)
    h = source_hash(post)

    assert ensure_syndication_link(post) is True
    text = journal.read_text(encoding="utf-8")
    assert "\t  syndication:: [[syndicator/2026-04-08_Segeln]]" in text
    # Inserted into the property block, before the first content bullet.
    prop_pos = text.index("syndication::")
    content_pos = text.index("Eigentlich sind wir ja zum segeln")
    assert prop_pos < content_pos
    # The private branch above the blog stays untouched.
    assert text.count("syndication::") == 1

    reparsed = _online_post(journal)
    assert source_hash(reparsed) == h  # property must not change the hash
    assert ensure_syndication_link(reparsed) is False  # idempotent
    assert journal.read_text(encoding="utf-8") == text


def test_page_insert_is_idempotent_and_hash_stable(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    page = cfg.pages_dir / "Renan.md"
    post = extract_posts(page)[0]
    h = source_hash(post)

    assert ensure_syndication_link(post) is True
    text = page.read_text(encoding="utf-8")
    assert "\nsyndication:: [[syndicator/2024-06-14_Renan]]\n" in text
    # Column-0 property, inside the leading property lines.
    lines = text.splitlines()
    idx = lines.index("syndication:: [[syndicator/2024-06-14_Renan]]")
    assert all("::" in line for line in lines[:idx] if line.strip())

    reparsed = extract_posts(page)[0]
    assert source_hash(reparsed) == h
    assert ensure_syndication_link(reparsed) is False
    assert page.read_text(encoding="utf-8") == text


def test_existing_link_is_updated_not_duplicated(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    journal = cfg.journals_dir / "2026_04_08.md"
    post = _online_post(journal)
    assert ensure_syndication_link(post) is True

    # Simulate a stale link value, e.g. after a title change.
    text = journal.read_text(encoding="utf-8").replace(
        "syndication:: [[syndicator/2026-04-08_Segeln]]",
        "syndication:: [[syndicator/old-slug]]",
    )
    journal.write_text(text, encoding="utf-8")

    post = _online_post(journal)
    assert ensure_syndication_link(post) is True
    updated = journal.read_text(encoding="utf-8")
    assert updated.count("syndication::") == 1
    assert "syndication:: [[syndicator/2026-04-08_Segeln]]" in updated
