"""Tests for the translate node (no network: fake LLM)."""

from pathlib import Path

from syndicator.hugo_format import index_filename
from syndicator.nodes.extract import scan_blog_posts
from syndicator.nodes.hugo import write_bundle
from syndicator.nodes.translate import (
    disclaimer_for,
    extract_first_paragraph,
    restore_asset_references,
    translate_bundle,
)
from conftest import FakeLLM, create_dummy_assets, make_cfg


def test_restore_asset_references_positional():
    source = 'Intro ![Alt](a_1.jpg) mehr {{< video src="clip_2.mp4" >}} und ![B](b_3.png)'
    translated = 'Intro ![Translated alt](WRONG.jpg) more {{< video src="WRONG.mp4" >}} and ![B trans](ALSO_WRONG.png)'
    restored = restore_asset_references(source, translated)
    assert "![Translated alt](a_1.jpg)" in restored
    assert '{{< video src="clip_2.mp4" >}}' in restored
    assert "![B trans](b_3.png)" in restored


def test_restore_handles_extra_images_gracefully():
    source = "![a](one.jpg)"
    translated = "![a](one_x.jpg) ![hallucinated](two.jpg)"
    restored = restore_asset_references(source, translated)
    assert "![a](one.jpg)" in restored
    assert "![hallucinated](two.jpg)" in restored  # unchanged, no source path left


def test_extract_first_paragraph():
    content = "\n\nFirst line\ncontinued line\n\nSecond para"
    assert extract_first_paragraph(content) == "First line continued line"
    assert extract_first_paragraph("### Heading\nText") == ""
    assert extract_first_paragraph("---\nText") == ""


def test_disclaimers_exist_for_all_languages():
    for lang in ("en", "de", "es", "fr", "it", "arrr"):
        assert disclaimer_for(lang).startswith("---")


def test_translations_reference_adapted_video_filenames(tmp_path: Path):
    """Translated pages must reference the adapted video (clip.mp4) that the
    hugo bundle actually contains, not the source filename (clip.mov)."""
    cfg = make_cfg(tmp_path)
    journal = cfg.journals_dir / "2026_07_01.md"
    journal.write_text(
        "- [[Blog]]\n"
        "\t- type:: blog\n"
        "\t  status:: online\n"
        "\t  language:: german\n"
        "\t  date:: 2026-07-01\n"
        "\t  title:: Clip Post\n"
        "\t  author:: Benno\n"
        "\t- Ein kurzer Einleitungstext fuer den Blogbeitrag.\n"
        "\t- ![clip](../assets/clip.mov)\n"
        "\t- Noch ein Absatz nach dem Video.\n",
        encoding="utf-8",
    )

    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2026-07-01_Clip_Post"]
    create_dummy_assets([post])

    bundle = write_bundle(post, cfg.hugo_posts_dir, cfg, FakeLLM())
    assert (bundle / "clip.mp4").exists()  # hugo adapted (fell back to raw copy)

    translate_bundle(post, cfg, FakeLLM(), bundle)

    source_index = index_filename(post.meta.language)
    for index in bundle.glob("index.*.md"):
        if index.name == source_index:
            continue
        text = index.read_text(encoding="utf-8")
        assert "clip.mp4" in text, f"{index.name} lost the adapted video reference"
        assert "clip.mov" not in text, f"{index.name} still references clip.mov"


def test_translate_bundle_writes_files(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    post = posts["2026-05-19_Charly_Superstar"]  # German source
    bundle = write_bundle(post, cfg.hugo_posts_dir, cfg, FakeLLM())

    llm = FakeLLM()
    langs = translate_bundle(post, cfg, llm, bundle)
    assert sorted(langs) == ["arrr", "en", "es", "fr", "it"]

    en = (bundle / "index.en.md").read_text(encoding="utf-8")
    assert en.startswith("+++\n")
    assert 'title = "[translate_en] Charly Superstar"' in en
    assert disclaimer_for("en") in en
    # Asset references restored to the real filenames.
    assert "{{< video src=" in en or "![" in en

    # Pirate speak keeps the original title.
    arrr = (bundle / "index.arrr.md").read_text(encoding="utf-8")
    assert 'title = "Charly Superstar"' in arrr
    # Pirate is derived from the English translation, not the German source.
    assert "[translate_arrr] [translate_en]" in arrr

    # 5 body translations + 4 title translations (no pirate title).
    assert llm.calls == 9

    # Source change: translate_bundle always retranslates when called.
    post.blocks[0].raw += " neu"
    llm3 = FakeLLM()
    assert len(translate_bundle(post, cfg, llm3, bundle)) == 5


def test_translate_bundle_english_source_targets(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    posts = {p.slug: p for p in scan_blog_posts(cfg.journals_dir, cfg.pages_dir)}
    renan = posts["2024-06-14_Renan"]
    bundle = write_bundle(renan, cfg.hugo_posts_dir, cfg, FakeLLM())

    langs = translate_bundle(renan, cfg, FakeLLM(), bundle)
    assert sorted(langs) == ["arrr", "de", "es", "fr", "it"]
    assert (bundle / "index.de.md").exists()
    assert not (bundle / "index.en.md").read_text(encoding="utf-8").startswith("[translate")
