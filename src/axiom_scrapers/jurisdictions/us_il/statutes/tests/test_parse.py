"""Offline tests for IL parser.

Fixtures are real section HTML saved under ``fixtures/``. No live HTTP
in these tests — the subclass's :meth:`parse_section` is exercised via
the pure helper :func:`_parse_section_html`. End-to-end fetch behavior
is covered by the shared base-class tests; crawl-layer helpers
(``_list_*``, ``_fetch_text``) are covered via a mock ``http_get``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from axiom_scrapers.jurisdictions.us_il.statutes import scrape
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


class _FakeResponse:
    """Minimal stand-in for ``FetchResult`` in crawl-layer tests."""

    def __init__(self, body: str) -> None:
        self._body = body

    def text(self, _fallback: str = "utf-8") -> str:
        return self._body


def _install_fake_http(
    monkeypatch: pytest.MonkeyPatch, responses: dict[str, str | None]
) -> list[str]:
    """Replace ``scrape.http_get`` with a stub that returns ``responses[url]``.

    Records every URL fetched in the returned list (call-order preserved).
    ``None`` values simulate a 404 / soft-fail. Unknown URLs raise so tests
    surface unexpected crawls.
    """
    calls: list[str] = []

    def fake_http_get(url: str, **_kwargs: Any) -> _FakeResponse | None:
        calls.append(url)
        if url not in responses:
            raise AssertionError(f"unexpected URL fetched: {url}")
        body = responses[url]
        return None if body is None else _FakeResponse(body)

    monkeypatch.setattr(scrape, "http_get", fake_http_get)
    return calls


class TestCrawlLayer:
    """Exercise ``_list_*`` + ``_fetch_text`` + ``list_sections`` via mock HTTP."""

    def test_fetch_text_returns_body_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_http(monkeypatch, {"https://x.test/": "hello"})
        assert scrape._fetch_text("https://x.test/") == "hello"

    def test_fetch_text_returns_empty_on_soft_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_http(monkeypatch, {"https://x.test/": None})
        assert scrape._fetch_text("https://x.test/") == ""

    def test_list_chapter_hrefs_filters_to_chapter_dirs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root_listing = (
            '<A HREF="/ftp/">[To Parent Directory]</A>'
            '<A HREF="/ftp/ILCS/Ch%200005/">Ch 0005</A>'
            '<A HREF="/ftp/ILCS/Ch%200010/">Ch 0010</A>'
            '<A HREF="/ftp/ILCS/README.txt">README.txt</A>'
            '<A HREF="/ftp/ILCS/Act%209999/">Act 9999</A>'
        )
        _install_fake_http(monkeypatch, {f"{scrape.BASE}/": root_listing})
        hrefs = scrape._list_chapter_hrefs()
        assert hrefs == ["/ftp/ILCS/Ch%200005/", "/ftp/ILCS/Ch%200010/"]

    def test_list_act_hrefs_filters_to_act_dirs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # IIS listings emit %20-encoded spaces in href values; the crawl layer
        # passes those through verbatim when constructing fetch URLs.
        chapter_html = (
            '<A HREF="/ftp/">[To Parent]</A>'
            '<A HREF="/ftp/ILCS/Ch%200035/Act%200155/">Act 0155</A>'
            '<A HREF="/ftp/ILCS/Ch%200035/ActUNKNOWN.html">ActUNKNOWN.html</A>'
        )
        _install_fake_http(
            monkeypatch,
            {"https://www.ilga.gov/ftp/ILCS/Ch%200035/": chapter_html},
        )
        hrefs = scrape._list_act_hrefs("/ftp/ILCS/Ch%200035/")
        assert hrefs == ["/ftp/ILCS/Ch%200035/Act%200155/"]

    def test_list_section_urls_accepts_well_formed_files(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        act_html = (
            '<A HREF="/ftp/">[Parent]</A>'
            '<A HREF="/ftp/ILCS/Ch%200035/Act%200155/035015500K2.html">'
            "035015500K2.html</A>"
            '<A HREF="/ftp/ILCS/Ch%200035/Act%200155/index.html">index.html</A>'
            '<A HREF="/ftp/ILCS/Ch%200035/Act%200155/subdir/">subdir/</A>'
        )
        _install_fake_http(
            monkeypatch,
            {"https://www.ilga.gov/ftp/ILCS/Ch%200035/Act%200155/": act_html},
        )
        urls = scrape._list_section_urls("/ftp/ILCS/Ch%200035/Act%200155/")
        assert urls == [
            "https://www.ilga.gov/ftp/ILCS/Ch%200035/Act%200155/035015500K2.html",
        ]

    def test_list_sections_walks_three_levels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: chapters → acts → section files via fake HTTP."""
        root = '<A HREF="/ftp/ILCS/Ch%200035/">Ch 0035</A>'
        chapter = (
            '<A HREF="/ftp/ILCS/Ch%200035/Act%200155/">Act 0155</A>'
        )
        act = (
            '<A HREF="/ftp/ILCS/Ch%200035/Act%200155/035015500K2.html">'
            "035015500K2.html</A>"
        )
        _install_fake_http(
            monkeypatch,
            {
                f"{scrape.BASE}/": root,
                "https://www.ilga.gov/ftp/ILCS/Ch%200035/": chapter,
                "https://www.ilga.gov/ftp/ILCS/Ch%200035/Act%200155/": act,
            },
        )
        refs = list(ILCSStatutesScraper().list_sections())
        assert refs == [
            "https://www.ilga.gov/ftp/ILCS/Ch%200035/Act%200155/035015500K2.html",
        ]

    def test_parse_section_soft_fails_on_missing_fetch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_http(monkeypatch, {"https://x.test/": None})
        scraper = ILCSStatutesScraper()
        assert scraper.parse_section("https://x.test/") is None

    def test_parse_section_returns_section_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        html = (
            "<p>(35 ILCS 155/2)<br>"
            "Sec. 2.  Definitions. As used in this Act:<br>"
            "(a) The term &quot;Department&quot; means ...<br>"
            "(Source: P.A. 100-0001.)</p>"
        )
        _install_fake_http(monkeypatch, {"https://x.test/sec.html": html})
        sec = ILCSStatutesScraper().parse_section("https://x.test/sec.html")
        assert sec is not None
        assert sec.work_number == "35-155-2"
        assert sec.heading == "Definitions"
