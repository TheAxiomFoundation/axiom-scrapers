"""Offline parse tests for the Nebraska Revised Statutes scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.source_section import SourceSection
from axiom_scrapers.jurisdictions.us_ne.statutes.scrape import (
    NebRevStatScraper,
    extract_section_tokens,
    parse_section_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseSectionPage:
    def test_parses_real_section_77_101(self) -> None:
        parsed = parse_section_page(_fixture("sec_77_101.html"), "77-101")
        assert parsed is not None
        section_num, heading, body = parsed
        assert section_num == "77-101"
        assert heading
        assert body

    def test_returns_none_on_no_statute_div(self) -> None:
        # NE returns 200 with plain-text error body for invalid ids.
        assert parse_section_page("Invalid statute number format: 'zzz'", "zzz") is None

    def test_repealed_no_body_returns_none(self) -> None:
        html = (
            '<div class="statute">'
            "<h2>1-999.</h2>"
            "<h3>Repealed. Laws 1957, c. 1, § 65.</h3>"
            "</div></div></div></div>"
        )
        assert parse_section_page(html, "1-999") is None

    def test_strips_source_history_div(self) -> None:
        html = (
            '<div class="statute">'
            "<h2>1-101.</h2>"
            "<h3>Heading.</h3>"
            '<p class="text-justify">Body text.</p>'
            "<div><h2>Source</h2><ul><li>RS 1866</li></ul></div>"
            "</div></div></div></div>"
        )
        parsed = parse_section_page(html, "1-101")
        assert parsed is not None
        _, heading, body = parsed
        assert heading == "Heading"
        assert "Body text" in body
        assert "RS 1866" not in body

    def test_section_num_falls_back_to_expected(self) -> None:
        html = (
            '<div class="statute">'
            "<h3>Heading.</h3>"
            '<p class="text-justify">Body text.</p>'
            "</div></div></div></div>"
        )
        parsed = parse_section_page(html, "1-101")
        assert parsed is not None
        assert parsed[0] == "1-101"  # fell back to expected_token

    def test_body_joins_multiple_paragraphs(self) -> None:
        html = (
            '<div class="statute">'
            "<h2>1-101.</h2><h3>H.</h3>"
            '<p class="text-justify">First.</p>'
            '<p class="text-justify">Second.</p>'
            "</div></div></div></div>"
        )
        parsed = parse_section_page(html, "1-101")
        assert parsed is not None
        assert parsed[2] == "First.\n\nSecond."


class TestExtractSectionTokens:
    def test_real_chapter_1_toc(self) -> None:
        tokens = extract_section_tokens(_fixture("ch_1_toc.html"), 1)
        assert len(tokens) >= 5
        for tok in tokens:
            assert tok.startswith("1-")

    def test_filters_cross_chapter_refs(self) -> None:
        html = (
            '<a href="/laws/statutes.php?statute=1-101">'
            '<span class="sr-only">View Statute </span>1-101</a>'
            '<a href="/laws/statutes.php?statute=2-101">'
            '<span class="sr-only">View Statute </span>2-101</a>'
        )
        assert extract_section_tokens(html, 1) == ["1-101"]

    def test_preserves_order_and_dedupes(self) -> None:
        html = (
            '<a href="/laws/statutes.php?statute=1-102">'
            '<span class="sr-only">View Statute </span>1-102</a>'
            '<a href="/laws/statutes.php?statute=1-101">'
            '<span class="sr-only">View Statute </span>1-101</a>'
            '<a href="/laws/statutes.php?statute=1-102">'
            '<span class="sr-only">View Statute </span>1-102</a>'
        )
        assert extract_section_tokens(html, 1) == ["1-102", "1-101"]


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = NebRevStatScraper(generation_date=date(2026, 4, 20))
        assert scraper.jurisdiction == "us-ne"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "NebRevStat"


class TestOutputPath:
    def _section(self, work_number: str) -> SourceSection:
        return SourceSection(
            jurisdiction="us-ne",
            doc_type="statute",
            authority_code="NebRevStat",
            work_number=work_number,
            citation=f"Neb. Rev. Stat. \u00a7 {work_number}",
            heading="H",
            body="B",
            author_id="ne-legislature",
            author_name="NE",
            author_url="https://nebraskalegislature.gov",
            generation_date=date(2026, 4, 20),
        )

    def test_nests_by_chapter(self) -> None:
        scraper = NebRevStatScraper()
        rel = scraper.relative_output_path(self._section("1-101"))
        assert rel == Path("us-ne/statutes/ch-1/ch-1-sec-101.txt")

    def test_decimal_section(self) -> None:
        scraper = NebRevStatScraper()
        rel = scraper.relative_output_path(self._section("1-105.01"))
        assert rel == Path("us-ne/statutes/ch-1/ch-1-sec-105.01.txt")

    def test_two_digit_chapter(self) -> None:
        scraper = NebRevStatScraper()
        rel = scraper.relative_output_path(self._section("90-1201"))
        assert rel == Path("us-ne/statutes/ch-90/ch-90-sec-1201.txt")
