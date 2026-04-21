"""Offline parse tests for the Arizona A.R.S. scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_az.statutes.scrape import (
    ARSStatutesScraper,
    extract_toc_urls,
    parse_section_html,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseSectionHtml:
    def test_parses_real_section_1_101(self) -> None:
        parsed = parse_section_html(_fixture("sec_01_101.html"))
        assert parsed is not None
        section_id, heading, body = parsed
        assert section_id == "1-101"
        assert heading == "Designation and citation"
        assert body.startswith("The Arizona Revised Statutes")
        assert "A.R.S." in body

    def test_returns_none_on_missing_header(self) -> None:
        assert parse_section_html("<html><body>no header here</body></html>") is None

    def test_returns_none_on_repealed_placeholder(self) -> None:
        # AZ publishes repealed sections as blank placeholders; no <font> spans.
        html = "<HTML><BODY><p>Repealed</p></BODY></HTML>"
        assert parse_section_html(html) is None

    def test_strips_html_from_body(self) -> None:
        html = (
            "<p><font color=GREEN>1-200</font>. "
            "<font color=PURPLE><u>Test heading</u></font></p>"
            "<p>First paragraph.</p>"
            "<p>Second <b>bold</b> paragraph.</p>"
            "</BODY>"
        )
        parsed = parse_section_html(html)
        assert parsed is not None
        _, _, body = parsed
        assert "<b>" not in body
        assert "bold" in body

    def test_tolerates_quoted_attributes(self) -> None:
        # Some pages use color="GREEN", others color=GREEN — regex handles both.
        html = (
            '<p><font color="GREEN">1-300</font>. '
            '<font color="PURPLE"><u>Heading</u></font></p>'
            "<p>Body text.</p></BODY>"
        )
        parsed = parse_section_html(html)
        assert parsed is not None
        assert parsed[0] == "1-300"
        assert parsed[1] == "Heading"

    def test_body_trims_leading_period(self) -> None:
        """The regex captures up through the `. ` after the GREEN tag,
        but stray punctuation can slip through — confirm we clean it."""
        html = (
            "<p><font color=GREEN>1-400</font>. "
            "<font color=PURPLE><u>H</u></font></p>"
            ".<p>Actual body.</p></BODY>"
        )
        parsed = parse_section_html(html)
        assert parsed is not None
        assert parsed[2].startswith("Actual body")

    def test_empty_body_returns_tuple_with_empty_body(self) -> None:
        html = (
            "<p><font color=GREEN>1-500</font>. "
            "<font color=PURPLE><u>H</u></font></p></BODY>"
        )
        parsed = parse_section_html(html)
        assert parsed is not None
        assert parsed[2] == ""


class TestExtractTocUrls:
    def test_real_title_1_toc(self) -> None:
        urls = extract_toc_urls(_fixture("title_01_toc.html"), 1)
        assert len(urls) > 20  # title 1 has many sections
        assert all(u.startswith("https://www.azleg.gov/ars/1/") for u in urls)
        assert all(u.endswith(".htm") for u in urls)

    def test_filters_out_other_title_links(self) -> None:
        html = (
            'mixed links: '
            'href="https://www.azleg.gov/ars/1/00101.htm" '
            'href="https://www.azleg.gov/ars/2/00201.htm" '
            'href="/viewdocument/?docName=https://www.azleg.gov/ars/1/00102.htm"'
        )
        urls = extract_toc_urls(html, 1)
        assert urls == [
            "https://www.azleg.gov/ars/1/00101.htm",
            "https://www.azleg.gov/ars/1/00102.htm",
        ]

    def test_deduplicates_identical_urls(self) -> None:
        # TOC pages sometimes list each section twice (nav + list).
        html = (
            'href="https://www.azleg.gov/ars/3/00301.htm" '
            'href="https://www.azleg.gov/ars/3/00301.htm"'
        )
        urls = extract_toc_urls(html, 3)
        assert urls == ["https://www.azleg.gov/ars/3/00301.htm"]

    def test_empty_on_no_matches(self) -> None:
        assert extract_toc_urls("<html></html>", 5) == []


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = ARSStatutesScraper(generation_date=date(2026, 4, 20))
        assert scraper.jurisdiction == "us-az"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "ARS"

    def test_parse_section_returns_none_on_404(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from axiom_scrapers.jurisdictions.us_az.statutes import scrape

        monkeypatch.setattr(scrape, "http_get", lambda url: None)
        scraper = ARSStatutesScraper()
        assert scraper.parse_section((1, "https://example.test/")) is None


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-az",
            doc_type="statute",
            authority_code="ARS",
            work_number=work_number,
            citation=f"A.R.S. § {work_number}",
            heading="H",
            body="B",
            author_id="az-legislature",
            author_name="AZ",
            author_url="https://az.gov",
            generation_date=date(2026, 4, 20),
        )

    def test_nests_by_title(self) -> None:
        scraper = ARSStatutesScraper()
        rel = scraper.relative_output_path(self._section("1-101"))
        assert rel == Path("us-az/statutes/ch-1/ch-1-sec-1-101.xml")

    def test_nests_for_two_digit_title(self) -> None:
        scraper = ARSStatutesScraper()
        rel = scraper.relative_output_path(self._section("43-1001"))
        assert rel == Path("us-az/statutes/ch-43/ch-43-sec-43-1001.xml")
