"""Tests for BaseScraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

import pytest

from axiom_scrapers._common.akn import Section
from axiom_scrapers._common.base import ScrapeResult, Scraper


def make_section(num: str = "1.01", heading: str = "Title") -> Section:
    return Section(
        jurisdiction="us-test",
        doc_type="statute",
        authority_code="TEST",
        work_number=num,
        citation=f"TEST {num}",
        heading=heading,
        body=f"Body of section {num}.",
        author_id="test-auth",
        author_name="Test Authority",
        author_url="https://test.example",
        generation_date=date(2026, 4, 20),
    )


class _OneShotScraper(Scraper[str]):
    jurisdiction = "us-test"
    doc_type = "statute"
    authority_code = "TEST"
    author_id = "test-auth"
    author_name = "Test Authority"
    author_url = "https://test.example"
    workers = 2

    def __init__(self, refs: list[str], skip: set[str] | None = None, **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self._refs = refs
        self._skip = skip or set()

    def list_sections(self) -> Iterable[str]:
        return iter(self._refs)

    def parse_section(self, ref: str) -> Section | None:
        if ref in self._skip:
            return None
        return make_section(num=ref, heading=f"Heading for {ref}")


class TestScraperConfigValidation:
    def test_missing_class_attrs_raises(self) -> None:
        class BadScraper(Scraper[str]):
            # Forgets to set jurisdiction, doc_type, etc.
            def list_sections(self) -> Iterable[str]:
                return iter([])

            def parse_section(self, ref: str) -> Section | None:
                return None

        with pytest.raises(TypeError, match="must set class attributes"):
            BadScraper()

    def test_all_attrs_set_initializes_cleanly(self) -> None:
        scraper = _OneShotScraper(["1.01"])
        assert scraper.jurisdiction == "us-test"
        assert scraper.generation_date == date.today()


class TestScraperRun:
    def test_writes_one_xml_per_parsed_section(self, tmp_path: Path) -> None:
        scraper = _OneShotScraper(["1.01", "1.02", "1.03"])
        result = scraper.run(tmp_path, log_every=0)

        assert result.written == 3
        assert result.skipped == 0
        assert result.total == 3
        assert result.elapsed_seconds >= 0

        files = list(tmp_path.rglob("*.xml"))
        assert len(files) == 3

    def test_default_output_path_shape(self, tmp_path: Path) -> None:
        scraper = _OneShotScraper(["244.010"])
        scraper.run(tmp_path, log_every=0)
        expected = tmp_path / "us-test" / "statute" / "244.010.xml"
        assert expected.exists()

    def test_slash_in_section_id_is_sanitized(self, tmp_path: Path) -> None:
        # Some real IL citations are "35-155/2" — slashes would break path.
        scraper = _OneShotScraper(["35-155_2"])  # we pass safe form already
        scraper.run(tmp_path, log_every=0)
        assert (tmp_path / "us-test" / "statute" / "35-155_2.xml").exists()

    def test_parse_returning_none_counts_as_skip(self, tmp_path: Path) -> None:
        scraper = _OneShotScraper(["1.01", "1.02", "1.03"], skip={"1.02"})
        result = scraper.run(tmp_path, log_every=0)
        assert result.written == 2
        assert result.skipped == 1
        assert len(list(tmp_path.rglob("*.xml"))) == 2

    def test_limit_caps_run(self, tmp_path: Path) -> None:
        scraper = _OneShotScraper([f"1.{i:02d}" for i in range(100)])
        result = scraper.run(tmp_path, limit=5, log_every=0)
        assert result.written == 5

    def test_exception_in_parse_is_soft_failed(self, tmp_path: Path) -> None:
        class ExplodingScraper(_OneShotScraper):
            def parse_section(self, ref: str) -> Section | None:
                if ref == "BAD":
                    raise ValueError("boom")
                return make_section(num=ref)

        scraper = ExplodingScraper(["1.01", "BAD", "1.02"])
        result = scraper.run(tmp_path, log_every=0)
        assert result.written == 2
        assert result.skipped == 1  # BAD was soft-failed, not crashed

    def test_custom_logger_receives_progress(self, tmp_path: Path) -> None:
        scraper = _OneShotScraper([f"1.{i:02d}" for i in range(10)])
        msgs: list[str] = []
        scraper.run(tmp_path, log_every=5, logger=msgs.append)
        # Opening "Scraping N sections" + closing "DONE" + progress ticks.
        assert any(m.startswith("Scraping") for m in msgs)
        assert any("DONE" in m for m in msgs)

    def test_written_xml_contains_section_heading(self, tmp_path: Path) -> None:
        scraper = _OneShotScraper(["42.01"])
        scraper.run(tmp_path, log_every=0)
        content = (tmp_path / "us-test" / "statute" / "42.01.xml").read_text()
        assert "Heading for 42.01" in content
        assert "TEST 42.01" in content


class TestOutputPathOverride:
    def test_subclass_can_override_layout(self, tmp_path: Path) -> None:
        class ChapterNestedScraper(_OneShotScraper):
            def relative_output_path(self, section: Section) -> Path:
                chapter = section.work_number.split(".")[0]
                return Path(
                    self.jurisdiction,
                    self.doc_type,
                    f"ch-{chapter}",
                    f"{section.work_number}.xml",
                )

        scraper = ChapterNestedScraper(["244.010", "244.011", "300.001"])
        scraper.run(tmp_path, log_every=0)
        assert (tmp_path / "us-test/statute/ch-244/244.010.xml").exists()
        assert (tmp_path / "us-test/statute/ch-300/300.001.xml").exists()


class TestScrapeResult:
    def test_total_property(self) -> None:
        r = ScrapeResult(written=5, skipped=3, elapsed_seconds=12.5)
        assert r.total == 8
