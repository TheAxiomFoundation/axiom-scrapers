"""Tests for text normalization helpers."""

import pytest

from axiom_scrapers._common.text import clean_text, safe_path_segment, split_paragraphs


class TestCleanText:
    def test_empty_input_returns_empty(self) -> None:
        assert clean_text("") == ""
        assert clean_text("   ") == ""

    def test_plain_text_is_unchanged(self) -> None:
        assert clean_text("Hello world.") == "Hello world."

    def test_strips_tags(self) -> None:
        assert clean_text("<b>Hello</b> <i>world</i>") == "Hello world"

    def test_br_becomes_newline(self) -> None:
        assert clean_text("Line 1<br>Line 2") == "Line 1\nLine 2"
        assert clean_text("Line 1<br/>Line 2") == "Line 1\nLine 2"
        assert clean_text("Line 1<BR />Line 2") == "Line 1\nLine 2"

    def test_block_close_tags_become_newlines(self) -> None:
        assert clean_text("<p>A</p><p>B</p>") == "A\nB"
        assert clean_text("<div>A</div><div>B</div>") == "A\nB"
        assert clean_text("<li>A</li><li>B</li>") == "A\nB"

    def test_entity_decoded(self) -> None:
        assert clean_text("&sect; 1") == "§ 1"
        assert clean_text("A &amp; B") == "A & B"
        assert clean_text("&#167; 5") == "§ 5"

    def test_nbsp_becomes_space(self) -> None:
        assert clean_text("A\xa0B") == "A B"
        assert clean_text("A&nbsp;B") == "A B"

    def test_collapses_runs_of_spaces(self) -> None:
        assert clean_text("A     B\t\t\tC") == "A B C"

    def test_collapses_runs_of_newlines(self) -> None:
        assert clean_text("A\n\n\n\nB") == "A\nB"

    def test_trims_leading_trailing_whitespace(self) -> None:
        assert clean_text("  Hello  ") == "Hello"
        assert clean_text("\n\nHello\n\n") == "Hello"

    def test_preserves_single_newlines(self) -> None:
        """Keeps paragraph boundaries from <br> intact."""
        assert clean_text("A<br><br>B") == "A\nB"  # collapsed to single \n

    def test_handles_complex_nested_markup(self) -> None:
        html = """
        <p><span class="font">(a)&nbsp;&nbsp;A person commits</span></p>
        <p><span>an attempt when, with intent.</span></p>
        """
        out = clean_text(html)
        assert "(a) A person commits" in out
        assert "an attempt when, with intent." in out
        # Two paragraphs separated by newline(s).
        assert "\n" in out


class TestSplitParagraphs:
    def test_empty_returns_empty_list(self) -> None:
        assert split_paragraphs("") == []

    def test_single_paragraph(self) -> None:
        assert split_paragraphs("Hello.") == ["Hello."]

    def test_double_newline_splits(self) -> None:
        assert split_paragraphs("First.\n\nSecond.") == ["First.", "Second."]

    def test_triple_newline_splits_once(self) -> None:
        assert split_paragraphs("A.\n\n\nB.") == ["A.", "B."]

    def test_ignores_single_newline(self) -> None:
        # Single newline = line wrap, not paragraph break.
        assert split_paragraphs("A\nB") == ["A\nB"]

    def test_drops_empty_paragraphs(self) -> None:
        assert split_paragraphs("\n\nA.\n\n\n\nB.\n\n") == ["A.", "B."]


class TestSafePathSegment:
    def test_plain_string_unchanged(self) -> None:
        assert safe_path_segment("17-0105") == "17-0105"

    def test_slashes_replaced_with_underscore(self) -> None:
        assert safe_path_segment("28:9/316") == "28:9_316"

    def test_colons_preserved(self) -> None:
        """DC UCC section numbers use colons; must not be stripped."""
        assert safe_path_segment("28:9-316") == "28:9-316"

    def test_dots_preserved(self) -> None:
        """NV/IL use dots in section numbers."""
        assert safe_path_segment("244.010") == "244.010"
        assert safe_path_segment("35-155-2.1") == "35-155-2.1"

    def test_trims_whitespace(self) -> None:
        assert safe_path_segment("  1.010  ") == "1.010"


@pytest.mark.parametrize(
    "dirty,clean",
    [
        ("<p>A</p>", "A"),
        ("<span>&sect; 1</span>", "§ 1"),
        ("A\u00a0B", "A B"),
        ("&amp;", "&"),
        ("", ""),
    ],
)
def test_clean_text_parametrized(dirty: str, clean: str) -> None:
    assert clean_text(dirty) == clean
