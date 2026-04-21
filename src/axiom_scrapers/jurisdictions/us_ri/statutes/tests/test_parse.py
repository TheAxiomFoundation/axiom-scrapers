"""Offline parse tests for the Rhode Island RIGL scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_ri.statutes.scrape import (
    RIGLStatutesScraper,
    RISectionRef,
    extract_chapter_tokens,
    extract_section_tokens,
    extract_titles,
    parse_section_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseSectionPage:
    def test_parses_real_section_1_2_1(self) -> None:
        parsed = parse_section_page(_fixture("sec_1_2_1.html"))
        assert parsed is not None
        section_id, heading, body = parsed
        assert section_id == "1-2-1"
        assert heading
        assert body

    def test_returns_none_on_no_header_or_citation(self) -> None:
        assert parse_section_page("<html>no headers</html>") is None

    def test_strips_history_of_section(self) -> None:
        html = (
            "<h3>R.I. Gen. Laws \u00a7 1-2-1</h3>"
            "<p><b>\u00a7 1-2-1. Sample.</b></p>"
            "<p>Body text.</p>"
            "<div><p>History of Section.<br>P.L. 1951, ch. 2850</p></div>"
            "</body></html>"
        )
        parsed = parse_section_page(html)
        assert parsed is not None
        _, _, body = parsed
        assert "Body text" in body
        assert "History of Section" not in body
        assert "P.L. 1951" not in body

    def test_heading_strips_trailing_period(self) -> None:
        html = (
            "<p><b>\u00a7 1-2-1. Sample heading.</b></p>"
            "<p>Body.</p>"
        )
        parsed = parse_section_page(html)
        assert parsed is not None
        assert parsed[1] == "Sample heading"

    def test_repealed_empty_body(self) -> None:
        html = (
            "<p><b>\u00a7 1-2-1. Repealed.</b></p>"
            "<p>History of Section.<br>P.L. 1951</p>"
        )
        parsed = parse_section_page(html)
        assert parsed is not None
        assert parsed[2] == ""

    def test_fallback_to_citation_header(self) -> None:
        """If the <p><b>...</b></p> header is missing, fall back to <h3>."""
        html = (
            "<h3>R.I. Gen. Laws \u00a7 1-2-1</h3>"
            "<p>Body.</p>"
        )
        parsed = parse_section_page(html)
        assert parsed is not None
        assert parsed[0] == "1-2-1"
        assert parsed[1] == ""


class TestExtractTitles:
    def test_basic(self) -> None:
        html = (
            'href="TITLE1/INDEX.HTM" '
            'href="TITLE6A/INDEX.HTM" '
            'href="TITLE40.1/INDEX.HTM"'
        )
        assert extract_titles(html) == ["1", "6A", "40.1"]

    def test_dedupes(self) -> None:
        html = 'href="TITLE1/INDEX.HTM" href="TITLE1/INDEX.HTM"'
        assert extract_titles(html) == ["1"]


class TestExtractChapterTokens:
    def test_basic(self) -> None:
        html = 'href="1-2/INDEX.htm" href="1-4.1/INDEX.htm"'
        assert extract_chapter_tokens(html, "1") == ["2", "4.1"]

    def test_filters_other_titles(self) -> None:
        html = 'href="1-2/INDEX.htm" href="2-3/INDEX.htm"'
        assert extract_chapter_tokens(html, "1") == ["2"]


class TestExtractSectionTokens:
    def test_basic(self) -> None:
        html = 'href="1-2-1.htm" href="1-2-17.1.htm"'
        assert extract_section_tokens(html, "1", "2") == ["1", "17.1"]

    def test_decimal_sort(self) -> None:
        html = (
            'href="1-2-18.htm" href="1-2-17.1.htm" '
            'href="1-2-17.htm" href="1-2-17.2.htm"'
        )
        tokens = extract_section_tokens(html, "1", "2")
        assert tokens == ["17", "17.1", "17.2", "18"]


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = RIGLStatutesScraper(generation_date=date(2026, 4, 20))
        assert scraper.jurisdiction == "us-ri"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "RIGL"

    def test_section_ref_frozen(self) -> None:
        ref = RISectionRef(title="1", chapter="2", section="1")
        assert ref.title == "1"


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-ri",
            doc_type="statute",
            authority_code="RIGL",
            work_number=work_number,
            citation=f"R.I. Gen. Laws § {work_number}",
            heading="H",
            body="B",
            author_id="ri-legislature",
            author_name="RI",
            author_url="https://webserver.rilegislature.gov",
            generation_date=date(2026, 4, 20),
        )

    def test_nests_by_title(self) -> None:
        scraper = RIGLStatutesScraper()
        rel = scraper.relative_output_path(self._section("1-2-1"))
        assert rel == Path("us-ri/statutes/ch-1/ch-1-sec-1-2-1.xml")

    def test_alpha_title(self) -> None:
        scraper = RIGLStatutesScraper()
        rel = scraper.relative_output_path(self._section("6A-2-1"))
        assert rel == Path("us-ri/statutes/ch-6A/ch-6A-sec-6A-2-1.xml")
