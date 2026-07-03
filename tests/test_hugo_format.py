"""Tests for the shared Hugo front-matter/bundle-format helpers."""

from syndicator.hugo_format import escape_toml, index_filename, split_front_matter


def test_index_filename_maps_languages():
    assert index_filename("german") == "index.de.md"
    assert index_filename("English") == "index.en.md"
    assert index_filename(" spanish ") == "index.es.md"
    assert index_filename("klingon") == "index.de.md"


def test_escape_toml_escapes_specials():
    assert escape_toml('a"b') == 'a\\"b'
    assert escape_toml("a\\b") == "a\\\\b"
    assert escape_toml("a\nb") == "a\\nb"


def test_split_front_matter_separates_body():
    text = '+++\ntitle = "x"\n+++\n\nbody line one\n\nbody line two\n'
    front, body = split_front_matter(text)
    assert front == '+++\ntitle = "x"\n+++'
    assert body == "body line one\n\nbody line two\n"


def test_split_front_matter_without_delimiter():
    assert split_front_matter("just body") == ("", "just body")
