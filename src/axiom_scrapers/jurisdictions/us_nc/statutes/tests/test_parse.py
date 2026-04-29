"""Offline tests for the North Carolina G.S. parser.

Runs against a real chapter-1 HTML fixture saved under ``fixtures/``.
No live HTTP here — the helpers tested (``split_sections``,
``GSStatutesScraper.parse_section``) operate on an in-memory cache.

Test coverage
-------------
* Full chapter yields 600+ sections — catches regex regressions.
* ``1-1`` has the expected heading and body opening — catches header
  and body delimiter drift.
* Dot-numbered sections like ``1-339.1`` survive the greedy section
  regex — catches the classic "[0-9A-Za-z.-]" bug where the ``.1``
  suffix would be dropped as "end of section id".
* Repealed-section placeholders (empty body after trailer strip) are
  filtered out via ``parse_section`` returning ``None``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers.jurisdictions.us_nc.statutes.scrape import (
    GSStatutesScraper,
    split_sections,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestSplitSections:
    def test_chapter_1_yields_600_plus_sections(self) -> None:
        html = _load("chapter_1.html")
        parsed = split_sections(html)
        # Chapter 1 is "Civil Procedure" — 740+ section headers as of
        # the fixture snapshot, including repealed placeholders.
        assert len(parsed) >= 600

    def test_known_section_1_1_has_expected_heading(self) -> None:
        html = _load("chapter_1.html")
        parsed = dict((s, (h, b)) for s, h, b in split_sections(html))
        heading, _ = parsed["1-1"]
        assert heading == "Remedies"

    def test_known_section_1_1_body_includes_remedies_text(self) -> None:
        html = _load("chapter_1.html")
        parsed = dict((s, (h, b)) for s, h, b in split_sections(html))
        _, body = parsed["1-1"]
        # G.S. 1-1: "Remedies in the courts of justice are divided into -"
        assert "Remedies in the courts of justice" in body
        # Subitems "(1) Actions" and "(2) Special proceedings" follow.
        assert "Actions" in body
        assert "Special proceedings" in body

    def test_dot_numbered_section_is_captured_as_one_unit(self) -> None:
        html = _load("chapter_1.html")
        ids = {sec for sec, _, _ in split_sections(html)}
        # Greedy scan must keep "1-339.1" together, not split into
        # "1-339" + orphan ".1".
        assert "1-339.1" in ids
        # A nearby plain section id still exists — confirms we didn't
        # over-capture and eat the next header.
        assert "1-340" in ids

    def test_dot_numbered_section_heading_parses(self) -> None:
        html = _load("chapter_1.html")
        parsed = dict((s, (h, b)) for s, h, b in split_sections(html))
        heading, body = parsed["1-339.1"]
        assert heading == "Definitions"
        assert "judicial sale" in body.lower()

    def test_repealed_section_has_empty_body(self) -> None:
        html = _load("chapter_1.html")
        parsed = dict((s, (h, b)) for s, h, b in split_sections(html))
        heading, body = parsed["1-9"]
        assert "Repealed" in heading
        # Body after the header is just a blank paragraph; trailer
        # strip is a no-op here but clean_text collapses it to "".
        assert body == ""

    def test_empty_document_yields_no_sections(self) -> None:
        assert split_sections("") == []
        assert split_sections("<html><body>Nothing here.</body></html>") == []


class TestParseSectionViaCache:
    def test_parse_section_returns_section_from_cache(self) -> None:
        scraper = GSStatutesScraper(generation_date=date(2026, 4, 17))
        scraper._cache[("1", "1-1")] = (
            "Remedies",
            "Remedies in the courts of justice are divided into ...",
        )
        sec = scraper.parse_section(("1", "1-1"))
        assert sec is not None
        assert sec.jurisdiction == "us-nc"
        assert sec.doc_type == "statute"
        assert sec.authority_code == "GS"
        assert sec.work_number == "1-1"
        assert sec.citation == "G.S. 1-1"
        assert sec.heading == "Remedies"
        assert sec.author_id == "nc-legislature"
        assert sec.author_name == "North Carolina General Assembly"
        assert sec.author_url == "https://www.ncleg.gov"

    def test_parse_section_skips_repealed_placeholder(self) -> None:
        scraper = GSStatutesScraper()
        # A repealed section caches with an empty body; parse_section
        # must return None so the base runner records it as "skipped".
        scraper._cache[("1", "1-9")] = ("Repealed by Session Laws 1967", "")
        assert scraper.parse_section(("1", "1-9")) is None

    def test_parse_section_skips_repealed_literal(self) -> None:
        scraper = GSStatutesScraper()
        # Some chapters emit "Repealed." as the body content instead of
        # an empty string; guard for that literal too.
        scraper._cache[("1", "99-1")] = ("Repealed", "Repealed.")
        assert scraper.parse_section(("1", "99-1")) is None

    def test_parse_section_returns_none_for_unknown_ref(self) -> None:
        scraper = GSStatutesScraper()
        assert scraper.parse_section(("99", "99-99")) is None

    def test_parse_section_preserves_dot_in_work_number(self) -> None:
        scraper = GSStatutesScraper(generation_date=date(2026, 4, 17))
        scraper._cache[("1", "1-339.1")] = ("Definitions", "body text")
        sec = scraper.parse_section(("1", "1-339.1"))
        assert sec is not None
        assert sec.work_number == "1-339.1"
        assert sec.citation == "G.S. 1-339.1"


class TestGSScraperConfig:
    def test_subclass_config_complete(self) -> None:
        scraper = GSStatutesScraper()
        assert scraper.jurisdiction == "us-nc"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "GS"
        assert scraper.author_id == "nc-legislature"
        assert scraper.author_name == "North Carolina General Assembly"
        assert scraper.author_url == "https://www.ncleg.gov"
        assert scraper.workers == 8

    def test_output_path_nests_by_chapter(self) -> None:
        from axiom_scrapers._common.source_section import SourceSection

        scraper = GSStatutesScraper()
        sec = SourceSection(
            jurisdiction="us-nc",
            doc_type="statute",
            authority_code="GS",
            work_number="1-339.1",
            citation="G.S. 1-339.1",
            heading="Definitions",
            body="body",
            author_id="nc-legislature",
            author_name="North Carolina General Assembly",
            author_url="https://www.ncleg.gov",
            generation_date=date.today(),
        )
        rel = scraper.relative_output_path(sec)
        # Chapter prefix ("1") becomes the intermediate dir; filename
        # prefixes the chapter token for browseability.
        assert rel == Path("us-nc/statutes/ch-1/ch-1-sec-1-339.1.txt")

    def test_output_path_handles_alphanumeric_chapter(self) -> None:
        from axiom_scrapers._common.source_section import SourceSection

        scraper = GSStatutesScraper()
        sec = SourceSection(
            jurisdiction="us-nc",
            doc_type="statute",
            authority_code="GS",
            work_number="14B-500",
            citation="G.S. 14B-500",
            heading="Some heading",
            body="body",
            author_id="nc-legislature",
            author_name="North Carolina General Assembly",
            author_url="https://www.ncleg.gov",
            generation_date=date.today(),
        )
        rel = scraper.relative_output_path(sec)
        assert rel == Path("us-nc/statutes/ch-14B/ch-14B-sec-14B-500.txt")
