"""Golden tests for the extract node, based on real (public) blog branches."""

from pathlib import Path

from syndicator.nodes.extract import extract_posts, scan_blog_posts, source_hash

FIXTURES = Path(__file__).parent / "fixtures"
JOURNALS = FIXTURES / "journals"
PAGES = FIXTURES / "pages"


def post_by_slug(slug: str):
    posts = scan_blog_posts(JOURNALS, PAGES)
    by_slug = {p.slug: p for p in posts}
    return by_slug[slug]


def test_scan_finds_all_online_posts_and_skips_drafts():
    posts = scan_blog_posts(JOURNALS, PAGES)
    slugs = {p.slug for p in posts}
    assert slugs == {
        "2026-06-10_Griechenland_❤️",
        "2026-05-19_Charly_Superstar",
        "2026-06-03_Athen",
        "2026-05-28_Lefkada",
        "2026-01-17_Frühlingspläne_2026",
        "2026-04-08_Segeln",
        "2024-06-14_Renan",
    }


def test_draft_template_is_parsed_but_not_online():
    posts = extract_posts(JOURNALS / "2026_04_06_template.md")
    assert len(posts) == 1
    assert posts[0].meta.status == "draft"


def test_branch_isolation_no_noise_leaks():
    posts = scan_blog_posts(JOURNALS, PAGES)
    for post in posts:
        for block in post.blocks:
            assert "Synthetic private entry" not in block.raw
            assert "current-position" not in block.raw


def test_griechenland_sections():
    post = post_by_slug("2026-06-10_Griechenland_❤️")
    assert post.meta.title == "Griechenland ❤️"
    assert post.lang_code == "de"
    assert post.intro.startswith("Nach fast einem Monat")

    sections = post.sections
    assert [s.title for s in sections] == ["Gastfreundschaft", "Wirtschaftskrise", "Herbstpläne"]
    assert [len(s.media) for s in sections] == [5, 1, 4]
    assert all(len(s.texts) == 1 for s in sections)
    # Mixed media kinds within a section.
    kinds = {m.kind for m in sections[0].media}
    assert kinds == {"image", "video"}


def test_charly_sections_without_titles():
    post = post_by_slug("2026-05-19_Charly_Superstar")
    sections = post.sections
    assert all(s.title is None for s in sections)
    assert len(sections) == 4
    # First section: one video, one text.
    assert sections[0].media[0].kind == "video"
    assert len(sections[0].texts) == 1
    # Third section: six images.
    assert [m.kind for m in sections[2].media] == ["image"] * 6


def test_athen_youtube_block():
    post = post_by_slug("2026-06-03_Athen")
    yt = [m for m in post.all_media() if m.kind == "youtube"]
    assert len(yt) == 1
    assert yt[0].youtube_id == "FAIZtHHsbSM"
    assert yt[0].url.startswith("https://youtu.be/")


def test_lefkada_meta_and_media():
    post = post_by_slug("2026-05-28_Lefkada")
    # date:: differs from the journal filename date (2026_05_25.md).
    assert post.meta.date == "2026-05-28"
    # Image attributes like {:height 274, :width 473} must not leak into filenames.
    for m in post.all_media():
        if m.kind != "youtube":
            assert "{" not in m.filename
            assert "/" not in m.filename


def test_segeln_keeps_id_property_in_raw_but_classifies_media():
    post = post_by_slug("2026-04-08_Segeln")
    blocks_with_id = [b for b in post.blocks if "id::" in b.raw]
    assert blocks_with_id, "expected a block with id:: continuation"
    assert blocks_with_id[0].kind == "media"


def test_fruehlingsplaene_table_preserved():
    post = post_by_slug("2026-01-17_Frühlingspläne_2026")
    tables = [b for b in post.blocks if "| **Bezeichnung**" in b.raw]
    assert len(tables) == 1
    assert tables[0].kind == "text"
    # Table continuation rows are dedented to column 0.
    assert "\n| ---" in tables[0].raw


def test_renan_page_format():
    post = post_by_slug("2024-06-14_Renan")
    assert post.lang_code == "en"
    assert post.meta.date == "2024-06-14"  # trailing space in source must be trimmed
    assert post.meta.header == "../assets/Renan/renand.jpg"
    assert post.intro.startswith("My dream is to embark")
    titles = [s.title for s in post.sections if s.title]
    assert "The Idea" in titles
    # Nested bullets are flattened to "* ..." plain text lines.
    lessons = [b for b in post.blocks if "valuable lessons" in b.raw]
    assert lessons and "\n* We can definitely imagine" in lessons[0].raw


def test_header_media_resolution():
    post = post_by_slug("2026-06-10_Griechenland_❤️")
    header = post.header_media
    assert header is not None
    assert header.filename == "alex-lachen_1781168210193_0.jpg"


def test_source_hash_stability_and_sensitivity():
    a = post_by_slug("2026-05-19_Charly_Superstar")
    b = post_by_slug("2026-05-19_Charly_Superstar")
    assert source_hash(a) == source_hash(b)
    b.blocks[0].raw += " geändert"
    assert source_hash(a) != source_hash(b)
