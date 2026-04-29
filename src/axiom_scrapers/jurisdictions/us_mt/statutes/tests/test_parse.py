"""Offline tests for the MCA parser.

Fixtures are real section HTML saved under ``fixtures/`` (downloaded
from ``mca.legmt.gov`` during development). No live HTTP in these
tests — the subclass's :meth:`parse_section` is exercised via the pure
helper :func:`_parse_section_html`. End-to-end fetch behavior is
covered by the shared base-class tests.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers.jurisdictions.us_mt.statutes.scrape import (
    MCAStatutesScraper,
    _parse_section_html,
    _token_to_num,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseSectionHtml:
    def test_known_section_yields_expected_citation(self) -> None:
        html = _load("section_1_1_1_101.html")
        sec = _parse_section_html(html, generation_date=date(2026, 4, 20))
        assert sec is not None
        assert sec.jurisdiction == "us-mt"
        assert sec.authority_code == "MCA"
        assert sec.work_number == "1-1-101"
        assert sec.citation == "MCA \u00a7 1-1-101"

    def test_known_section_yields_expected_heading(self) -> None:
        html = _load("section_1_1_1_101.html")
        sec = _parse_section_html(html, generation_date=date(2026, 4, 20))
        assert sec is not None
        assert sec.heading == "Definition of law"

    def test_known_section_body_includes_definition_text(self) -> None:
        html = _load("section_1_1_1_101.html")
        sec = _parse_section_html(html, generation_date=date(2026, 4, 20))
        assert sec is not None
        # 1-1-101 is the MCA's "Definition of law":
        # "Law is a solemn expression of the will of the supreme power …"
        assert "Law" in sec.body
        assert "solemn expression" in sec.body

    def test_history_doc_is_excluded(self) -> None:
        html = _load("section_1_1_1_101.html")
        sec = _parse_section_html(html, generation_date=date(2026, 4, 20))
        assert sec is not None
        # The ``<div class="history-doc">`` trailer ("En. Sec. 5150, Pol.
        # C. 1895 …") must not leak into the body.
        assert "Pol. C. 1895" not in sec.body
        assert "History" not in sec.body

    def test_no_section_doc_returns_none(self) -> None:
        html = "<html><body>Nothing to see here.</body></html>"
        assert _parse_section_html(html, generation_date=date.today()) is None

    def test_missing_section_doc_on_404_page_returns_none(self) -> None:
        # Typical 404/landing page shape — plenty of HTML but no
        # section-doc div. Must skip cleanly instead of crashing.
        html = """
        <html><body>
          <div class="container">
            <h1>Page Not Found</h1>
            <p>We couldn't find what you were looking for.</p>
          </div>
        </body></html>
        """
        assert _parse_section_html(html, generation_date=date.today()) is None

    def test_multi_paragraph_section_preserves_subsection_markers(self) -> None:
        html = _load("section_15_30_2101.html")
        sec = _parse_section_html(html, generation_date=date(2026, 4, 20))
        assert sec is not None
        assert sec.work_number == "15-30-2101"
        assert sec.citation == "MCA \u00a7 15-30-2101"
        assert sec.heading == "Definitions"

        # Each <p class="line-indent"> becomes its own paragraph in the
        # body, so subsection markers "(1)", "(a)", … survive as
        # paragraph-leading text.
        assert "\n\n" in sec.body
        assert '(1)\t"Consumer price index"' in sec.body or '(1) "Consumer price index"' in sec.body
        assert "(2)" in sec.body
        assert "(a)" in sec.body
        assert "(b)" in sec.body
        # The catchline's lead-in sentence (after the heading) is
        # retained as the body's opening paragraph.
        assert "For the purpose of this chapter" in sec.body


class TestTokenToNum:
    def test_title_token_divides_by_ten(self) -> None:
        assert _token_to_num("title_0150") == "15"
        assert _token_to_num("title_0010") == "1"
        assert _token_to_num("title_0000") == "0"

    def test_chapter_and_part_tokens(self) -> None:
        assert _token_to_num("chapter_0030") == "3"
        assert _token_to_num("part_0210") == "21"

    def test_section_token_within_part(self) -> None:
        # section_0010 => section 1 within its part (the citation's
        # section component combines part × 100 + this value).
        assert _token_to_num("section_0010") == "1"
        assert _token_to_num("section_0100") == "10"

    def test_non_divisible_token_falls_back_to_raw(self) -> None:
        # Unusual tokens that don't end in 0 should not be silently
        # mangled — keep the raw integer string.
        assert _token_to_num("section_0123") == "123"

    def test_unrecognized_token_returns_itself(self) -> None:
        assert _token_to_num("weird") == "weird"


class TestMCAScraperConfig:
    def test_subclass_config_complete(self) -> None:
        scraper = MCAStatutesScraper()
        assert scraper.jurisdiction == "us-mt"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "MCA"
        assert scraper.author_id == "mt-legislature"
        assert scraper.author_name == "Montana Legislature"
        assert scraper.author_url == "https://leg.mt.gov"
        assert scraper.workers == 6

    def test_output_path_nests_by_title_and_chapter(self, tmp_path: Path) -> None:
        from axiom_scrapers._common.source_section import SourceSection

        scraper = MCAStatutesScraper()
        sec = SourceSection(
            jurisdiction="us-mt",
            doc_type="statute",
            authority_code="MCA",
            work_number="15-30-2101",
            citation="MCA \u00a7 15-30-2101",
            heading="Definitions",
            body="body",
            author_id="mt-legislature",
            author_name="Montana Legislature",
            author_url="https://leg.mt.gov",
            generation_date=date.today(),
        )
        rel = scraper.relative_output_path(sec)
        # Title + chapter prefix forms the intermediate dir.
        assert rel == Path("us-mt/statutes/ch-15-30/15-30-2101.txt")

    def test_output_path_for_single_digit_title(self) -> None:
        from axiom_scrapers._common.source_section import SourceSection

        scraper = MCAStatutesScraper()
        sec = SourceSection(
            jurisdiction="us-mt",
            doc_type="statute",
            authority_code="MCA",
            work_number="1-1-101",
            citation="MCA \u00a7 1-1-101",
            heading="Definition of law",
            body="body",
            author_id="mt-legislature",
            author_name="Montana Legislature",
            author_url="https://leg.mt.gov",
            generation_date=date.today(),
        )
        rel = scraper.relative_output_path(sec)
        assert rel == Path("us-mt/statutes/ch-1-1/1-1-101.txt")
