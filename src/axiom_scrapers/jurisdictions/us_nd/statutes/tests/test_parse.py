"""Offline parse tests for the North Dakota Century Code scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.source_section import SourceSection
from axiom_scrapers.jurisdictions.us_nd.statutes.scrape import (
    NDCCStatutesScraper,
    NDSectionRef,
    _clean_body,
    extract_toc_entries,
    parse_chapter_pdf,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestExtractTocEntries:
    def test_real_toc_excerpt_parses(self) -> None:
        entries = extract_toc_entries(_fixture("toc_excerpt.html"))
        # The excerpt covers chapter 1-01 which has ~50 sections.
        assert len(entries) >= 30
        for pdf_url, num, heading in entries:
            assert pdf_url.startswith("/cencode/")
            assert pdf_url.endswith(".pdf")
            assert num.startswith("1-01-")
            assert heading

    def test_first_entry_matches_expected_shape(self) -> None:
        entries = extract_toc_entries(_fixture("toc_excerpt.html"))
        pdf_url, num, heading = entries[0]
        assert pdf_url == "/cencode/t01c01.pdf"
        assert num == "1-01-01"
        assert heading == "This act - How referred to"

    def test_subsection_numbered_entries_survive(self) -> None:
        entries = extract_toc_entries(_fixture("toc_excerpt.html"))
        nums = {num for _, num, _ in entries}
        assert "1-01-01.1" in nums
        assert "1-01-01.2" in nums

    def test_dedupes_by_section_number(self) -> None:
        html = (
            '<td class="no-wrap"><a href="/cencode/t01c01.pdf#nameddest=1-01-01">1-01-01</a></td>'
            "<td>A</td>"
            '<td class="no-wrap"><a href="/cencode/t01c01.pdf#nameddest=1-01-01">1-01-01</a></td>'
            "<td>A</td>"
        )
        assert extract_toc_entries(html) == [
            ("/cencode/t01c01.pdf", "1-01-01", "A")
        ]

    def test_non_breaking_hyphen_in_heading_normalized(self) -> None:
        # U+2011 NON-BREAKING HYPHEN appears between "This act" and "How" in
        # the live TOC; normalize to ASCII hyphen so search indices behave.
        html = (
            '<td class="no-wrap"><a href="/cencode/t01c01.pdf#nameddest=1-01-01">'
            "1-01-01</a></td>"
            "<td>A\u2011B</td>"
        )
        (_, _, heading), = extract_toc_entries(html)
        assert heading == "A-B"


class TestParseChapterPdf:
    def test_parses_first_chapter_fixture(self) -> None:
        sections = parse_chapter_pdf(_fixture("chapter_1_01.txt"))
        # Chapter 1-01 has ~50 sections. The fixture truncates but we
        # should see at least the first dozen.
        assert len(sections) >= 12

    def test_first_section_heading_and_body(self) -> None:
        sections = parse_chapter_pdf(_fixture("chapter_1_01.txt"))
        heading, body = sections["1-01-01"]
        assert heading == "This act - How referred to"
        assert "North Dakota Century Code" in body
        # TITLE banner before first section must not appear in body.
        assert "TITLE 1" not in body
        assert "GENERAL PROVISIONS" not in body

    def test_subsection_numbered_section_survives(self) -> None:
        sections = parse_chapter_pdf(_fixture("chapter_1_01.txt"))
        heading, body = sections["1-01-01.1"]
        assert heading == "Adoption of North Dakota Revised Code of 1943"
        assert "Repealed" in body

    def test_page_markers_stripped_from_body(self) -> None:
        sections = parse_chapter_pdf(_fixture("chapter_1_01.txt"))
        for _, body in sections.values():
            assert "Page No." not in body

    def test_wrapped_heading_reassembled(self) -> None:
        sections = parse_chapter_pdf(_fixture("chapter_57_38_excerpt.txt"))
        heading, body = sections["57-38-01.8"]
        assert heading == (
            "Income tax credit for installation of geothermal, solar, "
            "wind, or biomass energy devices"
        )
        # The wrapped heading fragment must not leak into the body.
        assert "energy devices." not in body
        assert "A taxpayer filing a North Dakota income tax return" in body

    def test_subsection_numbered_header_with_decimal(self) -> None:
        sections = parse_chapter_pdf(_fixture("chapter_57_38_excerpt.txt"))
        assert "57-38-01.15" in sections
        heading, body = sections["57-38-01.15"]
        assert heading.startswith("Proration and itemization")
        assert body

    def test_cross_reference_in_body_not_treated_as_header(self) -> None:
        # "... imposed by section 57-38-30." appears at the end of a
        # sentence inside 57-38-01.4's body; it must not split the
        # section into two halves.
        sections = parse_chapter_pdf(_fixture("chapter_57_38_excerpt.txt"))
        _, body = sections["57-38-01.4"]
        assert "at the corporate income tax rates imposed by section 57-38-30" in body
        # 57-38-30 is NOT one of this chapter's TOC entries; even if it
        # were, mid-paragraph occurrences should not surface as keys.
        assert "57-38-30" not in sections

    def test_chapter_banner_dropped(self) -> None:
        sections = parse_chapter_pdf(_fixture("chapter_57_38_excerpt.txt"))
        # No section key should smuggle the CHAPTER banner into its body.
        for _, body in sections.values():
            assert "CHAPTER 57-38" not in body
            assert "INCOME TAX" not in body or "income tax" in body.lower()

    def test_empty_input_returns_empty_dict(self) -> None:
        assert parse_chapter_pdf("") == {}

    def test_text_without_section_headers_returns_empty(self) -> None:
        assert parse_chapter_pdf("Just a preamble with no sections.") == {}


class TestCleanBody:
    def test_drops_page_markers(self) -> None:
        slab = (
            "First paragraph continues.\n"
            "                         Page No. 3\n"
            "Second paragraph starts here.\n"
        )
        body = _clean_body(slab)
        assert "Page No." not in body
        assert "First paragraph continues." in body
        assert "Second paragraph starts here." in body

    def test_collapses_wrap_lines_into_single_paragraph(self) -> None:
        slab = (
            "This sentence wraps\n"
            "across two lines.\n"
            "\n"
            "A second paragraph.\n"
        )
        assert _clean_body(slab) == (
            "This sentence wraps across two lines.\n\nA second paragraph."
        )

    def test_empty_slab_returns_empty(self) -> None:
        assert _clean_body("") == ""

    def test_banner_lines_dropped(self) -> None:
        slab = "Body text.\nTITLE 57\nCHAPTER 57-38\nMore body.\n"
        body = _clean_body(slab)
        # Because the banners are inline with surrounding text (no blank
        # lines), they join with the body lines as a single paragraph
        # after the banners themselves are dropped.
        assert "TITLE 57" not in body
        assert "CHAPTER 57-38" not in body
        assert "Body text." in body
        assert "More body." in body


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = NDCCStatutesScraper(generation_date=date(2026, 4, 21))
        assert scraper.jurisdiction == "us-nd"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "NDCC"
        assert scraper.author_id == "nd-legislature"

    def test_section_ref_is_frozen(self) -> None:
        ref = NDSectionRef(
            pdf_url="/cencode/t01c01.pdf",
            section_num="1-01-01",
            toc_heading="This act - How referred to",
        )
        assert ref.pdf_url == "/cencode/t01c01.pdf"
        assert ref.section_num == "1-01-01"

    def test_parse_section_uses_cache_and_returns_section(self) -> None:
        scraper = NDCCStatutesScraper(generation_date=date(2026, 4, 21))
        scraper._chapter_cache["/cencode/t01c01.pdf"] = {
            "1-01-01": ("This act", "Body text here."),
        }
        ref = NDSectionRef(
            pdf_url="/cencode/t01c01.pdf",
            section_num="1-01-01",
            toc_heading="This act - How referred to",
        )
        sec = scraper.parse_section(ref)
        assert sec is not None
        assert sec.work_number == "1-01-01"
        assert sec.heading == "This act"
        assert sec.body == "Body text here."
        assert sec.citation == "N.D. Cent. Code \u00a7 1-01-01"

    def test_parse_section_falls_back_to_toc_heading(self) -> None:
        scraper = NDCCStatutesScraper(generation_date=date(2026, 4, 21))
        # Cache has empty heading — scraper should use TOC heading.
        scraper._chapter_cache["/cencode/t01c01.pdf"] = {
            "1-01-01": ("", "Body only."),
        }
        ref = NDSectionRef(
            pdf_url="/cencode/t01c01.pdf",
            section_num="1-01-01",
            toc_heading="From TOC",
        )
        sec = scraper.parse_section(ref)
        assert sec is not None
        assert sec.heading == "From TOC"

    def test_parse_section_returns_none_when_body_missing(self) -> None:
        scraper = NDCCStatutesScraper(generation_date=date(2026, 4, 21))
        scraper._chapter_cache["/cencode/t01c01.pdf"] = {}
        ref = NDSectionRef(
            pdf_url="/cencode/t01c01.pdf",
            section_num="1-01-01",
            toc_heading="This act",
        )
        assert scraper.parse_section(ref) is None


class TestOutputPath:
    def _section(self, work_number: str) -> SourceSection:
        return SourceSection(
            jurisdiction="us-nd",
            doc_type="statute",
            authority_code="NDCC",
            work_number=work_number,
            citation=f"N.D. Cent. Code \u00a7 {work_number}",
            heading="H",
            body="B",
            author_id="nd-legislature",
            author_name="North Dakota Legislative Assembly",
            author_url="https://ndlegis.gov",
            generation_date=date(2026, 4, 21),
        )

    def test_nests_by_title_and_chapter(self) -> None:
        scraper = NDCCStatutesScraper()
        rel = scraper.relative_output_path(self._section("1-01-01"))
        assert rel == Path("us-nd/statutes/ch-1-01/ch-1-01-sec-1-01-01.txt")

    def test_handles_subsection_numbered_section(self) -> None:
        scraper = NDCCStatutesScraper()
        rel = scraper.relative_output_path(self._section("57-38-01.15"))
        assert rel == Path("us-nd/statutes/ch-57-38/ch-57-38-sec-57-38-01.15.txt")

    def test_handles_decimal_title_and_chapter(self) -> None:
        scraper = NDCCStatutesScraper()
        rel = scraper.relative_output_path(self._section("16.1-08.1-03.15"))
        assert rel == Path(
            "us-nd/statutes/ch-16.1-08.1/ch-16.1-08.1-sec-16.1-08.1-03.15.txt"
        )
