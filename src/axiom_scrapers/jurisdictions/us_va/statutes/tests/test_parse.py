"""Offline parse tests for the Virginia Code scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.source_section import SourceSection
from axiom_scrapers.jurisdictions.us_va.statutes.scrape import (
    VACodeStatutesScraper,
    VASectionRef,
    extract_chapter_tokens,
    extract_section_tokens,
    extract_title_tokens,
    parse_section_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseSectionPage:
    def test_parses_real_section_1_1(self) -> None:
        parsed = parse_section_page(_fixture("sec_1_1.html"))
        assert parsed is not None
        heading, body = parsed
        assert heading == "Contents and designation of Code"
        assert "Code of Virginia" in body

    def test_returns_none_when_no_h2_heading(self) -> None:
        assert parse_section_page("<html>no section here</html>") is None

    def test_returns_none_when_no_body_section(self) -> None:
        html = "<h2><span id='v0'>§ 1-1</span>. Title.</h2><p>stray</p>"
        assert parse_section_page(html) is None

    def test_synthetic_section(self) -> None:
        html = (
            "<h2> <span id='v0'>§ 2.2-4007</span>. Sample heading.</h2>"
            "<section class='body editable'>"
            "<p>First paragraph.</p>"
            "<p>Second paragraph.</p>"
            "</section>"
        )
        parsed = parse_section_page(html)
        assert parsed is not None
        heading, body = parsed
        assert heading == "Sample heading"
        assert "First paragraph." in body
        assert "Second paragraph." in body
        assert "\n\n" in body  # paragraph break preserved


class TestExtractTitleTokens:
    def test_real_root_toc(self) -> None:
        tokens = extract_title_tokens(_fixture("root_toc.html"))
        assert len(tokens) >= 60  # VA has 74 titles
        assert "1" in tokens
        assert "2.2" in tokens  # decimal title

    def test_dedupes(self) -> None:
        html = 'href="/vacode/title1/" href="/vacode/title1/"'
        assert extract_title_tokens(html) == ["1"]


class TestExtractChapterTokens:
    def test_real_title1_toc(self) -> None:
        tokens = extract_chapter_tokens(_fixture("title1_toc.html"), "1")
        assert "1" in tokens
        assert len(tokens) >= 5

    def test_filters_by_title(self) -> None:
        html = (
            'href="/vacode/title1/chapter1/" '
            'href="/vacode/title2/chapter1/"'
        )
        assert extract_chapter_tokens(html, "1") == ["1"]


class TestExtractSectionTokens:
    def test_real_title1_ch1_toc(self) -> None:
        tokens = extract_section_tokens(_fixture("title1_ch1_toc.html"), "1", "1")
        assert "1-1" in tokens
        assert len(tokens) >= 5

    def test_filters_by_title_and_chapter(self) -> None:
        html = (
            'href="/vacode/title1/chapter1/section1-1/" '
            'href="/vacode/title1/chapter2/section1-99/" '
            'href="/vacode/title2/chapter1/section2-1/"'
        )
        assert extract_section_tokens(html, "1", "1") == ["1-1"]


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = VACodeStatutesScraper(generation_date=date(2026, 4, 21))
        assert scraper.jurisdiction == "us-va"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "Va. Code Ann."

    def test_section_ref_frozen(self) -> None:
        ref = VASectionRef(title="1", chapter="1", section="1-1")
        assert ref.title == "1"


class TestOutputPath:
    def _section(self, work_number: str) -> SourceSection:
        return SourceSection(
            jurisdiction="us-va",
            doc_type="statute",
            authority_code="Va. Code Ann.",
            work_number=work_number,
            citation=f"Va. Code Ann. § {work_number}",
            heading="H",
            body="B",
            author_id="va-legislature",
            author_name="VA",
            author_url="https://law.lis.virginia.gov",
            generation_date=date(2026, 4, 21),
        )

    def test_nests_by_title(self) -> None:
        scraper = VACodeStatutesScraper()
        rel = scraper.relative_output_path(self._section("1-1"))
        assert rel == Path("us-va/statutes/title-1/title-1-sec-1-1.txt")

    def test_decimal_title(self) -> None:
        scraper = VACodeStatutesScraper()
        rel = scraper.relative_output_path(self._section("2.2-4007.01"))
        assert rel == Path("us-va/statutes/title-2.2/title-2.2-sec-2.2-4007.01.txt")
