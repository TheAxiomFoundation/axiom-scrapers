"""Offline parse tests for the Arkansas Code scraper."""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import pytest

from axiom_scrapers._common.akn import Section
from axiom_scrapers._common.testing import install_fake_http
from axiom_scrapers.jurisdictions.us_ar.statutes import scrape
from axiom_scrapers.jurisdictions.us_ar.statutes.scrape import (
    ARCodeStatutesScraper,
    ARSectionRef,
    parse_title_xml,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseTitleXml:
    def test_parses_real_title1_chapter1(self) -> None:
        sections = parse_title_xml(_fixture("title_01_ch1.xml"))
        ids = [s[0] for s in sections]
        assert ids == ["1-1-101", "1-1-102"]

    def test_real_section_has_expected_heading_and_body(self) -> None:
        sections = dict(
            (sid, (heading, body))
            for sid, heading, body in parse_title_xml(_fixture("title_01_ch1.xml"))
        )
        heading, body = sections["1-1-101"]
        assert heading == "Extension of western boundary line"
        assert "western boundary line of the State of Arkansas" in body
        # HISTORY block is preserved inline as the trailing paragraph.
        assert "HISTORY:" in body
        assert "Acts 1905, No. 41" in body

    def test_body_preserves_paragraph_breaks(self) -> None:
        """Adjacent ``<br><br>`` in source survives as a ``\\n\\n`` gap."""
        sections = dict(
            (sid, (heading, body))
            for sid, heading, body in parse_title_xml(_fixture("title_01_ch1.xml"))
        )
        _, body = sections["1-1-102"]
        # Section 1-1-102 is a multi-paragraph numbered list; at least one
        # \n\n must survive so build_akn_xml emits multiple <p> elements.
        assert "\n\n" in body

    def test_handles_alphanumeric_chapter_and_deep_nesting(self) -> None:
        """Section 4-2A-101 lives at Level 5 (Title → Chapter → Subchapter → Part)."""
        ids = [s[0] for s in parse_title_xml(_fixture("title_synthetic.xml"))]
        assert "4-2A-101" in ids

    def test_skips_range_captions(self) -> None:
        """Reserved/repealed ranges shouldn't produce a section."""
        ids = [s[0] for s in parse_title_xml(_fixture("title_synthetic.xml"))]
        assert not any("--" in sid for sid in ids)
        assert "4-99-100" not in ids

    def test_skips_empty_content(self) -> None:
        """Sections with no body text are dropped by parse_title_xml."""
        ids = [s[0] for s in parse_title_xml(_fixture("title_synthetic.xml"))]
        assert "4-99-200" not in ids

    def test_rejects_list_captions(self) -> None:
        xml = (
            '<?xml version="1.0"?><Content><Indexes>'
            '<Index Level="3" HasChildren="0">'
            "<Caption>Section 1-1-101, 1-1-102</Caption>"
            "<Description>[Repealed.]</Description>"
            "<Content>&lt;br&gt;repealed</Content>"
            "</Index>"
            "</Indexes></Content>"
        )
        assert parse_title_xml(xml) == []

    def test_heading_trailing_period_stripped(self) -> None:
        xml = (
            '<?xml version="1.0"?><Content><Indexes>'
            '<Index Level="3" HasChildren="0">'
            "<Caption>Section 1-1-101</Caption>"
            "<Description>Heading.</Description>"
            "<Content>&lt;br&gt;body.</Content>"
            "</Index>"
            "</Indexes></Content>"
        )
        sections = parse_title_xml(xml)
        assert sections == [("1-1-101", "Heading", "body.")]


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = ARCodeStatutesScraper(generation_date=date(2026, 4, 21))
        assert scraper.jurisdiction == "us-ar"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "Ark. Code Ann."
        assert scraper.author_url == "https://www.arkleg.state.ar.us"

    def test_section_ref_frozen(self) -> None:
        ref = ARSectionRef(title="01", section="1-1-101")
        with pytest.raises(dataclasses.FrozenInstanceError):
            ref.title = "02"  # type: ignore[misc]

    def test_parse_section_from_cache(self) -> None:
        scraper = ARCodeStatutesScraper(generation_date=date(2026, 4, 21))
        ref = ARSectionRef(title="01", section="1-1-101")
        scraper._cache[ref] = ("Extension of western boundary line", "Body.")
        sec = scraper.parse_section(ref)
        assert sec is not None
        assert sec.work_number == "1-1-101"
        assert sec.citation == "Ark. Code Ann. \u00a7 1-1-101"
        assert sec.heading == "Extension of western boundary line"

    def test_parse_section_unknown_ref_returns_none(self) -> None:
        scraper = ARCodeStatutesScraper()
        assert scraper.parse_section(ARSectionRef(title="01", section="9-9-999")) is None


class TestCrawlLayer:
    """End-to-end list_sections via install_fake_http."""

    def test_list_sections_fetches_every_title_and_populates_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Keep the test fast: trim to two titles. Both URLs must be
        # requested exactly once and the cache populated for every
        # parsed section.
        real = _fixture("title_01_ch1.xml")
        synth = _fixture("title_synthetic.xml")
        scraper = ARCodeStatutesScraper(generation_date=date(2026, 4, 21))
        scraper._titles = ("01", "04")
        calls = install_fake_http(
            monkeypatch,
            scrape,
            {
                f"{scrape.BASE}/ar.code.01.2012.xml": real,
                f"{scrape.BASE}/ar.code.04.2012.xml": synth,
            },
        )

        refs = list(scraper.list_sections())

        section_ids = [r.section for r in refs]
        assert section_ids == ["1-1-101", "1-1-102", "4-2A-101"]
        assert calls == [
            f"{scrape.BASE}/ar.code.01.2012.xml",
            f"{scrape.BASE}/ar.code.04.2012.xml",
        ]
        # Cache must hold a (heading, body) entry for every yielded ref.
        for ref in refs:
            assert ref in scraper._cache
            heading, body = scraper._cache[ref]
            assert body

    def test_list_sections_skips_titles_that_fail_to_fetch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scraper = ARCodeStatutesScraper(generation_date=date(2026, 4, 21))
        scraper._titles = ("01",)
        install_fake_http(
            monkeypatch,
            scrape,
            {f"{scrape.BASE}/ar.code.01.2012.xml": None},
        )
        assert list(scraper.list_sections()) == []

    def test_list_sections_skips_titles_with_empty_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scraper = ARCodeStatutesScraper(generation_date=date(2026, 4, 21))
        scraper._titles = ("01",)
        install_fake_http(
            monkeypatch,
            scrape,
            {f"{scrape.BASE}/ar.code.01.2012.xml": ""},
        )
        assert list(scraper.list_sections()) == []

    def test_parse_section_after_list_yields_full_section(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scraper = ARCodeStatutesScraper(generation_date=date(2026, 4, 21))
        scraper._titles = ("01",)
        install_fake_http(
            monkeypatch,
            scrape,
            {f"{scrape.BASE}/ar.code.01.2012.xml": _fixture("title_01_ch1.xml")},
        )
        refs = list(scraper.list_sections())
        sec = scraper.parse_section(refs[0])
        assert sec is not None
        assert sec.jurisdiction == "us-ar"
        assert sec.work_number == "1-1-101"
        assert sec.heading == "Extension of western boundary line"


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-ar",
            doc_type="statute",
            authority_code="Ark. Code Ann.",
            work_number=work_number,
            citation=f"Ark. Code Ann. \u00a7 {work_number}",
            heading="H",
            body="B",
            author_id="ar-legislature",
            author_name="AR",
            author_url="https://www.arkleg.state.ar.us",
            generation_date=date(2026, 4, 21),
        )

    def test_nests_by_title(self) -> None:
        scraper = ARCodeStatutesScraper()
        rel = scraper.relative_output_path(self._section("1-1-101"))
        assert rel == Path("us-ar/statutes/title-1/title-1-sec-1-1-101.xml")

    def test_alphanumeric_chapter(self) -> None:
        scraper = ARCodeStatutesScraper()
        rel = scraper.relative_output_path(self._section("4-2A-101"))
        assert rel == Path("us-ar/statutes/title-4/title-4-sec-4-2A-101.xml")

    def test_double_digit_title(self) -> None:
        scraper = ARCodeStatutesScraper()
        rel = scraper.relative_output_path(self._section("26-51-2405"))
        assert rel == Path("us-ar/statutes/title-26/title-26-sec-26-51-2405.xml")
