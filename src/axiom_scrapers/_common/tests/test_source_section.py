"""Tests for normalized source-section artifacts."""

from datetime import date

from axiom_scrapers._common.source_section import (
    SourceSection,
    render_source_metadata_yaml,
    render_source_text,
    strip_invalid_control_chars,
)


def make_section(**overrides: object) -> SourceSection:
    defaults: dict[str, object] = {
        "jurisdiction": "us-il",
        "doc_type": "statute",
        "authority_code": "ILCS",
        "work_number": "35-155-2",
        "citation": "35 ILCS 155/2",
        "heading": "Definitions",
        "body": "As used in this Act:\n\n\"Renting\" means any transfer.",
        "author_id": "il-legislature",
        "author_name": "Illinois General Assembly",
        "author_url": "https://www.ilga.gov",
        "generation_date": date(2026, 4, 20),
    }
    defaults.update(overrides)
    return SourceSection(**defaults)  # type: ignore[arg-type]


class TestRenderSourceText:
    def test_body_splits_on_blank_lines(self) -> None:
        body = "Para one.\n\nPara two.\n\nPara three."
        assert render_source_text(make_section(body=body)) == (
            "Para one.\n\nPara two.\n\nPara three.\n"
        )

    def test_empty_body_is_empty_text(self) -> None:
        assert render_source_text(make_section(body="")) == ""

    def test_invalid_control_chars_stripped_from_body(self) -> None:
        text = render_source_text(
            make_section(body="Before\x00 NULL\x0b vert-tab\x0c form-feed\x1f us.")
        )
        assert "\x00" not in text
        assert "\x0b" not in text
        assert "\x0c" not in text
        assert "\x1f" not in text
        assert "NULL" in text


class TestRenderSourceMetadataYaml:
    def test_metadata_contains_core_fields(self) -> None:
        meta = render_source_metadata_yaml(
            make_section(work_number="9A-33-5", citation='R.C. "1.01"'),
            text_path="9A-33-5.txt",
        )

        assert "format: axiom-source-section/v1" in meta
        assert 'jurisdiction: "us-il"' in meta
        assert 'doc_type: "statute"' in meta
        assert 'authority_code: "ILCS"' in meta
        assert 'work_number: "9A-33-5"' in meta
        assert 'citation: "R.C. \\"1.01\\""' in meta
        assert 'generation_date: "2026-04-20"' in meta
        assert 'author_id: "il-legislature"' in meta
        assert 'text_path: "9A-33-5.txt"' in meta

    def test_invalid_control_chars_stripped_from_metadata(self) -> None:
        meta = render_source_metadata_yaml(
            make_section(heading="Heading\x00 with null"),
            text_path="section.txt",
        )
        assert "\x00" not in meta
        assert 'heading: "Heading with null"' in meta


def test_strip_invalid_control_chars() -> None:
    assert strip_invalid_control_chars("a\x00b\x0bc\x1fd") == "abcd"
