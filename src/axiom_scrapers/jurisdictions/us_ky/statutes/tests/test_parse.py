"""Offline parse tests for the Kentucky Revised Statutes scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_ky.statutes.scrape import (
    KRSStatutesScraper,
    KYSectionRef,
    extract_chapter_anchors,
    extract_section_anchors,
    parse_pdf_body,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestExtractChapterAnchors:
    def test_real_master_toc_excerpt(self) -> None:
        anchors = extract_chapter_anchors(_fixture("master_toc.html"))
        assert len(anchors) >= 5  # many chapters in the excerpt
        for token, cid in anchors:
            assert token
            assert cid.isdigit()

    def test_dedupes_by_token(self) -> None:
        html = (
            '<a class="chapter" href="chapter.aspx?id=1">CHAPTER 1 General</a>'
            '<a class="chapter" href="chapter.aspx?id=1">CHAPTER 1 General</a>'
        )
        assert extract_chapter_anchors(html) == [("1", "1")]

    def test_handles_alpha_suffix_tokens(self) -> None:
        html = '<a class="chapter" href="chapter.aspx?id=99">CHAPTER 6A Admin</a>'
        assert extract_chapter_anchors(html) == [("6A", "99")]

    def test_uppercases_token(self) -> None:
        html = '<a class="chapter" href="chapter.aspx?id=99">CHAPTER 6a Admin</a>'
        assert extract_chapter_anchors(html)[0][0] == "6A"


class TestExtractSectionAnchors:
    def test_basic(self) -> None:
        html = (
            '<a class="statute" href="statute.aspx?id=100">'
            ".010  Legislative intent.</a>"
        )
        assert extract_section_anchors(html) == [(".010", "100", "Legislative intent")]

    def test_dedupes_by_section_id(self) -> None:
        html = (
            '<a class="statute" href="statute.aspx?id=100">.010  A.</a>'
            '<a class="statute" href="statute.aspx?id=100">.010  A.</a>'
        )
        assert extract_section_anchors(html) == [(".010", "100", "A")]

    def test_heading_strips_trailing_period(self) -> None:
        html = '<a class="statute" href="statute.aspx?id=1">.020  Sample heading.</a>'
        assert extract_section_anchors(html)[0][2] == "Sample heading"


class TestParsePdfBody:
    def test_simple_pdf_extraction(self) -> None:
        text = (
            "1.010  Legislative intent.\n"
            "\n"
            "The General Assembly intends to promote the general welfare.\n"
            "\n"
            "Additional body paragraph.\n"
            "\n"
            "Effective: June 29, 2023\n"
            "History: 2023 ch. 12, sec. 1.\n"
        )
        heading, body = parse_pdf_body(text, "1", ".010")
        assert heading == "Legislative intent"
        assert "General Assembly" in body
        assert "Additional body paragraph" in body
        assert "Effective:" not in body
        assert "History:" not in body

    def test_heading_without_number_prefix(self) -> None:
        text = "Legislative intent.\n\nBody text.\n"
        heading, body = parse_pdf_body(text, "1", ".010")
        assert heading == "Legislative intent"
        assert body == "Body text."

    def test_lrc_note_is_trailer(self) -> None:
        text = (
            "1.010  Heading.\n\n"
            "Body.\n\n"
            "Legislative Research Commission Note (7/15/2024): XYZ\n"
        )
        _, body = parse_pdf_body(text, "1", ".010")
        assert body == "Body."

    def test_catchline_at_repeal_is_trailer(self) -> None:
        text = "1.010  H.\n\nBody.\n\nCatchline at repeal: Legislative intent.\n"
        _, body = parse_pdf_body(text, "1", ".010")
        assert body == "Body."

    def test_empty_body(self) -> None:
        text = "1.010  Heading only.\n\nEffective: whenever\n"
        heading, body = parse_pdf_body(text, "1", ".010")
        assert heading == "Heading only"
        assert body == ""

    def test_multiline_paragraph_collapsed(self) -> None:
        # pdftotext -layout preserves line wraps inside a paragraph; we
        # should join them with a single space.
        text = (
            "1.010  H.\n\n"
            "This is a long paragraph that the\n"
            "PDF extractor split across multiple\n"
            "lines with layout preservation.\n"
        )
        _, body = parse_pdf_body(text, "1", ".010")
        assert body == (
            "This is a long paragraph that the "
            "PDF extractor split across multiple "
            "lines with layout preservation."
        )


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = KRSStatutesScraper(generation_date=date(2026, 4, 20))
        assert scraper.jurisdiction == "us-ky"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "KRS"


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-ky",
            doc_type="statute",
            authority_code="KRS",
            work_number=work_number,
            citation=f"KRS {work_number}",
            heading="H",
            body="B",
            author_id="ky-legislature",
            author_name="KY",
            author_url="https://legislature.ky.gov",
            generation_date=date(2026, 4, 20),
        )

    def test_nests_by_chapter(self) -> None:
        scraper = KRSStatutesScraper()
        rel = scraper.relative_output_path(self._section("1.010"))
        assert rel == Path("us-ky/statutes/ch-1/ch-1-sec-1.010.xml")

    def test_alpha_suffix_chapter(self) -> None:
        scraper = KRSStatutesScraper()
        rel = scraper.relative_output_path(self._section("6A.100"))
        assert rel == Path("us-ky/statutes/ch-6A/ch-6A-sec-6A.100.xml")


class TestSectionRefDataclass:
    def test_is_frozen(self) -> None:
        ref = KYSectionRef(chapter="1", section_dot=".010", section_id="100", toc_heading="H")
        assert ref.chapter == "1"
