"""Offline parse tests for the Oregon ORS scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_or.statutes.scrape import (
    ORSStatutesScraper,
    _chapter_key,
    _section_chapter_token,
    enumerate_chapter_files,
    split_sections,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="cp1252")


class TestSplitSections:
    def test_parses_real_ch_1(self) -> None:
        sections = split_sections(_fixture("ors001.html"))
        assert len(sections) >= 3
        for section, _heading, _body in sections:
            assert section.startswith("1.")

    def test_basic_section_extraction(self) -> None:
        # Real ORS pages use \xa0 (non-breaking space char), not &nbsp; entity;
        # Word export decodes entities to characters.
        html = (
            "<b><span style='font-family:\"Times New Roman\",serif'>"
            "\xa0\xa0 1.020 Contempt of court."
            "</span></b>"
            "<p>The power of a court to punish for contempt shall not be "
            "construed to extend to ordinary conversation.</p>"
            "<b><span>Note: next thing</span></b>"
        )
        sections = split_sections(html)
        assert len(sections) == 1
        section, heading, body = sections[0]
        assert section == "1.020"
        assert heading == "Contempt of court"
        assert "construed to extend" in body

    def test_strips_trailing_session_law_brackets(self) -> None:
        html = (
            "<b><span> 1.030 Heading.</span></b>"
            "<p>Body text. [1981 c.1 §4; 1995 c.658 §7]</p>"
            "<b><span> 1.040 Next.</span></b>"
            "<p>Other.</p>"
        )
        sections = split_sections(html)
        body = sections[0][2]
        assert "Body text." in body
        assert "1981" not in body
        assert "1995" not in body

    def test_heading_strips_trailing_period(self) -> None:
        html = "<b><span> 1.050 Sample heading.</span></b><p>Body.</p>"
        assert split_sections(html)[0][1] == "Sample heading"

    def test_body_strips_leading_period(self) -> None:
        html = "<b><span> 1.060 H.</span></b>.<p>Real body.</p>"
        assert split_sections(html)[0][2].startswith("Real body")

    def test_alpha_chapter_section(self) -> None:
        html = "<b><span> 285A.010 Heading.</span></b><p>Body.</p>"
        assert split_sections(html)[0][0] == "285A.010"


class TestChapterKey:
    def test_strips_leading_zeros(self) -> None:
        assert _chapter_key("ors001.html") == "1"

    def test_preserves_alpha_suffix(self) -> None:
        assert _chapter_key("ors285A.html") == "285A"

    def test_three_digit(self) -> None:
        assert _chapter_key("ors244.html") == "244"


class TestSectionChapterToken:
    def test_basic(self) -> None:
        assert _section_chapter_token("1.020") == "1"

    def test_alpha(self) -> None:
        assert _section_chapter_token("285A.050") == "285A"

    def test_three_digit(self) -> None:
        assert _section_chapter_token("244.050") == "244"


class TestEnumerateChapterFiles:
    def test_includes_chapter_1(self) -> None:
        files = enumerate_chapter_files()
        assert "ors001.html" in files

    def test_includes_alpha_suffixed(self) -> None:
        assert "ors285A.html" in enumerate_chapter_files()

    def test_max_chapter_covered(self) -> None:
        assert "ors840.html" in enumerate_chapter_files()


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = ORSStatutesScraper(generation_date=date(2026, 4, 20))
        assert scraper.jurisdiction == "us-or"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "ORS"

    def test_parse_section_from_cache(self) -> None:
        scraper = ORSStatutesScraper()
        scraper._cache[("1", "1.020")] = ("Contempt", "Body.")
        sec = scraper.parse_section(("1", "1.020"))
        assert sec is not None
        assert sec.work_number == "1.020"
        assert sec.citation == "ORS 1.020"

    def test_parse_section_miss_returns_none(self) -> None:
        scraper = ORSStatutesScraper()
        assert scraper.parse_section(("1", "1.020")) is None


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-or",
            doc_type="statute",
            authority_code="ORS",
            work_number=work_number,
            citation=f"ORS {work_number}",
            heading="H",
            body="B",
            author_id="or-legislature",
            author_name="OR",
            author_url="https://www.oregonlegislature.gov",
            generation_date=date(2026, 4, 20),
        )

    def test_numeric_chapter(self) -> None:
        scraper = ORSStatutesScraper()
        rel = scraper.relative_output_path(self._section("1.020"))
        assert rel == Path("us-or/statutes/ch-1/ch-1-sec-1.020.xml")

    def test_alpha_chapter(self) -> None:
        scraper = ORSStatutesScraper()
        rel = scraper.relative_output_path(self._section("285A.050"))
        assert rel == Path("us-or/statutes/ch-285A/ch-285A-sec-285A.050.xml")
