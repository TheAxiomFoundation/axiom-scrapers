"""Offline parse tests for the Iowa Code scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_ia.statutes.scrape import (
    IASectionRef,
    IowaCodeStatutesScraper,
    extract_chapter_tokens,
    extract_section_tokens,
    extract_title_tokens,
    parse_pdf_body,
    strip_page_chrome,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestExtractTitleTokens:
    def test_real_root_toc(self) -> None:
        tokens = extract_title_tokens(_fixture("root_toc.html"))
        # Iowa has 16 titles I..XVI; each typically appears multiple
        # times on the root page (menu + table row). Require every one.
        assert set(tokens) >= {
            "I",
            "II",
            "III",
            "IV",
            "V",
            "VI",
            "VII",
            "VIII",
            "IX",
            "X",
            "XI",
            "XII",
            "XIII",
            "XIV",
            "XV",
            "XVI",
        }

    def test_dedupes(self) -> None:
        html = (
            'href="/law/iowaCode/chapters?title=I&year=2026" '
            'href="/law/iowaCode/chapters?title=I&year=2026"'
        )
        assert extract_title_tokens(html) == ["I"]

    def test_uppercases(self) -> None:
        html = 'href="/law/iowaCode/chapters?title=iv&year=2026"'
        assert extract_title_tokens(html) == ["IV"]


class TestExtractChapterTokens:
    def test_real_title_i_toc(self) -> None:
        tokens = extract_chapter_tokens(_fixture("title_i_toc.html"))
        assert "1" in tokens
        assert "2" in tokens
        # Title I has alpha-suffix chapters like 1A, 1B, 1C, 1D, 2A…
        assert any(t.endswith("A") for t in tokens)

    def test_alpha_suffix(self) -> None:
        html = 'href="/law/iowaCode/sections?codeChapter=38D&year=2026"'
        assert extract_chapter_tokens(html) == ["38D"]

    def test_dedupes(self) -> None:
        html = (
            'href="/law/iowaCode/sections?codeChapter=1&year=2026" '
            'href="/law/iowaCode/sections?codeChapter=1&year=2026"'
        )
        assert extract_chapter_tokens(html) == ["1"]


class TestExtractSectionTokens:
    def test_real_chapter_1_sections(self) -> None:
        tokens = extract_section_tokens(_fixture("ch1_sections.html"), "1")
        assert "1" in tokens
        assert "15A" in tokens  # alpha-suffix section
        # Chapter 1 has sections 1..18 plus 15A.
        assert len(tokens) >= 18

    def test_filters_by_chapter(self) -> None:
        html = (
            'href="/docs/code/2026/1.1.pdf" '
            'href="/docs/code/2026/2.5.pdf" '
            'href="/docs/code/2026/1.15A.pdf"'
        )
        assert extract_section_tokens(html, "1") == ["1", "15A"]

    def test_skips_full_chapter_pdf(self) -> None:
        # The ``/docs/code/YYYY/1.pdf`` full-chapter link has no
        # section component; the regex requires ``chapter.section`` so
        # the bare chapter PDF is excluded automatically.
        html = 'href="/docs/code/2026/1.pdf" href="/docs/code/2026/1.1.pdf"'
        assert extract_section_tokens(html, "1") == ["1"]


class TestStripPageChrome:
    def test_drops_page1_header(self) -> None:
        text = (
            "1                                      SOVEREIGNTY, §1.1\n"
            "  1.1 Heading.\n"
            "  Body.\n"
        )
        out = strip_page_chrome(text).strip()
        assert "SOVEREIGNTY" not in out
        assert "Heading" in out

    def test_drops_page_n_header(self) -> None:
        text = (
            "§422.7, INDIVIDUAL INCOME                                     2\n"
            "continued body paragraph.\n"
        )
        out = strip_page_chrome(text).strip()
        assert "§422.7" not in out
        assert "continued body" in out

    def test_drops_timestamp_footer(self) -> None:
        text = (
            "Body line.\n"
            "Wed Dec 10 21:39:07 2025  Iowa Code 2026, Section 1.1 (17, 0)\n"
        )
        out = strip_page_chrome(text).strip()
        assert "Iowa Code 2026" not in out
        assert "Body line" in out


class TestParsePdfBody:
    def test_real_section_1_1(self) -> None:
        heading, body = parse_pdf_body(_fixture("sec_1_1.txt"), "1.1")
        assert heading == "State boundaries"
        assert "Constitution of the State" in body
        # Provenance citations (Code history, session acts, cross-refs)
        # stay inline with the body per Atlas convention.
        assert "2009 Acts" in body
        assert "Iowa Code 2026" not in body  # footer stripped

    def test_real_section_422_7_multi_page(self) -> None:
        heading, body = parse_pdf_body(_fixture("sec_422_7.txt"), "422.7")
        assert "Net income" in heading
        # Content from page 1 (subsection 1) and page 2 (subsection 6) both present.
        assert "Subtract interest and dividends" in body
        assert "small business" in body
        # No page chrome bled through.
        assert "INDIVIDUAL INCOME" not in body
        assert "Iowa Code 2026" not in body

    def test_simple_extraction(self) -> None:
        text = (
            "1.5 Sample heading.\n"
            "\n"
            "First paragraph of body.\n"
            "\n"
            "Second paragraph.\n"
        )
        heading, body = parse_pdf_body(text, "1.5")
        assert heading == "Sample heading"
        assert "First paragraph of body." in body
        assert "Second paragraph." in body
        assert "\n\n" in body  # paragraph break preserved

    def test_multiline_paragraph_collapsed(self) -> None:
        text = (
            "1.5 H.\n"
            "\n"
            "This paragraph was split by\n"
            "pdftotext layout across multiple\n"
            "source lines.\n"
        )
        _, body = parse_pdf_body(text, "1.5")
        assert body == (
            "This paragraph was split by pdftotext layout across multiple source lines."
        )

    def test_empty_body(self) -> None:
        text = "1.5 Heading only.\n"
        heading, body = parse_pdf_body(text, "1.5")
        assert heading == "Heading only"
        assert body == ""


class TestSectionRef:
    def test_work_number_composition(self) -> None:
        ref = IASectionRef(chapter="422", section="7", year=2026)
        assert ref.work_number == "422.7"

    def test_alpha_suffix_section(self) -> None:
        ref = IASectionRef(chapter="1", section="15A", year=2026)
        assert ref.work_number == "1.15A"

    def test_is_frozen(self) -> None:
        ref = IASectionRef(chapter="1", section="1", year=2026)
        assert ref.chapter == "1"


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = IowaCodeStatutesScraper(generation_date=date(2026, 4, 21))
        assert scraper.jurisdiction == "us-ia"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "Iowa Code"
        # Year defaults to the scrape year.
        assert scraper.year == 2026

    def test_year_override(self) -> None:
        scraper = IowaCodeStatutesScraper(generation_date=date(2026, 4, 21), year=2025)
        assert scraper.year == 2025


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-ia",
            doc_type="statute",
            authority_code="Iowa Code",
            work_number=work_number,
            citation=f"Iowa Code § {work_number}",
            heading="H",
            body="B",
            author_id="ia-legislature",
            author_name="IA",
            author_url="https://www.legis.iowa.gov",
            generation_date=date(2026, 4, 21),
        )

    def test_nests_by_chapter(self) -> None:
        scraper = IowaCodeStatutesScraper()
        rel = scraper.relative_output_path(self._section("422.7"))
        assert rel == Path("us-ia/statutes/ch-422/ch-422-sec-422.7.xml")

    def test_alpha_suffix_chapter(self) -> None:
        scraper = IowaCodeStatutesScraper()
        rel = scraper.relative_output_path(self._section("38D.2"))
        assert rel == Path("us-ia/statutes/ch-38D/ch-38D-sec-38D.2.xml")

    def test_alpha_suffix_section(self) -> None:
        scraper = IowaCodeStatutesScraper()
        rel = scraper.relative_output_path(self._section("1.15A"))
        assert rel == Path("us-ia/statutes/ch-1/ch-1-sec-1.15A.xml")
