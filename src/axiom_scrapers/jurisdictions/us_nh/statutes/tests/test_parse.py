"""Offline parse tests for the New Hampshire RSA scraper.

Uses inline HTML fixtures matching the shape documented in the atlas
source (the live server was rate-limiting/timing-out at port time).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_nh.statutes.scrape import (
    NHSectionRef,
    RSAStatutesScraper,
    extract_chapter_pairs,
    extract_section_tokens,
    extract_title_names,
    parse_section_page,
)

SECTION_FIXTURE = """<html>
<body>
<center><h1>TITLE I<br>THE STATE AND ITS GOVERNMENT</h1></center>
<center><h2>CHAPTER 1<br>STATE BOUNDARIES</h2></center>
<center><h3>Section 1:1</h3></center>
&nbsp;&nbsp;&nbsp;<b> 1:1 Perambulation of Boundary Lines &#150;</b>
<codesect>
The boundary lines between the state of New Hampshire and the adjoining states
shall be perambulated once in every seven years.
</codesect>
<sourcenote>
<p><b>Source.</b> 2000, 35:1, eff. Jan. 1, 2001.</p>
</sourcenote>
</body>
</html>"""

TITLE_TOC_FIXTURE = """<html>
<a href="NHTOC/NHTOC-I.htm">Title I</a>
<a href="NHTOC/NHTOC-II.htm">Title II</a>
<a href="NHTOC/NHTOC-LXIV.htm">Title LXIV</a>
<a href="NHTOC/NHTOC-I.htm">Title I (repeat)</a>
</html>"""

CHAPTER_TOC_FIXTURE = """<html>
<a href="NHTOC-I-1.htm">Chapter 1</a>
<a href="NHTOC-I-1-A.htm">Chapter 1-A</a>
<a href="NHTOC-I-1.htm">Chapter 1 (dup)</a>
<a href="NHTOC-II-5.htm">Chapter 5 in Title II</a>
</html>"""

SECTION_TOC_FIXTURE = """<html>
<a href="../I/1/1-1.htm">1:1</a>
<a href="../I/1/1-14.htm">1:14</a>
<a href="../I/1/1-14-a.htm">1:14-a</a>
<a href="../I/1/1-mrg.htm">legend</a>
<a href="../I/1/1-1.htm">1:1 (dup)</a>
</html>"""


class TestParseSectionPage:
    def test_basic_extraction(self) -> None:
        heading, body = parse_section_page(SECTION_FIXTURE)
        assert "Perambulation of Boundary Lines" in heading
        assert "boundary lines" in body.lower()
        assert "Source" not in body  # sourcenote stripped

    def test_returns_empty_when_no_codesect(self) -> None:
        assert parse_section_page("<html>no codesect here</html>") == ("", "")

    def test_heading_strips_trailing_period(self) -> None:
        html = "<b> 1:1 Sample heading. &#150;</b><codesect>body</codesect>"
        heading, _ = parse_section_page(html)
        assert heading == "Sample heading"

    def test_em_dash_separator_accepted(self) -> None:
        html = "<b> 1:1 Em dash variant \u2014</b><codesect>body</codesect>"
        heading, _ = parse_section_page(html)
        assert heading == "Em dash variant"


class TestExtractTitleNames:
    def test_extracts_roman_numerals(self) -> None:
        assert extract_title_names(TITLE_TOC_FIXTURE) == ["I", "II", "LXIV"]

    def test_empty_toc(self) -> None:
        assert extract_title_names("<html></html>") == []


class TestExtractChapterPairs:
    def test_filters_by_title(self) -> None:
        pairs = extract_chapter_pairs(CHAPTER_TOC_FIXTURE, "I")
        assert pairs == [("I", "1"), ("I", "1-A")]

    def test_dedupes(self) -> None:
        html = 'href="NHTOC-I-1.htm" href="NHTOC-I-1.htm"'
        assert extract_chapter_pairs(html, "I") == [("I", "1")]

    def test_filters_out_other_titles(self) -> None:
        assert extract_chapter_pairs(CHAPTER_TOC_FIXTURE, "II") == [("II", "5")]


class TestExtractSectionTokens:
    def test_filters_and_dedupes(self) -> None:
        tokens = extract_section_tokens(SECTION_TOC_FIXTURE, "I", "1")
        assert tokens == ["1", "14", "14-a"]

    def test_drops_mrg_marginal_placeholder(self) -> None:
        assert (
            "mrg" not in extract_section_tokens(SECTION_TOC_FIXTURE, "I", "1")
        )


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = RSAStatutesScraper(generation_date=date(2026, 4, 20))
        assert scraper.jurisdiction == "us-nh"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "RSA"

    def test_section_ref_frozen(self) -> None:
        ref = NHSectionRef(title="I", chapter="1", section="1")
        assert ref.title == "I"


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-nh",
            doc_type="statute",
            authority_code="RSA",
            work_number=work_number,
            citation=f"RSA {work_number}",
            heading="H",
            body="B",
            author_id="nh-legislature",
            author_name="NH",
            author_url="https://www.gencourt.state.nh.us",
            generation_date=date(2026, 4, 20),
        )

    def test_numeric_chapter(self) -> None:
        scraper = RSAStatutesScraper()
        rel = scraper.relative_output_path(self._section("1-1"))
        assert rel == Path("us-nh/statute/ch-1/ch-1-sec-1-1.xml")

    def test_alpha_suffix_chapter(self) -> None:
        scraper = RSAStatutesScraper()
        rel = scraper.relative_output_path(self._section("1-A-5"))
        assert rel == Path("us-nh/statute/ch-1-A/ch-1-A-sec-1-A-5.xml")

    def test_dashed_section_token(self) -> None:
        scraper = RSAStatutesScraper()
        rel = scraper.relative_output_path(self._section("1-14-a"))
        assert rel == Path("us-nh/statute/ch-1/ch-1-sec-1-14-a.xml")
