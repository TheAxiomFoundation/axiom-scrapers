"""Offline tests for the PA parser.

Fixtures are real slabs of ``legis.state.pa.us`` HTML saved under
``fixtures/``. No live HTTP — :meth:`PAStatutesScraper.parse_section`
is exercised via the pure helpers :func:`_split_sections` and
:func:`parse_title_html`, plus the scraper's ``parse_section`` for
end-to-end Section construction.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from axiom_scrapers.jurisdictions.us_pa.statutes.scrape import (
    MIN_VISIBLE_CHARS,
    PASectionRef,
    PAStatutesScraper,
    _extract_heading_and_body,
    _is_consolidated,
    _split_sections,
    parse_title_html,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _find(refs: list[PASectionRef], section: str) -> PASectionRef | None:
    return next((r for r in refs if r.section == section), None)


class TestSplitSections:
    def test_title_18_section_901_heading_and_citation(self) -> None:
        html = _load("title_18_excerpt.html")
        refs = _split_sections(html, 18)
        ref = _find(refs, "901")
        assert ref is not None
        assert ref.heading == "Criminal attempt"

        # End-to-end through the scraper: the SectionRef yields a
        # Section with the expected Pa.C.S. citation.
        scraper = PAStatutesScraper(generation_date=date(2026, 4, 20))
        sec = scraper.parse_section(ref)
        assert sec is not None
        assert sec.citation == "18 Pa.C.S. \u00a7 901"
        assert sec.heading == "Criminal attempt"
        assert sec.jurisdiction == "us-pa"
        assert sec.authority_code == "PaCS"
        assert sec.work_number == "18-901"

    def test_section_901_body_contains_definition(self) -> None:
        html = _load("title_18_excerpt.html")
        refs = _split_sections(html, 18)
        ref = _find(refs, "901")
        assert ref is not None
        # The statutory "substantial step" language is the textbook
        # definition of criminal attempt in PA.
        assert "substantial step" in ref.body_text.lower()
        assert "intent to commit" in ref.body_text.lower()

    def test_dedup_keeps_body_not_toc_entry(self) -> None:
        """Each section appears twice — keep the last (body), not TOC.

        In the TOC entry, section 901 appears as just
        ``&#167; 901. Criminal attempt.`` with no body. In the body
        entry, the text after the heading runs to hundreds of chars.
        If dedup kept the first occurrence we'd get an empty body.
        """
        html = _load("title_18_excerpt.html")
        # Sanity: the fixture genuinely contains section 901 twice.
        assert len(re.findall(r"&#167;\s*901\.", html)) == 2

        refs = _split_sections(html, 18)
        ref = _find(refs, "901")
        assert ref is not None
        # Body from the actual body entry is far longer than the TOC's
        # heading-only stub — proves we picked the last occurrence.
        assert len(ref.body_text) > 200

    def test_dedup_yields_each_section_once(self) -> None:
        html = _load("title_18_excerpt.html")
        refs = _split_sections(html, 18)
        section_ids = [r.section for r in refs]
        # No duplicates in the output even though each appears twice
        # in the source HTML.
        assert len(section_ids) == len(set(section_ids))


class TestIsConsolidated:
    def test_unconsolidated_title_1_excerpt_is_not_consolidated(self) -> None:
        html = _load("title_01_toc.html")
        # Sanity: the TOC fixture is below threshold.
        visible = len(re.sub(r"<[^>]+>", " ", html))
        assert visible < MIN_VISIBLE_CHARS
        assert _is_consolidated(html) is False

    def test_parse_title_html_returns_empty_for_unconsolidated(self) -> None:
        html = _load("title_01_toc.html")
        refs = parse_title_html(html, 1)
        assert refs == []

    def test_parse_title_html_returns_empty_for_tiny_input(self) -> None:
        assert parse_title_html("<html></html>", 42) == []


class TestExtractHeadingAndBody:
    def test_strips_source_trailer(self) -> None:
        # PA bodies often end with "(Source: ...)" parentheticals.
        slab = (
            "&nbsp;&nbsp;Definitions.</b></p>"
            "<p>The following words shall have these meanings.</p>"
            "<p>(Source: Act 1 of 2020, P.L. 1, No. 1.)</p>"
        )
        heading, body = _extract_heading_and_body(slab)
        assert heading == "Definitions"
        assert "Source:" not in body
        assert body.startswith("The following words")


class TestParseSectionShortCircuit:
    def test_short_body_returns_none(self) -> None:
        scraper = PAStatutesScraper(generation_date=date(2026, 4, 20))
        ref = PASectionRef(title=99, section="1", heading="Tiny", body_text="ok")
        assert scraper.parse_section(ref) is None

    def test_empty_body_returns_none(self) -> None:
        scraper = PAStatutesScraper(generation_date=date(2026, 4, 20))
        ref = PASectionRef(title=99, section="1", heading="", body_text="")
        assert scraper.parse_section(ref) is None


class TestPAScraperConfig:
    def test_subclass_config_complete(self) -> None:
        scraper = PAStatutesScraper()
        assert scraper.jurisdiction == "us-pa"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "PaCS"
        assert scraper.author_id == "pa-legislature"
        assert scraper.author_name == "Pennsylvania General Assembly"
        assert scraper.author_url == "https://www.legis.state.pa.us"

    def test_output_path_nests_by_title(self) -> None:
        from axiom_scrapers._common.akn import Section

        scraper = PAStatutesScraper()
        sec = Section(
            jurisdiction="us-pa",
            doc_type="statute",
            authority_code="PaCS",
            work_number="18-901",
            citation="18 Pa.C.S. \u00a7 901",
            heading="Criminal attempt",
            body="body",
            author_id="pa-legislature",
            author_name="Pennsylvania General Assembly",
            author_url="https://www.legis.state.pa.us",
            generation_date=date.today(),
        )
        rel = scraper.relative_output_path(sec)
        assert rel == Path("us-pa/statutes/tt-18/tt-18-sec-901.xml")

    def test_output_path_preserves_dotted_section(self) -> None:
        from axiom_scrapers._common.akn import Section

        scraper = PAStatutesScraper()
        sec = Section(
            jurisdiction="us-pa",
            doc_type="statute",
            authority_code="PaCS",
            work_number="42-5523.1",
            citation="42 Pa.C.S. \u00a7 5523.1",
            heading="Other offenses",
            body="body",
            author_id="pa-legislature",
            author_name="Pennsylvania General Assembly",
            author_url="https://www.legis.state.pa.us",
            generation_date=date.today(),
        )
        rel = scraper.relative_output_path(sec)
        assert rel == Path("us-pa/statutes/tt-42/tt-42-sec-5523.1.xml")
