"""Offline parse tests for the Minnesota Statutes scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_mn.statutes.scrape import (
    MinnStatutesScraper,
    extract_chapter_tokens,
    extract_section_tokens,
    parse_section_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseSectionPage:
    def test_parses_real_section_1_01(self) -> None:
        parsed = parse_section_page(_fixture("sec_1_01.html"), "1.01")
        assert parsed is not None
        heading, body = parsed
        assert "EXTENT" in heading.upper()
        assert body

    def test_repealed_section_returns_none(self) -> None:
        html = '<div class="sr" id="stat.1.99">Repealed.</div>'
        assert parse_section_page(html, "1.99") is None

    def test_missing_section_returns_none(self) -> None:
        assert parse_section_page("<html>no content</html>", "1.01") is None

    def test_heading_strips_section_number(self) -> None:
        html = (
            '<div class="section" id="stat.1.01">'
            '<h1 class="shn">1.01 EXTENT.</h1>'
            "<p>Body paragraph.</p>"
            '</div><div class="history">...</div>'
        )
        parsed = parse_section_page(html, "1.01")
        assert parsed is not None
        assert parsed[0] == "EXTENT"

    def test_body_cleans_html(self) -> None:
        html = (
            '<div class="section" id="stat.1.01">'
            '<h1 class="shn">1.01 H.</h1>'
            '<p>One <a class="permalink">#</a> with permalink.</p>'
            "<p>Two.</p>"
            '</div><div class="history">...</div>'
        )
        parsed = parse_section_page(html, "1.01")
        assert parsed is not None
        _, body = parsed
        assert "permalink" not in body.lower() or "#" not in body
        assert "One" in body
        assert "Two" in body


class TestExtractChapterTokens:
    def test_numeric_and_alpha_chapters_sorted(self) -> None:
        html = (
            'href="/statutes/cite/2A" '
            'href="/statutes/cite/1" '
            'href="/statutes/cite/13D" '
            'href="/statutes/cite/2"'
        )
        assert extract_chapter_tokens(html) == ["1", "2", "2A", "13D"]

    def test_dedupes(self) -> None:
        html = 'href="/statutes/cite/1" href="/statutes/cite/1"'
        assert extract_chapter_tokens(html) == ["1"]

    def test_ignores_section_links(self) -> None:
        # Section links have dots; chapter tokens don't.
        html = 'href="/statutes/cite/1" href="/statutes/cite/1.01"'
        assert extract_chapter_tokens(html) == ["1"]

    def test_tolerates_absolute_urls(self) -> None:
        html = 'href="https://www.revisor.mn.gov/statutes/cite/5"'
        assert extract_chapter_tokens(html) == ["5"]


class TestExtractSectionTokens:
    def test_real_chapter_1_toc(self) -> None:
        tokens = extract_section_tokens(_fixture("ch_1_toc.html"), "1")
        assert len(tokens) >= 5
        for tok in tokens:
            assert tok.startswith("1.")

    def test_preserves_order(self) -> None:
        html = (
            'href="/statutes/cite/1.03" '
            'href="/statutes/cite/1.01" '
            'href="/statutes/cite/1.02"'
        )
        assert extract_section_tokens(html, "1") == ["1.03", "1.01", "1.02"]

    def test_filters_other_chapters(self) -> None:
        html = 'href="/statutes/cite/1.01" href="/statutes/cite/2.01"'
        assert extract_section_tokens(html, "1") == ["1.01"]


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = MinnStatutesScraper(generation_date=date(2026, 4, 20))
        assert scraper.jurisdiction == "us-mn"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "Minn. Stat."


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-mn",
            doc_type="statute",
            authority_code="Minn. Stat.",
            work_number=work_number,
            citation=f"Minn. Stat. \u00a7 {work_number}",
            heading="H",
            body="B",
            author_id="mn-legislature",
            author_name="MN",
            author_url="https://www.revisor.mn.gov",
            generation_date=date(2026, 4, 20),
        )

    def test_nests_by_chapter(self) -> None:
        scraper = MinnStatutesScraper()
        rel = scraper.relative_output_path(self._section("1.01"))
        assert rel == Path("us-mn/statute/ch-1/ch-1-sec-1.01.xml")

    def test_alpha_suffix_chapter(self) -> None:
        scraper = MinnStatutesScraper()
        rel = scraper.relative_output_path(self._section("2A.04"))
        assert rel == Path("us-mn/statute/ch-2A/ch-2A-sec-2A.04.xml")
