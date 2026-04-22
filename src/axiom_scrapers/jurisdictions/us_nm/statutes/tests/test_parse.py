"""Offline parse tests for the New Mexico Statutes Annotated scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_nm.statutes.scrape import (
    NMSAStatutesScraper,
    NMSectionRef,
    _clean_heading,
    _heading_continues,
    _join_body,
    _split_heading_body,
    extract_chapter_items,
    parse_chapter_text,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestExtractChapterItems:
    def test_real_listing_page(self) -> None:
        items = extract_chapter_items(_fixture("chapters_p1.html"))
        assert len(items) >= 10
        item_ids = {i[0] for i in items}
        assert "4340" in item_ids  # Chapter 7 (Taxation) appears in page 1
        # All tokens are either digits or digits+alpha
        for _item_id, token, _title in items:
            assert token and (token.isdigit() or token[:-1].isdigit())

    def test_uppercases_alpha_suffix(self) -> None:
        html = '<a href="/nmos/nmsa/en/item/99/index.do">Chapter 24a - Health Care Code</a>'
        assert extract_chapter_items(html) == [("99", "24A", "Health Care Code")]

    def test_dedupes_by_item_id(self) -> None:
        html = (
            '<a href="/nmos/nmsa/en/item/99/index.do">Chapter 7 - Taxation</a>'
            '<a href="/nmos/nmsa/en/item/99/index.do">Chapter 7 - Taxation</a>'
        )
        assert extract_chapter_items(html) == [("99", "7", "Taxation")]

    def test_ignores_non_chapter_anchors(self) -> None:
        html = (
            '<a href="/nmos/nmsa/en/item/99/index.do">Preamble - NMSA</a>'
            '<a href="/nmos/nmsa/en/item/100/index.do">Chapter 1 - Elections</a>'
        )
        items = extract_chapter_items(html)
        assert items == [("100", "1", "Elections")]

    def test_handles_whitespace_in_title(self) -> None:
        html = '<a href="/nmos/nmsa/en/item/1/index.do">\n  Chapter 17 -\n  Game and  Fish\n</a>'
        assert extract_chapter_items(html) == [("1", "17", "Game and Fish")]


class TestParseChapterText:
    def test_real_excerpt(self) -> None:
        text = _fixture("chapter_17_excerpt.txt")
        results = parse_chapter_text(text, "17")
        assert len(results) >= 3
        # First section in chapter 17 is 17-1-1, Declaration of policy
        first = results[0]
        assert first[0].section_id == "17-1-1"
        assert "Declaration of policy" in first[1]
        assert "policy of the state of New Mexico" in first[2]

    def test_filters_by_chapter(self) -> None:
        # Marker references another chapter; must not be emitted under ``17``
        text = (
            "17-1-1. Sample section.\n"
            "    Body paragraph one.\n"
            "\n"
            "History: Laws 2020, ch. 5, § 1.\n"
            "\n"
            "19-1-1. Different chapter.\n"
            "    Do not emit under chapter 17.\n"
            "\n"
            "History: Laws 2020, ch. 6, § 1.\n"
        )
        results = parse_chapter_text(text, "17")
        assert [r[0].section_id for r in results] == ["17-1-1"]

    def test_emits_repealed_with_empty_body(self) -> None:
        text = "17-1-12. Repealed.\n\nHistory: Laws 1955, ch. 181, § 7.\n"
        results = parse_chapter_text(text, "17")
        assert len(results) == 1
        ref, heading, body = results[0]
        assert ref.section_id == "17-1-12"
        assert heading == "Repealed"
        assert body == ""

    def test_subdivided_section_id(self) -> None:
        text = (
            "17-1-1.1. Definitions. (Effective July 1, 2026.)\n"
            "    A. Terms defined here.\n"
            "\n"
            "History: Laws 2025, ch. 9, § 1.\n"
        )
        results = parse_chapter_text(text, "17")
        assert results[0][0].section == "1.1"
        assert results[0][0].section_id == "17-1-1.1"

    def test_alpha_chapter_token(self) -> None:
        text = "24A-1-1. Short title.\n    This act may be cited.\n\nHistory: 2020.\n"
        results = parse_chapter_text(text, "24A")
        assert len(results) == 1
        assert results[0][0].chapter == "24A"

    def test_body_stops_at_annotations(self) -> None:
        text = (
            "17-1-1. Heading.\n"
            "    Body paragraph.\n"
            "\n"
            "History: Laws 2020, ch. 1, § 1.\n"
            "\n"
            "                  ANNOTATIONS\n"
            "Case law commentary must not leak into the body.\n"
            "\n"
            "17-1-2. Next section.\n"
            "    Next body.\n"
            "\n"
            "History: Laws 2020, ch. 1, § 2.\n"
        )
        results = parse_chapter_text(text, "17")
        bodies = {r[0].section_id: r[2] for r in results}
        assert "Body paragraph." in bodies["17-1-1"]
        assert "commentary" not in bodies["17-1-1"]
        assert bodies["17-1-2"].startswith("Next body.")


class TestSplitHeadingBody:
    def test_simple(self) -> None:
        heading, body = _split_heading_body(
            "Heading text.",
            "\n    Body paragraph one.\n\n    Body paragraph two.\n\nHistory: 2020.\n",
        )
        assert heading == "Heading text"
        assert body == "Body paragraph one.\n\nBody paragraph two."

    def test_bracketed_heading(self) -> None:
        heading, _body = _split_heading_body(
            "[Declaration of policy.]", "\n    It is the purpose.\n\nHistory: 1921.\n"
        )
        assert heading == "Declaration of policy"

    def test_wrapped_parenthetical(self) -> None:
        heading, _body = _split_heading_body(
            "Heading. (Effective July 1,",
            "2027.)\n    Body.\n\nHistory: 2025.\n",
        )
        assert heading == "Heading. (Effective July 1, 2027.)"

    def test_wrapped_semicolon_continuation(self) -> None:
        heading, body = _split_heading_body(
            "General powers;",
            "game protection fund.\n    Body text.\n\nHistory: 1921.\n",
        )
        assert heading == "General powers; game protection fund"
        assert body == "Body text."

    def test_wrapped_bracket_continuation(self) -> None:
        heading, body = _split_heading_body(
            "[Issuance of commissions;",
            "revocation.]\n    Officer commissions.\n\nHistory: 1955.\n",
        )
        assert heading == "Issuance of commissions; revocation"
        assert body == "Officer commissions."


class TestCleanHeading:
    def test_strips_brackets(self) -> None:
        assert _clean_heading("[Heading text.]") == "Heading text"

    def test_strips_trailing_period(self) -> None:
        assert _clean_heading("Heading.") == "Heading"

    def test_preserves_interior_punctuation(self) -> None:
        assert _clean_heading("Heading; clause.") == "Heading; clause"

    def test_collapses_whitespace(self) -> None:
        assert _clean_heading("Heading   with   spaces.") == "Heading with spaces"

    def test_keeps_trailing_parenthetical(self) -> None:
        # The period inside the trailing parenthetical is preserved —
        # only a naked sentence-ending ``.`` at the very end of the
        # string is stripped.
        assert _clean_heading("Heading. (Effective 2026.)") == "Heading. (Effective 2026.)"


class TestHeadingContinues:
    def test_unclosed_paren(self) -> None:
        assert _heading_continues("Heading. (Effective")

    def test_unclosed_bracket(self) -> None:
        assert _heading_continues("[Heading")

    def test_trailing_semicolon(self) -> None:
        assert _heading_continues("General powers;")

    def test_trailing_comma(self) -> None:
        assert _heading_continues("General powers,")

    def test_complete(self) -> None:
        assert not _heading_continues("General powers.")

    def test_empty(self) -> None:
        assert not _heading_continues("   ")


class TestJoinBody:
    def test_folds_softwraps(self) -> None:
        assert _join_body(["Line one,", "wrapped here."]) == "Line one, wrapped here."

    def test_preserves_paragraph_breaks(self) -> None:
        result = _join_body(["First paragraph.", "", "Second paragraph."])
        assert result == "First paragraph.\n\nSecond paragraph."

    def test_collapses_multi_blank_runs(self) -> None:
        result = _join_body(["A.", "", "", "", "B."])
        assert result == "A.\n\nB."

    def test_empty(self) -> None:
        assert _join_body([]) == ""


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = NMSAStatutesScraper(generation_date=date(2026, 4, 21))
        assert scraper.jurisdiction == "us-nm"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "NMSA 1978"
        assert scraper.author_id == "nm-compilation-commission"

    def test_section_ref_frozen(self) -> None:
        ref = NMSectionRef(chapter="17", article="1", section="1")
        assert ref.section_id == "17-1-1"

    def test_section_ref_with_subdivided_section(self) -> None:
        ref = NMSectionRef(chapter="7", article="2", section="1.1")
        assert ref.section_id == "7-2-1.1"

    def test_parse_section_returns_none_for_unknown_ref(self) -> None:
        scraper = NMSAStatutesScraper()
        ref = NMSectionRef(chapter="17", article="1", section="1")
        # Nothing cached → soft-skip
        assert scraper.parse_section(ref) is None

    def test_parse_section_returns_cached(self) -> None:
        scraper = NMSAStatutesScraper(generation_date=date(2026, 4, 21))
        ref = NMSectionRef(chapter="17", article="1", section="1")
        scraper._cache[ref] = ("Declaration of policy", "Body text.")
        section = scraper.parse_section(ref)
        assert section is not None
        assert section.work_number == "17-1-1"
        assert section.citation == "NMSA 1978 § 17-1-1"
        assert section.heading == "Declaration of policy"
        assert section.body == "Body text."


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-nm",
            doc_type="statute",
            authority_code="NMSA 1978",
            work_number=work_number,
            citation=f"NMSA 1978 § {work_number}",
            heading="H",
            body="B",
            author_id="nm-compilation-commission",
            author_name="NMCC",
            author_url="https://www.nmcompcomm.us",
            generation_date=date(2026, 4, 21),
        )

    def test_nests_by_chapter(self) -> None:
        scraper = NMSAStatutesScraper()
        rel = scraper.relative_output_path(self._section("17-1-1"))
        assert rel == Path("us-nm/statutes/ch-17/ch-17-sec-17-1-1.xml")

    def test_alpha_chapter(self) -> None:
        scraper = NMSAStatutesScraper()
        rel = scraper.relative_output_path(self._section("24A-1-3"))
        assert rel == Path("us-nm/statutes/ch-24A/ch-24A-sec-24A-1-3.xml")

    def test_subdivided_section(self) -> None:
        scraper = NMSAStatutesScraper()
        rel = scraper.relative_output_path(self._section("7-2-1.1"))
        assert rel == Path("us-nm/statutes/ch-7/ch-7-sec-7-2-1.1.xml")
