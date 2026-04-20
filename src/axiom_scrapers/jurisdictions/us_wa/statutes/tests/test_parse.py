"""Offline parse tests for the Washington RCW scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_wa.statutes.scrape import (
    RCWStatutesScraper,
    extract_chapter_tokens,
    extract_section_tokens,
    extract_title_tokens,
    parse_section_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseSectionPage:
    def test_parses_real_section(self) -> None:
        parsed = parse_section_page(_fixture("sec_1_04_010.html"))
        assert parsed is not None
        heading, body = parsed
        assert heading
        assert body

    def test_citation_not_found_returns_none(self) -> None:
        assert parse_section_page("Citation not found") is None

    def test_chapter_index_returns_none(self) -> None:
        # Chapter-page view served on the same endpoint — has 'chapter-page' class.
        html = (
            "<div id='contentWrapper' class='chapter-page'>chapter listing</div>"
            "<div id='ContentPlaceHolder1_pnlExpanded'></div>"
        )
        assert parse_section_page(html) is None

    def test_strips_session_law_brackets(self) -> None:
        html = (
            "<title>RCW 1.04.010: Designation</title>"
            "<div id='contentWrapper' class='section-page'>"
            "<p>Body text.</p>"
            "<div style=\"margin-top:15pt\">[ 1951 c 5 § 2; 1985 c 7 § 1.]</div>"
            "</div>"
            "<div id='ContentPlaceHolder1_pnlExpanded'></div>"
        )
        parsed = parse_section_page(html)
        assert parsed is not None
        _, body = parsed
        assert "Body text" in body
        assert "1951" not in body
        assert "1985" not in body

    def test_strips_notes_trailer(self) -> None:
        html = (
            "<title>RCW 1.04.010: H</title>"
            "<div id='contentWrapper' class='section-page'>"
            "<p>Body text.</p>"
            "<h3>Notes:</h3>"
            "<p>Editorial commentary not part of the statute.</p>"
            "</div>"
            "<div id='ContentPlaceHolder1_pnlExpanded'></div>"
        )
        parsed = parse_section_page(html)
        assert parsed is not None
        _, body = parsed
        assert "Body text" in body
        assert "Editorial" not in body

    def test_heading_falls_back_to_title_tag(self) -> None:
        html = (
            "<title>RCW 1.04.010: Designation and citation</title>"
            "<div id='contentWrapper' class='section-page'>"
            "<p>body</p>"
            "</div>"
            "<div id='ContentPlaceHolder1_pnlExpanded'></div>"
        )
        parsed = parse_section_page(html)
        assert parsed is not None
        assert parsed[0] == "Designation and citation"


class TestExtractTitleTokens:
    def test_basic(self) -> None:
        html = (
            '<table id="ContentPlaceHolder1_dgSections">'
            'default.aspx?Cite=1" '
            'default.aspx?Cite=9A" '
            'default.aspx?Cite=28A"'
            "</table>"
        )
        assert extract_title_tokens(html) == ["1", "9A", "28A"]

    def test_dedupes(self) -> None:
        html = (
            '<table id="ContentPlaceHolder1_dgSections">'
            'default.aspx?Cite=1" default.aspx?Cite=1"'
            "</table>"
        )
        assert extract_title_tokens(html) == ["1"]


class TestExtractChapterTokens:
    def test_filters_by_title(self) -> None:
        html = (
            "<div id='contentWrapper'>"
            "default.aspx?cite=1.04' "
            "default.aspx?cite=1.08' "
            "default.aspx?cite=2.01'"
            "</div>"
            "<div id='ContentPlaceHolder1_pnlExpanded'></div>"
        )
        assert extract_chapter_tokens(html, "1") == ["1.04", "1.08"]


class TestExtractSectionTokens:
    def test_filters_by_chapter(self) -> None:
        html = (
            "<div id='contentWrapper'>"
            "default.aspx?cite=1.04.010' "
            "default.aspx?cite=1.04.020' "
            "default.aspx?cite=1.08.010'"
            "</div>"
            "<div id='ContentPlaceHolder1_pnlExpanded'></div>"
        )
        assert extract_section_tokens(html, "1.04") == ["1.04.010", "1.04.020"]


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = RCWStatutesScraper(generation_date=date(2026, 4, 20))
        assert scraper.jurisdiction == "us-wa"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "RCW"


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-wa",
            doc_type="statute",
            authority_code="RCW",
            work_number=work_number,
            citation=f"RCW {work_number}",
            heading="H",
            body="B",
            author_id="wa-legislature",
            author_name="WA",
            author_url="https://app.leg.wa.gov",
            generation_date=date(2026, 4, 20),
        )

    def test_nests_by_chapter(self) -> None:
        scraper = RCWStatutesScraper()
        rel = scraper.relative_output_path(self._section("1.04.010"))
        assert rel == Path("us-wa/statute/ch-1.04/ch-1.04-sec-1.04.010.xml")

    def test_alpha_title(self) -> None:
        scraper = RCWStatutesScraper()
        rel = scraper.relative_output_path(self._section("9A.04.010"))
        assert rel == Path("us-wa/statute/ch-9A.04/ch-9A.04-sec-9A.04.010.xml")
