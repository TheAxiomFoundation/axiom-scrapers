"""Offline parse tests for the South Carolina Code scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_sc.statutes.scrape import (
    SCCodeStatutesScraper,
    extract_chapter_tokens,
    extract_heading_and_body,
    extract_title_numbers,
    parse_sections,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseSections:
    def test_parses_real_t01c001(self) -> None:
        sections = parse_sections(_fixture("t01c001.html"), 1, "001")
        assert len(sections) >= 3
        for num, _, _ in sections:
            assert num.startswith("1-1-")

    def test_filters_by_title_and_chapter(self) -> None:
        html = (
            "SECTION 1-1-10. Heading.<br/><br/>"
            "Body for ch1 sec10."
            "SECTION 2-3-10. Other.<br/><br/>"
            "Body for other title."
            "HISTORY: foo"
        )
        sections = parse_sections(html, 1, "001")
        nums = [s[0] for s in sections]
        assert "1-1-10" in nums
        assert "2-3-10" not in nums

    def test_ignores_history_cross_references(self) -> None:
        """Section references inside a HISTORY block shouldn't become sections."""
        html = (
            "SECTION 1-1-10. Heading.<br/><br/>"
            "Body.<br/><br/>"
            "HISTORY: 1976 Code SECTION 1-1-99; amended 2020."
        )
        assert [s[0] for s in parse_sections(html, 1, "001")] == ["1-1-10"]


class TestExtractHeadingAndBody:
    def test_basic_split(self) -> None:
        slab = "Heading.<br/><br/>Body para one.<br/><br/>Body para two.HISTORY: x"
        heading, body = extract_heading_and_body(slab)
        assert heading == "Heading"
        assert "Body para one" in body
        assert "Body para two" in body
        assert "HISTORY" not in body

    def test_no_history_keeps_everything(self) -> None:
        slab = "H.<br/>Body."
        heading, body = extract_heading_and_body(slab)
        assert heading == "H"
        assert body == "Body."

    def test_no_br_returns_empty_heading(self) -> None:
        slab = "Heading and body with no break"
        heading, body = extract_heading_and_body(slab)
        assert heading == ""
        assert "Heading and body" in body


class TestExtractTitleNumbers:
    def test_basic(self) -> None:
        html = (
            'href="/code/title1.php" '
            'href="/code/title12.php" '
            'href="/code/title63.php"'
        )
        assert extract_title_numbers(html) == [1, 12, 63]

    def test_dedupes(self) -> None:
        html = 'href="/code/title1.php" href="/code/title1.php"'
        assert extract_title_numbers(html) == [1]


class TestExtractChapterTokens:
    def test_preserves_order(self) -> None:
        html = (
            'href="/code/t01c005.php" '
            'href="/code/t01c001.php" '
            'href="/code/t01c010.php"'
        )
        assert extract_chapter_tokens(html, 1) == ["005", "001", "010"]

    def test_filters_other_titles(self) -> None:
        html = (
            'href="/code/t01c001.php" '
            'href="/code/t02c001.php"'
        )
        assert extract_chapter_tokens(html, 1) == ["001"]


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = SCCodeStatutesScraper(generation_date=date(2026, 4, 20))
        assert scraper.jurisdiction == "us-sc"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "S.C. Code"

    def test_parse_section_from_cache(self) -> None:
        scraper = SCCodeStatutesScraper()
        ref = (1, "001", "1-1-10")
        scraper._cache[ref] = ("Jurisdiction", "Body text.")
        sec = scraper.parse_section(ref)
        assert sec is not None
        assert sec.work_number == "1-1-10"
        assert sec.citation == "S.C. Code \u00a7 1-1-10"


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-sc",
            doc_type="statute",
            authority_code="S.C. Code",
            work_number=work_number,
            citation=f"S.C. Code § {work_number}",
            heading="H",
            body="B",
            author_id="sc-legislature",
            author_name="SC",
            author_url="https://www.scstatehouse.gov",
            generation_date=date(2026, 4, 20),
        )

    def test_nests_by_title(self) -> None:
        scraper = SCCodeStatutesScraper()
        rel = scraper.relative_output_path(self._section("1-1-10"))
        assert rel == Path("us-sc/statute/ch-1/ch-1-sec-1-1-10.xml")

    def test_alpha_section_suffix(self) -> None:
        scraper = SCCodeStatutesScraper()
        rel = scraper.relative_output_path(self._section("12-21-2710A"))
        assert rel == Path("us-sc/statute/ch-12/ch-12-sec-12-21-2710A.xml")
