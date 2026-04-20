"""Offline tests for the Ohio RC parser.

Fixtures are real section HTML saved under ``fixtures/``. No live HTTP
in these tests — :meth:`parse_section` is exercised via the pure helper
:func:`_parse_section_html`. End-to-end fetch behavior is covered by
the shared base-class tests.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers.jurisdictions.us_oh.statutes.scrape import (
    RCStatutesScraper,
    _parse_section_html,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseSectionHtml:
    def test_known_section_yields_expected_citation(self) -> None:
        html = _load("5747_01.html")
        sec = _parse_section_html(html, generation_date=date(2026, 4, 17))
        assert sec is not None
        assert sec.jurisdiction == "us-oh"
        assert sec.authority_code == "RC"
        assert sec.work_number == "5747.01"
        assert sec.citation == "R.C. \u00a7 5747.01"

    def test_known_section_yields_expected_heading(self) -> None:
        html = _load("5747_01.html")
        sec = _parse_section_html(html, generation_date=date(2026, 4, 17))
        assert sec is not None
        # Section 5747.01 is Ohio's income-tax definitions section.
        assert sec.heading == "Definitions"

    def test_known_section_body_includes_definitions_text(self) -> None:
        html = _load("5747_01.html")
        sec = _parse_section_html(html, generation_date=date(2026, 4, 17))
        assert sec is not None
        # § 5747.01 is the definitions section for RC chapter 5747 (income tax).
        # The heading is "Definitions" and the body defines RC-wide terms by
        # reference to the Revised Code.
        assert sec.heading == "Definitions"
        assert "Revised Code" in sec.body
        # Sanity-check that the body is the whole statute, not just a snippet.
        assert len(sec.body) > 1000

    def test_last_updated_notice_is_stripped(self) -> None:
        html = _load("5747_01.html")
        sec = _parse_section_html(html, generation_date=date(2026, 4, 17))
        assert sec is not None
        # LSC's audit-trail "Last updated ..." footer must not leak into the body.
        assert "Last updated" not in sec.body

    def test_missing_laws_body_returns_none(self) -> None:
        html = (
            "<html><body>"
            "<h1>Section 99.99 <span class='codes-separator'>|</span> Repealed.</h1>"
            "<p>Section repealed; no laws-body present.</p>"
            "</body></html>"
        )
        assert _parse_section_html(html, generation_date=date.today()) is None

    def test_missing_h1_returns_none(self) -> None:
        html = (
            '<html><body><section class="laws-body"><p>Orphaned body.</p>'
            "</section></body></html>"
        )
        assert _parse_section_html(html, generation_date=date.today()) is None

    def test_author_metadata_populated(self) -> None:
        html = _load("5747_01.html")
        sec = _parse_section_html(html, generation_date=date(2026, 4, 17))
        assert sec is not None
        assert sec.author_id == "oh-lsc"
        assert sec.author_name == "Ohio Legislative Service Commission"
        assert sec.author_url == "https://codes.ohio.gov"


class TestRCScraperConfig:
    def test_subclass_config_complete(self) -> None:
        scraper = RCStatutesScraper()
        assert scraper.jurisdiction == "us-oh"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "RC"
        assert scraper.author_id == "oh-lsc"
        assert scraper.author_name == "Ohio Legislative Service Commission"
        assert scraper.author_url == "https://codes.ohio.gov"

    def test_output_path_nests_by_chapter(self) -> None:
        from axiom_scrapers._common.akn import Section

        scraper = RCStatutesScraper()
        sec = Section(
            jurisdiction="us-oh",
            doc_type="statute",
            authority_code="RC",
            work_number="5747.01",
            citation="R.C. \u00a7 5747.01",
            heading="Definitions",
            body="body",
            author_id="oh-lsc",
            author_name="Ohio Legislative Service Commission",
            author_url="https://codes.ohio.gov",
            generation_date=date.today(),
        )
        rel = scraper.relative_output_path(sec)
        # Chapter prefix ("5747") becomes the intermediate dir.
        assert rel == Path("us-oh/statute/ch-5747/5747.01.xml")

    def test_output_path_single_token_section(self) -> None:
        """A section number with no dot (rare but possible) still nests sensibly."""
        from axiom_scrapers._common.akn import Section

        scraper = RCStatutesScraper()
        sec = Section(
            jurisdiction="us-oh",
            doc_type="statute",
            authority_code="RC",
            work_number="1",
            citation="R.C. \u00a7 1",
            heading="Placeholder",
            body="body",
            author_id="oh-lsc",
            author_name="Ohio Legislative Service Commission",
            author_url="https://codes.ohio.gov",
            generation_date=date.today(),
        )
        rel = scraper.relative_output_path(sec)
        assert rel == Path("us-oh/statute/ch-1/1.xml")
