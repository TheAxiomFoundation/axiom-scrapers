"""Offline parse tests for the Indiana Code scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_in.statutes.scrape import (
    ICStatutesScraper,
    split_sections,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestSplitSections:
    def test_parses_real_title_2_excerpt(self) -> None:
        sections = split_sections(_fixture("title_2_excerpt.html"), "2")
        assert len(sections) >= 5

    def test_first_section_has_ic_number_and_heading(self) -> None:
        sections = split_sections(_fixture("title_2_excerpt.html"), "2")
        div_id, section_num, heading, body = sections[0]
        assert div_id.startswith("2-")
        assert section_num.startswith("2-")
        assert heading
        assert body

    def test_strips_history_paragraphs(self) -> None:
        html = (
            '<div class="section" id="2-1-1-1">'
            '<span id="ic_number">IC 2-1-1-1</span>'
            '<span id="shortdescription">Heading</span>'
            "</div>"
            "<p>Body paragraph.</p>"
            "<p><i>As added by P.L.1-2005, SEC.1.</i></p>"
        )
        sections = split_sections(html, "2")
        body = sections[0][3]
        assert "Body paragraph" in body
        assert "As added" not in body
        assert "P.L.1-2005" not in body

    def test_filters_sections_from_wrong_title(self) -> None:
        html = (
            '<div class="section" id="2-1-1-1">'
            '<span id="ic_number">IC 2-1-1-1</span>'
            '<span id="shortdescription">A</span></div>'
            "<p>body2</p>"
            '<div class="section" id="3-1-1-1">'
            '<span id="ic_number">IC 3-1-1-1</span>'
            '<span id="shortdescription">B</span></div>'
            "<p>body3</p>"
        )
        sections = split_sections(html, "2")
        ids = [s[0] for s in sections]
        assert "2-1-1-1" in ids
        assert "3-1-1-1" not in ids

    def test_heading_strips_trailing_period(self) -> None:
        html = (
            '<div class="section" id="2-1-1-1">'
            '<span id="ic_number">IC 2-1-1-1</span>'
            '<span id="shortdescription">Sample heading.</span></div>'
            "<p>body</p>"
        )
        assert split_sections(html, "2")[0][2] == "Sample heading"

    def test_empty_body_returns_empty_string(self) -> None:
        html = (
            '<div class="section" id="2-1-1-1">'
            '<span id="ic_number">IC 2-1-1-1</span>'
            '<span id="shortdescription">H</span></div>'
        )
        assert split_sections(html, "2")[0][3] == ""

    def test_version_suffix_preserved_in_div_id(self) -> None:
        html = (
            '<div class="section" id="6-1.1-12-10.1-b">'
            '<span id="ic_number">IC 6-1.1-12-10.1</span>'
            '<span id="shortdescription">Successor</span></div>'
            "<p>b</p>"
        )
        assert split_sections(html, "6")[0][0] == "6-1.1-12-10.1-b"


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = ICStatutesScraper(generation_date=date(2026, 4, 20))
        assert scraper.jurisdiction == "us-in"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "IC"

    def test_parse_section_miss_returns_none(self) -> None:
        scraper = ICStatutesScraper()
        assert scraper.parse_section(("2", "2-1-1-1")) is None

    def test_parse_section_from_cache(self) -> None:
        scraper = ICStatutesScraper()
        scraper._cache[("2", "2-1-1-1")] = ("2-1-1-1", "Heading", "Body.")
        sec = scraper.parse_section(("2", "2-1-1-1"))
        assert sec is not None
        assert sec.work_number == "2-1-1-1"
        assert sec.citation == "IC 2-1-1-1"

    def test_parse_section_empty_body_returns_none(self) -> None:
        scraper = ICStatutesScraper()
        scraper._cache[("2", "2-1-1-1")] = ("2-1-1-1", "H", "")
        assert scraper.parse_section(("2", "2-1-1-1")) is None


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-in",
            doc_type="statute",
            authority_code="IC",
            work_number=work_number,
            citation=f"IC {work_number}",
            heading="H",
            body="B",
            author_id="in-legislature",
            author_name="IN",
            author_url="https://iga.in.gov",
            generation_date=date(2026, 4, 20),
        )

    def test_nests_by_title(self) -> None:
        scraper = ICStatutesScraper()
        rel = scraper.relative_output_path(self._section("2-1-1-1"))
        assert rel == Path("us-in/statute/ch-2/ch-2-sec-1-1-1.xml")

    def test_version_suffix_kept_in_filename(self) -> None:
        scraper = ICStatutesScraper()
        rel = scraper.relative_output_path(self._section("6-1.1-12-10.1-b"))
        assert rel == Path("us-in/statute/ch-6/ch-6-sec-1.1-12-10.1-b.xml")
