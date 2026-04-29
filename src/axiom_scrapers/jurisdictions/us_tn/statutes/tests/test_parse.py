"""Offline parse tests for the Tennessee Code Annotated scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from axiom_scrapers._common.source_section import SourceSection
from axiom_scrapers._common.testing import install_fake_http
from axiom_scrapers.jurisdictions.us_tn.statutes import scrape
from axiom_scrapers.jurisdictions.us_tn.statutes.scrape import (
    BASE,
    TCAStatutesScraper,
    extract_chapter_tokens,
    extract_section_tokens,
    extract_title_tokens,
    parse_section_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseSectionPage:
    def test_parses_real_section_1_1_103(self) -> None:
        parsed = parse_section_page(_fixture("sec_1_1_103.html"))
        assert parsed is not None
        heading, body = parsed
        assert heading == "Staff Services for Commission"
        assert "office of legal services" in body.lower()

    def test_returns_none_when_no_heading(self) -> None:
        assert parse_section_page("<html>no section here</html>") is None

    def test_returns_none_when_no_body_div(self) -> None:
        html = (
            '<h1 class="heading-1">2021 Tennessee Code<br/>Title 1<br/>'
            "Chapter 1<br/>&sect; 1-1-103. Heading</h1>"
        )
        assert parse_section_page(html) is None

    def test_synthetic_section(self) -> None:
        html = (
            '<h1 class="heading-1">2021 Tennessee Code<br/>'
            "Title 67 - Taxes<br/>"
            "Chapter 8 - Inheritance<br/>"
            "&sect; 67-8-303. Sample heading</h1>"
            '<div id="codes-content">'
            "<p>First paragraph.</p>"
            "<p>Second paragraph.</p>"
            "</div>"
        )
        parsed = parse_section_page(html)
        assert parsed is not None
        heading, body = parsed
        assert heading == "Sample heading"
        assert "First paragraph." in body
        assert "Second paragraph." in body
        # paragraph break is preserved for downstream split_paragraphs
        assert "\n\n" in body

    def test_trailing_period_stripped(self) -> None:
        html = (
            '<h1 class="heading-1">2021<br/>Title 1<br/>Chapter 1<br/>'
            "&sect; 1-1-101. Short Title.</h1>"
            '<div id="codes-content"><p>Body text.</p></div>'
        )
        parsed = parse_section_page(html)
        assert parsed is not None
        assert parsed[0] == "Short Title"


class TestExtractTitleTokens:
    def test_real_code_root(self) -> None:
        tokens = extract_title_tokens(_fixture("code_root_2021.html"))
        # TCA has 68 active titles in the 2021 snapshot.
        assert len(tokens) >= 60
        assert "1" in tokens
        assert "67" in tokens  # tax title

    def test_dedupes(self) -> None:
        html = 'href="/codes/tennessee/2021/title-1/" href="/codes/tennessee/2021/title-1/"'
        assert extract_title_tokens(html) == ["1"]

    def test_ignores_other_states(self) -> None:
        html = (
            'href="/codes/tennessee/2021/title-1/" '
            'href="/codes/virginia/2021/title-99/"'
        )
        assert extract_title_tokens(html) == ["1"]


class TestExtractChapterTokens:
    def test_real_title1_toc(self) -> None:
        tokens = extract_chapter_tokens(_fixture("title1_toc.html"), "1")
        assert "1" in tokens
        assert len(tokens) >= 3

    def test_filters_by_title(self) -> None:
        html = (
            'href="/codes/tennessee/2021/title-1/chapter-1/" '
            'href="/codes/tennessee/2021/title-2/chapter-1/"'
        )
        assert extract_chapter_tokens(html, "1") == ["1"]


class TestExtractSectionTokens:
    def test_real_title1_ch1_toc(self) -> None:
        tokens = extract_section_tokens(_fixture("title1_ch1_toc.html"), "1", "1")
        assert "1-1-101" in tokens
        assert len(tokens) >= 10

    def test_filters_by_title_and_chapter(self) -> None:
        html = (
            'href="/codes/tennessee/2021/title-1/chapter-1/section-1-1-101/" '
            'href="/codes/tennessee/2021/title-1/chapter-2/section-1-2-101/" '
            'href="/codes/tennessee/2021/title-2/chapter-1/section-2-1-101/"'
        )
        assert extract_section_tokens(html, "1", "1") == ["1-1-101"]

    def test_accepts_dotted_section_ids(self) -> None:
        html = (
            'href="/codes/tennessee/2021/title-1/chapter-2/section-1-2-112.5/"'
        )
        assert extract_section_tokens(html, "1", "2") == ["1-2-112.5"]


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = TCAStatutesScraper(generation_date=date(2026, 4, 21))
        assert scraper.jurisdiction == "us-tn"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "Tenn. Code Ann."

    def test_section_ref_shape(self) -> None:
        # list_sections yields (title, chapter, section) tuples; just
        # confirm the shape is iterable and unpacking works.
        ref: tuple[str, str, str] = ("1", "1", "1-1-101")
        title, chapter, section = ref
        assert (title, chapter, section) == ("1", "1", "1-1-101")


class TestOutputPath:
    def _section(self, work_number: str) -> SourceSection:
        return SourceSection(
            jurisdiction="us-tn",
            doc_type="statute",
            authority_code="Tenn. Code Ann.",
            work_number=work_number,
            citation=f"Tenn. Code Ann. § {work_number}",
            heading="H",
            body="B",
            author_id="tn-legislature",
            author_name="TN",
            author_url="https://www.capitol.tn.gov",
            generation_date=date(2026, 4, 21),
        )

    def test_nests_by_title(self) -> None:
        scraper = TCAStatutesScraper()
        rel = scraper.relative_output_path(self._section("1-1-101"))
        assert rel == Path("us-tn/statutes/title-1/title-1-sec-1-1-101.txt")

    def test_multi_digit_title(self) -> None:
        scraper = TCAStatutesScraper()
        rel = scraper.relative_output_path(self._section("67-8-303"))
        assert rel == Path("us-tn/statutes/title-67/title-67-sec-67-8-303.txt")


class TestCrawlLayer:
    """Mock ``http_get`` to cover the URL-walker + per-section fetch path."""

    def test_list_sections_walks_three_levels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = 'href="/codes/tennessee/2021/title-1/"'
        title1 = 'href="/codes/tennessee/2021/title-1/chapter-1/"'
        chapter1 = (
            'href="/codes/tennessee/2021/title-1/chapter-1/section-1-1-101/" '
            'href="/codes/tennessee/2021/title-1/chapter-1/section-1-1-102/"'
        )
        install_fake_http(
            monkeypatch,
            scrape,
            {
                f"{BASE}/": root,
                f"{BASE}/title-1/": title1,
                f"{BASE}/title-1/chapter-1/": chapter1,
            },
        )
        refs = list(TCAStatutesScraper().list_sections())
        assert refs == [("1", "1", "1-1-101"), ("1", "1", "1-1-102")]

    def test_list_sections_soft_fails_on_missing_root(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_http(monkeypatch, scrape, {f"{BASE}/": None})
        assert list(TCAStatutesScraper().list_sections()) == []

    def test_list_chapters_empty_when_title_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_http(monkeypatch, scrape, {f"{BASE}/title-99/": None})
        assert scrape._list_chapters("99") == []

    def test_list_sections_empty_when_chapter_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_http(
            monkeypatch, scrape, {f"{BASE}/title-1/chapter-9/": None}
        )
        assert scrape._list_sections("1", "9") == []

    def test_parse_section_soft_fails_on_missing_fetch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        url = f"{BASE}/title-1/chapter-1/section-1-1-101/"
        install_fake_http(monkeypatch, scrape, {url: None})
        assert TCAStatutesScraper().parse_section(("1", "1", "1-1-101")) is None

    def test_parse_section_returns_section_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        html = (
            '<h1 class="heading-1">2021 Tennessee Code<br/>'
            "Title 1<br/>Chapter 1<br/>"
            "&sect; 1-1-101. Short Title</h1>"
            '<div id="codes-content"><p>Body text here.</p></div>'
        )
        url = f"{BASE}/title-1/chapter-1/section-1-1-101/"
        install_fake_http(monkeypatch, scrape, {url: html})
        scraper = TCAStatutesScraper(generation_date=date(2026, 4, 21))
        sec = scraper.parse_section(("1", "1", "1-1-101"))
        assert sec is not None
        assert sec.work_number == "1-1-101"
        assert sec.heading == "Short Title"
        assert sec.citation == "Tenn. Code Ann. § 1-1-101"
        assert "Body text" in sec.body

    def test_parse_section_returns_none_when_body_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        html = (
            '<h1 class="heading-1">2021 Tennessee Code<br/>'
            "Title 1<br/>Chapter 1<br/>"
            "&sect; 1-1-101. Reserved</h1>"
            '<div id="codes-content">   </div>'
        )
        url = f"{BASE}/title-1/chapter-1/section-1-1-101/"
        install_fake_http(monkeypatch, scrape, {url: html})
        assert (
            TCAStatutesScraper().parse_section(("1", "1", "1-1-101")) is None
        )

    def test_parse_section_returns_none_when_heading_unparseable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No ``<h1 class="heading-1">`` — parse_section_page returns None,
        # which parse_section bubbles up.
        html = '<div id="codes-content"><p>Orphan body.</p></div>'
        url = f"{BASE}/title-1/chapter-1/section-1-1-101/"
        install_fake_http(monkeypatch, scrape, {url: html})
        assert (
            TCAStatutesScraper().parse_section(("1", "1", "1-1-101")) is None
        )
