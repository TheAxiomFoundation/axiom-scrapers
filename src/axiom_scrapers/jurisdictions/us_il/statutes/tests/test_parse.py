"""Offline tests for IL parser.

Fixtures are real section HTML saved under ``fixtures/``. No live HTTP
in these tests — the subclass's :meth:`parse_section` is exercised via
the pure helper :func:`_parse_section_html`. End-to-end fetch behavior
is covered by the shared base-class tests.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers.jurisdictions.us_il.statutes.scrape import (
    ILCSStatutesScraper,
    _parse_iis_listing,
    _parse_section_html,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseSectionHtml:
    def test_known_section_yields_expected_citation(self) -> None:
        html = _load("35_155_2.html")
        sec = _parse_section_html(html, generation_date=date(2026, 4, 20))
        assert sec is not None
        assert sec.jurisdiction == "us-il"
        assert sec.authority_code == "ILCS"
        assert sec.work_number == "35-155-2"
        assert sec.citation == "35 ILCS 155/2"

    def test_known_section_yields_expected_heading(self) -> None:
        html = _load("35_155_2.html")
        sec = _parse_section_html(html, generation_date=date(2026, 4, 20))
        assert sec is not None
        assert sec.heading == "Definitions"

    def test_known_section_body_includes_definitions_text(self) -> None:
        html = _load("35_155_2.html")
        sec = _parse_section_html(html, generation_date=date(2026, 4, 20))
        assert sec is not None
        # The Automobile Renting Act § 2 is a definitions section.
        assert "Renting" in sec.body
        assert "Department" in sec.body

    def test_source_tail_is_stripped_from_body(self) -> None:
        html = _load("35_155_2.html")
        sec = _parse_section_html(html, generation_date=date(2026, 4, 20))
        assert sec is not None
        # "(Source: P.A. …)" should not leak into the body.
        assert "Source:" not in sec.body

    def test_no_ilcs_header_returns_none(self) -> None:
        html = "<html><body>Nothing to see here.</body></html>"
        assert _parse_section_html(html, generation_date=date.today()) is None

    def test_empty_body_returns_none(self) -> None:
        # Just the header, no body content — treat as repealed placeholder.
        html = """
        <html><body>
        <p>(99 ILCS 1/1)<br>
        Sec. 1.  Repealed.<br>
        (Source: P.A. 100-0000.)
        </p>
        </body></html>
        """
        # Body after "Sec. 1.  Repealed.<br>" is just the Source tail,
        # which gets stripped — leaves empty body.
        sec = _parse_section_html(html, generation_date=date.today())
        assert sec is None


class TestParseIisListing:
    def test_skips_to_parent_directory_link(self) -> None:
        html = """
        <A HREF="/ftp/">[To Parent Directory]</A><br>
        <A HREF="/ftp/ILCS/Ch%200005/">Ch 0005</A><br>
        <A HREF="/ftp/ILCS/Ch%200010/">Ch 0010</A><br>
        """
        entries = _parse_iis_listing(html)
        assert entries == [
            ("/ftp/ILCS/Ch%200005/", "Ch 0005", True),
            ("/ftp/ILCS/Ch%200010/", "Ch 0010", True),
        ]

    def test_trailing_slash_marks_directory(self) -> None:
        html = '<A HREF="/x/">Dir</A><A HREF="/x.html">File</A>'
        entries = _parse_iis_listing(html)
        assert entries == [
            ("/x/", "Dir", True),
            ("/x.html", "File", False),
        ]

    def test_empty_listing(self) -> None:
        assert _parse_iis_listing("") == []


class TestILCSScraperConfig:
    def test_subclass_config_complete(self) -> None:
        scraper = ILCSStatutesScraper()
        assert scraper.jurisdiction == "us-il"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "ILCS"
        assert scraper.author_id == "il-legislature"
        assert scraper.workers == 8

    def test_output_path_nests_by_chapter(self, tmp_path: Path) -> None:
        from axiom_scrapers._common.akn import Section

        scraper = ILCSStatutesScraper()
        sec = Section(
            jurisdiction="us-il",
            doc_type="statute",
            authority_code="ILCS",
            work_number="35-155-2",
            citation="35 ILCS 155/2",
            heading="Definitions",
            body="body",
            author_id="il-legislature",
            author_name="Illinois General Assembly",
            author_url="https://www.ilga.gov",
            generation_date=date.today(),
        )
        rel = scraper.relative_output_path(sec)
        # Chapter prefix ("35") becomes the intermediate dir.
        assert rel == Path("us-il/statute/ch-35/35-155-2.xml")
