"""Offline parse tests for the Vermont Statutes Annotated scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_vt.statutes.scrape import (
    VSAStatutesScraper,
    VTSectionRef,
    extract_chapter_tokens,
    extract_section_tokens,
    extract_title_tokens,
    parse_section_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseSectionPage:
    def test_parses_real_section_32_151_05811(self) -> None:
        parsed = parse_section_page(_fixture("sec_32_151_05811.html"))
        assert parsed is not None
        title, section, heading, body = parsed
        assert title == "32"
        # The `(Cite as:)` marker carries the unpadded citation form.
        # Confirming the exact string pins the canonicalization contract.
        assert section == "5811"
        assert heading
        assert body

    def test_returns_none_without_cite_marker(self) -> None:
        assert parse_section_page("<html>no cite here</html>") is None

    def test_returns_none_without_detail_ul(self) -> None:
        html = "<b>(Cite as: 32 V.S.A. § 5811)</b><p>stray content</p>"
        assert parse_section_page(html) is None

    def test_basic_synthetic_parse(self) -> None:
        html = (
            "<b>(Cite as: 32 V.S.A. § 5811)</b>"
            '<ul class="item-list statutes-detail">'
            "<li><p>§ 5811. Definitions.</p>"
            "<p>For purposes of this chapter:</p>"
            "<p>(1) Sample.</p></li></ul>"
        )
        parsed = parse_section_page(html)
        assert parsed is not None
        title, section, heading, body = parsed
        assert title == "32"
        assert section == "5811"
        assert heading == "Definitions"
        assert "For purposes" in body


class TestExtractTitleTokens:
    def test_basic(self) -> None:
        html = (
            'href="/statutes/title/01" '
            'href="/statutes/title/32" '
            'href="/statutes/title/09A"'
        )
        assert extract_title_tokens(html) == ["01", "32", "09A"]

    def test_dedupes(self) -> None:
        html = 'href="/statutes/title/01" href="/statutes/title/01"'
        assert extract_title_tokens(html) == ["01"]


class TestExtractChapterTokens:
    def test_filters_by_title(self) -> None:
        html = (
            'href="/statutes/chapter/32/151" '
            'href="/statutes/chapter/01/001"'
        )
        assert extract_chapter_tokens(html, "32") == ["151"]


class TestExtractSectionTokens:
    def test_filters_by_title_and_chapter(self) -> None:
        html = (
            'href="/statutes/section/32/151/05811" '
            'href="/statutes/section/32/151/05812" '
            'href="/statutes/section/32/150/05811"'
        )
        tokens = extract_section_tokens(html, "32", "151")
        assert tokens == ["05811", "05812"]

    def test_dedupes(self) -> None:
        html = (
            'href="/statutes/section/32/151/05811" '
            'href="/statutes/section/32/151/05811"'
        )
        assert extract_section_tokens(html, "32", "151") == ["05811"]


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = VSAStatutesScraper(generation_date=date(2026, 4, 20))
        assert scraper.jurisdiction == "us-vt"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "V.S.A."

    def test_section_ref_frozen(self) -> None:
        ref = VTSectionRef(title="32", chapter="151", section="05811")
        assert ref.title == "32"


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-vt",
            doc_type="statute",
            authority_code="V.S.A.",
            work_number=work_number,
            citation=f"V.S.A. {work_number}",
            heading="H",
            body="B",
            author_id="vt-legislature",
            author_name="VT",
            author_url="https://legislature.vermont.gov",
            generation_date=date(2026, 4, 20),
        )

    def test_nests_by_title(self) -> None:
        scraper = VSAStatutesScraper()
        rel = scraper.relative_output_path(self._section("32-5811"))
        assert rel == Path("us-vt/statute/ch-32/ch-32-sec-32-5811.xml")

    def test_alpha_title(self) -> None:
        scraper = VSAStatutesScraper()
        rel = scraper.relative_output_path(self._section("09A-101"))
        assert rel == Path("us-vt/statute/ch-09A/ch-09A-sec-09A-101.xml")
