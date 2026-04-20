"""Offline parse tests for the Missouri RSMo scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_mo.statutes.scrape import (
    RSMoStatutesScraper,
    extract_chapter_tokens,
    extract_section_tokens,
    parse_section_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseSectionPage:
    def test_parses_real_section_1_010(self) -> None:
        heading, body = parse_section_page(_fixture("sec_1_010.html"), "1.010")
        assert heading
        assert body
        assert len(body) > 50

    def test_returns_empty_on_no_norm_block(self) -> None:
        assert parse_section_page("<html>no norm</html>", "1.01") == ("", "")

    def test_strips_foot_history(self) -> None:
        html = (
            '<div class="norm">'
            '<span class="bold">1.010. Heading —</span>'
            "<p>Body text here.</p>"
            '<div class="foot">(RSMo 1939; L.1965 p.123)</div>'
            "</div><hr/>"
        )
        heading, body = parse_section_page(html, "1.010")
        assert heading == "Heading"
        assert "Body text" in body
        assert "RSMo 1939" not in body
        assert "L.1965" not in body

    def test_heading_from_og_description_fallback(self) -> None:
        html = (
            '<meta property="og:description" content="Common law in force."/>'
            '<div class="norm"><p>Body.</p></div><hr/>'
        )
        heading, _ = parse_section_page(html, "1.010")
        assert heading == "Common law in force"

    def test_strips_section_number_prefix(self) -> None:
        html = (
            '<div class="norm">'
            '<span class="bold">1.010. Designation</span>'
            "<p>Body.</p></div><hr/>"
        )
        heading, _ = parse_section_page(html, "1.010")
        assert heading == "Designation"


class TestExtractChapterTokens:
    def test_basic_extraction_and_sort(self) -> None:
        html = (
            '/main/OneChapter.aspx?chapter=143 '
            '/main/OneChapter.aspx?chapter=1 '
            '/main/OneChapter.aspx?chapter=32'
        )
        assert extract_chapter_tokens(html) == ["1", "32", "143"]

    def test_dedupes(self) -> None:
        html = (
            '/main/OneChapter.aspx?chapter=1 '
            '/main/OneChapter.aspx?chapter=1'
        )
        assert extract_chapter_tokens(html) == ["1"]

    def test_alpha_suffix_sorts_after_numeric(self) -> None:
        html = (
            '/main/OneChapter.aspx?chapter=1 '
            '/main/OneChapter.aspx?chapter=1A'
        )
        assert extract_chapter_tokens(html) == ["1", "1A"]


class TestExtractSectionTokens:
    def test_real_chapter_toc(self) -> None:
        tokens = extract_section_tokens(_fixture("ch_1_toc.html"))
        assert len(tokens) >= 5
        for tok in tokens:
            assert tok.startswith("1.")

    def test_preserves_order(self) -> None:
        html = (
            'PageSelect.aspx?section=1.020&bid=1 '
            'PageSelect.aspx?section=1.010&bid=2'
        )
        assert extract_section_tokens(html) == ["1.020", "1.010"]

    def test_handles_amp_escaped(self) -> None:
        html = 'PageSelect.aspx?section=1.030&amp;bid=1'
        assert extract_section_tokens(html) == ["1.030"]


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = RSMoStatutesScraper(generation_date=date(2026, 4, 20))
        assert scraper.jurisdiction == "us-mo"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "RSMo"


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-mo",
            doc_type="statute",
            authority_code="RSMo",
            work_number=work_number,
            citation=f"§ {work_number}, RSMo",
            heading="H",
            body="B",
            author_id="mo-legislature",
            author_name="MO",
            author_url="https://www.revisor.mo.gov",
            generation_date=date(2026, 4, 20),
        )

    def test_nests_by_chapter(self) -> None:
        scraper = RSMoStatutesScraper()
        rel = scraper.relative_output_path(self._section("1.010"))
        assert rel == Path("us-mo/statute/ch-1/ch-1-sec-1.010.xml")

    def test_three_digit_chapter(self) -> None:
        scraper = RSMoStatutesScraper()
        rel = scraper.relative_output_path(self._section("143.121"))
        assert rel == Path("us-mo/statute/ch-143/ch-143-sec-143.121.xml")
