"""Tests for the translate node (no network: fake LLM)."""

from pathlib import Path

from syndicator.nodes.extract import scan_blog_posts
from syndicator.nodes.hugo import write_bundle
from syndicator.nodes.translate import (
    disclaimer_for,
    extract_first_paragraph,
    restore_asset_references,
    translate_bundle,
)
from conftest import FakeLLM, make_cfg


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
