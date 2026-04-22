"""Offline parse tests for the Official Code of Georgia Annotated scraper."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_ga.statutes.scrape import (
    OCGASectionRef,
    OCGAStatutesScraper,
    _clean_inline,
    _paragraphs_to_text,
    extract_content_xml,
    extract_sections,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _build_minimal_zip(tmp_path: Path, *, sections: list[tuple[str, str, str]]) -> Path:
    """Build a 1-title OCGA ZIP with the given ``(id, heading, body)`` sections."""
    paras = []
    for sec_id, heading, body in sections:
        paras.append(
            '<text:p><text:span text:style-name="T1">'
            f"{sec_id}. {heading}.<text:line-break/></text:span></text:p>"
            "<text:p>Statute text</text:p>"
            f"<text:p>{body}</text:p>"
            "<text:p>History</text:p>"
        )
    content_xml = '<?xml version="1.0"?><doc>' + "".join(paras) + "</doc>"
    title_odt = io.BytesIO()
    with zipfile.ZipFile(title_odt, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.xml", content_xml)
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("gov.ga.ocga.2019.08.21.r73.title.01.odt", title_odt.getvalue())
    cache_zip = tmp_path / "ocga.zip"
    cache_zip.write_bytes(outer.getvalue())
    return cache_zip


class TestCleanInline:
    def test_text_s_becomes_space(self) -> None:
        assert _clean_inline("a<text:s/>b") == "a b"

    def test_text_s_with_count(self) -> None:
        assert _clean_inline('a<text:s text:c="3"/>b') == "a b"  # whitespace collapses

    def test_line_break_becomes_space(self) -> None:
        assert _clean_inline("a<text:line-break/>b") == "a b"

    def test_tab_becomes_space(self) -> None:
        assert _clean_inline("a<text:tab/>b") == "a b"

    def test_entities_decoded(self) -> None:
        assert _clean_inline("a &amp; b") == "a & b"

    def test_span_wrapping_stripped(self) -> None:
        assert _clean_inline('<text:span text:style-name="T2">Hi</text:span>') == "Hi"

    def test_whitespace_collapsed(self) -> None:
        assert _clean_inline("  many   \n  spaces  ") == "many spaces"


class TestParagraphsToText:
    def test_single_paragraph(self) -> None:
        assert _paragraphs_to_text("<text:p>Hello</text:p>") == "Hello"

    def test_multiple_paragraphs_joined_by_blank_line(self) -> None:
        xml = "<text:p>One</text:p><text:p>Two</text:p>"
        assert _paragraphs_to_text(xml) == "One\n\nTwo"

    def test_empty_paragraphs_dropped(self) -> None:
        xml = "<text:p>One</text:p><text:p></text:p><text:p>Two</text:p>"
        assert _paragraphs_to_text(xml) == "One\n\nTwo"


class TestExtractSections:
    def test_real_title1_fixture_yields_two_sections(self) -> None:
        sections = list(extract_sections(_fixture("title1_sections_1-1-1_1-1-2.xml")))
        ids = [s[0] for s in sections]
        assert ids == ["1-1-1", "1-1-2"]

    def test_real_title1_heading_stripped(self) -> None:
        sections = dict(
            (sid, (h, b))
            for sid, h, b in extract_sections(_fixture("title1_sections_1-1-1_1-1-2.xml"))
        )
        heading, _body = sections["1-1-1"]
        # Trailing period is stripped.
        assert heading == "Enactment of Code"

    def test_real_title1_body_starts_with_statute_text(self) -> None:
        sections = dict(
            (sid, (h, b))
            for sid, h, b in extract_sections(_fixture("title1_sections_1-1-1_1-1-2.xml"))
        )
        _, body = sections["1-1-1"]
        assert body.startswith("The statutory portion of the codification")

    def test_real_title1_body_excludes_annotations(self) -> None:
        sections = dict(
            (sid, (h, b))
            for sid, h, b in extract_sections(_fixture("title1_sections_1-1-1_1-1-2.xml"))
        )
        _, body = sections["1-1-2"]
        # Annotations + case notes + research refs must NOT leak into body.
        assert "JUDICIAL DECISIONS" not in body
        assert "Annotations" not in body
        assert "Cross references." not in body
        assert "Law reviews." not in body

    def test_decimal_id_section(self) -> None:
        sections = list(extract_sections(_fixture("title48_section_48-2-6.1.xml")))
        assert len(sections) == 1
        sid, heading, body = sections[0]
        assert sid == "48-2-6.1"
        assert heading.startswith("Disclosure of return information")
        assert '"return information"' in body

    def test_empty_doc_yields_nothing(self) -> None:
        assert list(extract_sections("<xml></xml>")) == []

    def test_synthetic_minimal_section(self) -> None:
        xml = (
            "<doc>"
            '<text:p><text:span text:style-name="T1">'
            "1-1-1. Heading here.<text:line-break/></text:span></text:p>"
            "<text:p>Statute text</text:p>"
            "<text:p>(a) Body paragraph one.</text:p>"
            "<text:p>(b) Body paragraph two.</text:p>"
            "<text:p>History</text:p>"
            "<text:p>(Code 1981; Ga. L. 1990, p. 1.)</text:p>"
            "</doc>"
        )
        sections = list(extract_sections(xml))
        assert len(sections) == 1
        sid, heading, body = sections[0]
        assert sid == "1-1-1"
        assert heading == "Heading here"
        assert "(a) Body paragraph one." in body
        assert "(b) Body paragraph two." in body
        # History provenance must not leak into the body.
        assert "Code 1981" not in body

    def test_section_without_statute_text_label_skipped(self) -> None:
        xml = (
            "<doc>"
            '<text:p><text:span text:style-name="T1">'
            "1-1-1. Heading.<text:line-break/></text:span></text:p>"
            "<text:p>(no label)</text:p>"
            "</doc>"
        )
        assert list(extract_sections(xml)) == []


class TestExtractContentXml:
    def test_reads_content_xml_from_odt_archive(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("content.xml", "<xml>hi</xml>")
        assert extract_content_xml(buf.getvalue()) == "<xml>hi</xml>"

    def test_missing_content_xml_returns_empty(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("other.xml", "<xml/>")
        assert extract_content_xml(buf.getvalue()) == ""


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = OCGAStatutesScraper(generation_date=date(2026, 4, 21))
        assert scraper.jurisdiction == "us-ga"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "O.C.G.A."
        assert scraper.author_id == "ga-code-revision-commission"

    def test_section_ref_frozen(self) -> None:
        ref = OCGASectionRef(title=1, section_id="1-1-10")
        assert ref.title == 1
        assert ref.section_id == "1-1-10"

    def test_parse_section_pulls_from_cache(self) -> None:
        scraper = OCGAStatutesScraper(generation_date=date(2026, 4, 21))
        scraper._cache["1-1-1"] = (1, "Heading", "Body paragraph.")
        section = scraper.parse_section(OCGASectionRef(title=1, section_id="1-1-1"))
        assert section is not None
        assert section.work_number == "1-1-1"
        assert section.citation == "O.C.G.A. \u00a7 1-1-1"
        assert section.heading == "Heading"
        assert section.body == "Body paragraph."

    def test_parse_section_returns_none_when_not_in_cache(self) -> None:
        scraper = OCGAStatutesScraper(generation_date=date(2026, 4, 21))
        assert scraper.parse_section(OCGASectionRef(title=1, section_id="9-9-9")) is None

    def test_parse_section_returns_none_on_empty_body(self) -> None:
        scraper = OCGAStatutesScraper(generation_date=date(2026, 4, 21))
        scraper._cache["1-1-1"] = (1, "Heading", "")
        assert scraper.parse_section(OCGASectionRef(title=1, section_id="1-1-1")) is None

    def test_env_var_overrides_cache_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        zip_path = tmp_path / "from-env.zip"
        monkeypatch.setenv("AXIOM_GA_OCGA_ZIP", str(zip_path))
        scraper = OCGAStatutesScraper(generation_date=date(2026, 4, 21))
        assert scraper._cache_path == zip_path

    def test_explicit_cache_path_beats_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_path = tmp_path / "env.zip"
        explicit_path = tmp_path / "explicit.zip"
        monkeypatch.setenv("AXIOM_GA_OCGA_ZIP", str(env_path))
        scraper = OCGAStatutesScraper(generation_date=date(2026, 4, 21), cache_path=explicit_path)
        assert scraper._cache_path == explicit_path

    def test_populate_cache_is_idempotent(self, tmp_path: Path) -> None:
        cache_zip = _build_minimal_zip(tmp_path, sections=[("1-1-1", "Heading", "Body")])
        scraper = OCGAStatutesScraper(generation_date=date(2026, 4, 21), cache_path=cache_zip)
        scraper._titles = (1,)
        list(scraper.list_sections())
        cache_zip.unlink()  # second call must not try to re-read the file
        list(scraper.list_sections())
        assert len(scraper._cache) == 1

    def test_populate_cache_skips_missing_titles(self, tmp_path: Path) -> None:
        # ZIP only has title 1; asking for titles (1, 99) shouldn't crash.
        cache_zip = _build_minimal_zip(tmp_path, sections=[("1-1-1", "H", "Body")])
        scraper = OCGAStatutesScraper(generation_date=date(2026, 4, 21), cache_path=cache_zip)
        scraper._titles = (1, 99)
        refs = list(scraper.list_sections())
        assert [r.section_id for r in refs] == ["1-1-1"]

    def test_populate_cache_uses_cached_zip_path(self, tmp_path: Path) -> None:
        # Build a tiny OCGA ZIP containing one title ODT with one section.
        title_odt = io.BytesIO()
        with zipfile.ZipFile(title_odt, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "content.xml",
                '<?xml version="1.0"?><doc>'
                '<text:p><text:span text:style-name="T1">'
                "1-1-1. Sample heading.<text:line-break/></text:span></text:p>"
                "<text:p>Statute text</text:p>"
                "<text:p>Sample body paragraph.</text:p>"
                "<text:p>History</text:p>"
                "</doc>",
            )
        title_odt_bytes = title_odt.getvalue()

        outer = io.BytesIO()
        with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("gov.ga.ocga.2019.08.21.r73.title.01.odt", title_odt_bytes)
        cache_zip = tmp_path / "ocga.zip"
        cache_zip.write_bytes(outer.getvalue())

        scraper = OCGAStatutesScraper(generation_date=date(2026, 4, 21), cache_path=cache_zip)
        scraper._titles = (1,)
        refs = list(scraper.list_sections())
        assert len(refs) == 1
        assert refs[0] == OCGASectionRef(title=1, section_id="1-1-1")
        section = scraper.parse_section(refs[0])
        assert section is not None
        assert section.body == "Sample body paragraph."


class TestOutputPath:
    @staticmethod
    def _section(work_number: str) -> Section:
        return Section(
            jurisdiction="us-ga",
            doc_type="statute",
            authority_code="O.C.G.A.",
            work_number=work_number,
            citation=f"O.C.G.A. \u00a7 {work_number}",
            heading="H",
            body="B",
            author_id="ga-code-revision-commission",
            author_name="Georgia Code Revision Commission",
            author_url="https://law.resource.org/pub/us/code/ga/",
            generation_date=date(2026, 4, 21),
        )

    def test_nests_by_title(self) -> None:
        scraper = OCGAStatutesScraper(generation_date=date(2026, 4, 21))
        rel = scraper.relative_output_path(self._section("1-1-10"))
        assert rel == Path("us-ga/statutes/title-1/title-1-sec-1-1-10.xml")

    def test_decimal_id(self) -> None:
        scraper = OCGAStatutesScraper(generation_date=date(2026, 4, 21))
        rel = scraper.relative_output_path(self._section("48-7-29.25"))
        assert rel == Path("us-ga/statutes/title-48/title-48-sec-48-7-29.25.xml")
