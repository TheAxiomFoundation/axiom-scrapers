"""Offline parse tests for the Delaware Code scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.source_section import SourceSection
from axiom_scrapers.jurisdictions.us_de.statutes.scrape import (
    DelCodeStatutesScraper,
    DESectionRef,
    _extract_subchapter_slugs,
    _has_sections_inline,
    parse_sections,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseSections:
    def test_parses_real_title_1_chapter_1(self) -> None:
        sections = parse_sections(_fixture("t1_c001.html"))
        assert len(sections) >= 3
        # First section is § 101 — "Designation and citation of Code".
        first = sections[0]
        assert first[0] == "101"
        assert "Designation" in first[1]
        assert first[2]  # body is not empty

    def test_all_sections_have_ids(self) -> None:
        for section_id, _, _ in parse_sections(_fixture("t1_c001.html")):
            assert section_id
            assert section_id != ""

    def test_heading_strips_section_marker_and_trailing_period(self) -> None:
        html = (
            '<div class="Section">'
            '<div class="SectionHead" id="200">§ 200. Sample heading.</div>'
            '<p class="subsection">Body text.</p>'
            "</div></div></div></div>"
        )
        sections = parse_sections(html)
        assert len(sections) == 1
        assert sections[0][0] == "200"
        assert sections[0][1] == "Sample heading"

    def test_drops_history_anchors_below_last_p(self) -> None:
        html = (
            '<div class="Section">'
            '<div class="SectionHead" id="300">§ 300. H.</div>'
            '<p class="subsection">Body one.</p>'
            '<p class="subsection">Body two.</p>'
            '<a href="https://legis.delaware.gov/history">1 Del. C. 1953</a>'
            "</div></div></div></div>"
        )
        sections = parse_sections(html)
        assert len(sections) == 1
        body = sections[0][2]
        assert "Body one" in body
        assert "Body two" in body
        assert "legis.delaware.gov" not in body
        assert "1953" not in body

    def test_empty_html_returns_empty(self) -> None:
        assert parse_sections("") == []

    def test_alpha_section_id(self) -> None:
        html = (
            '<div class="Section">'
            '<div class="SectionHead" id="101A">§ 101A. Alpha heading.</div>'
            '<p class="subsection">Body.</p>'
            "</div></div></div></div>"
        )
        sections = parse_sections(html)
        assert sections[0][0] == "101A"


class TestHasSectionsInline:
    def test_inline_returns_true(self) -> None:
        assert _has_sections_inline(_fixture("t1_c001.html"))

    def test_split_chapter_returns_false(self) -> None:
        # Split chapter — TOC only, no SectionHead divs.
        assert not _has_sections_inline(
            '<a href="/title1/c001/sc01/index.html">Subchapter I</a>'
        )


class TestExtractSubchapterSlugs:
    def test_basic_extraction(self) -> None:
        html = (
            'href="/title1/c001/sc01/index.html" '
            'href="/title1/c001/sc02/index.html"'
        )
        assert _extract_subchapter_slugs(html, 1, "c001") == ["sc01", "sc02"]

    def test_filters_other_titles(self) -> None:
        html = (
            'href="/title1/c001/sc01/index.html" '
            'href="/title2/c001/sc01/index.html"'
        )
        assert _extract_subchapter_slugs(html, 1, "c001") == ["sc01"]

    def test_dedupes(self) -> None:
        html = (
            'href="/title1/c001/sc01/index.html" '
            'href="/title1/c001/sc01/index.html"'
        )
        assert _extract_subchapter_slugs(html, 1, "c001") == ["sc01"]


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = DelCodeStatutesScraper(generation_date=date(2026, 4, 20))
        assert scraper.jurisdiction == "us-de"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "Del. C."

    def test_parse_section_misses_cache(self) -> None:
        scraper = DelCodeStatutesScraper()
        # No cache → None.
        ref = DESectionRef(1, "c001", "", "999")
        assert scraper.parse_section(ref) is None

    def test_parse_section_from_cache(self) -> None:
        scraper = DelCodeStatutesScraper()
        ref = DESectionRef(1, "c001", "", "101")
        scraper._cache[ref] = ("Designation", "Body text.")
        sec = scraper.parse_section(ref)
        assert sec is not None
        assert sec.work_number == "1-101"
        assert sec.citation == "1 Del. C. § 101"
        assert sec.heading == "Designation"

    def test_repealed_in_cache_returns_none(self) -> None:
        scraper = DelCodeStatutesScraper()
        ref = DESectionRef(1, "c001", "", "999")
        scraper._cache[ref] = ("H", "[Repealed]")
        assert scraper.parse_section(ref) is None


class TestOutputPath:
    def _section(self, work_number: str) -> SourceSection:
        return SourceSection(
            jurisdiction="us-de",
            doc_type="statute",
            authority_code="Del. C.",
            work_number=work_number,
            citation=f"Del. C. {work_number}",
            heading="H",
            body="B",
            author_id="de-legislature",
            author_name="DE",
            author_url="https://legis.delaware.gov",
            generation_date=date(2026, 4, 20),
        )

    def test_nests_by_title(self) -> None:
        scraper = DelCodeStatutesScraper()
        rel = scraper.relative_output_path(self._section("1-101"))
        assert rel == Path("us-de/statutes/title-1/title-1-sec-101.txt")

    def test_nests_for_title_31(self) -> None:
        scraper = DelCodeStatutesScraper()
        rel = scraper.relative_output_path(self._section("31-500"))
        assert rel == Path("us-de/statutes/title-31/title-31-sec-500.txt")
