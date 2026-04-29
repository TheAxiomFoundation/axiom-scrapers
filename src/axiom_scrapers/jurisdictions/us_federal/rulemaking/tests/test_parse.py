"""Offline tests for the Federal Register rulemaking scraper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from axiom_scrapers._common.source_section import SourceSection
from axiom_scrapers._common.testing import install_fake_http
from axiom_scrapers.jurisdictions.us_federal.rulemaking import scrape
from axiom_scrapers.jurisdictions.us_federal.rulemaking.scrape import (
    FederalRegisterRulemakingScraper,
    FRDocRef,
    _index_url,
    extract_body_text,
    parse_index_results,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseIndexResults:
    def test_real_recent_rules(self) -> None:
        refs = parse_index_results(_fixture("recent_rules.json"))
        assert len(refs) >= 1
        for ref in refs:
            assert ref.document_number
            assert ref.raw_text_url.startswith("https://")
            assert ref.publication_date

    def test_empty_payload_returns_empty(self) -> None:
        assert parse_index_results('{"results":[]}') == []
        assert parse_index_results("{}") == []

    def test_malformed_payload_returns_empty(self) -> None:
        assert parse_index_results("null") == []
        assert parse_index_results("[1,2,3]") == []

    def test_drops_entries_without_raw_text_url(self) -> None:
        payload = (
            '{"results":['
            '{"document_number":"2026-1","title":"Has text",'
            '"raw_text_url":"https://example.test/a.txt","publication_date":"2026-01-01"},'
            '{"document_number":"2026-2","title":"No text URL","publication_date":"2026-01-02"}'
            "]}"
        )
        refs = parse_index_results(payload)
        assert [r.document_number for r in refs] == ["2026-1"]

    def test_drops_entries_without_document_number(self) -> None:
        payload = (
            '{"results":['
            '{"title":"Anonymous","raw_text_url":"https://x.test/a.txt",'
            '"publication_date":"2026-01-01"}'
            "]}"
        )
        assert parse_index_results(payload) == []

    def test_collects_agency_names(self) -> None:
        payload = (
            '{"results":[{"document_number":"X","title":"T",'
            '"raw_text_url":"https://x.test/","publication_date":"2026-01-01",'
            '"agencies":[{"name":"Treasury"},{"name":"IRS"}]}]}'
        )
        assert parse_index_results(payload)[0].agency_names == ("Treasury", "IRS")

    def test_captures_fr_type(self) -> None:
        payload = (
            '{"results":['
            '{"document_number":"A","title":"T","type":"Rule",'
            '"raw_text_url":"https://x.test/a","publication_date":"2026-01-01"},'
            '{"document_number":"B","title":"T","type":"Proposed Rule",'
            '"raw_text_url":"https://x.test/b","publication_date":"2026-01-02"}'
            "]}"
        )
        refs = parse_index_results(payload)
        assert refs[0].fr_type == "Rule"
        assert refs[1].fr_type == "Proposed Rule"


class TestExtractBodyText:
    def test_real_document_body(self) -> None:
        body = extract_body_text(_fixture("doc_2026-07681.txt"))
        # Opening banner from the real page.
        assert "Federal Register Volume" in body
        assert "DEPARTMENT OF THE TREASURY" in body
        # Should not include the HTML wrapper tags.
        assert "<html>" not in body
        assert "</pre>" not in body
        # Inline <a href=...>www.gpo.gov</a> anchor should be stripped,
        # leaving just the visible text.
        assert "www.gpo.gov" in body
        assert "</a>" not in body

    def test_returns_empty_without_pre_block(self) -> None:
        assert extract_body_text("<html>no pre here</html>") == ""

    def test_entities_decoded(self) -> None:
        html = "<html><body><pre>Section &sect; 300 &amp; Title 26</pre></body></html>"
        assert extract_body_text(html) == "Section § 300 & Title 26"

    def test_inline_anchor_text_preserved(self) -> None:
        html = (
            "<html><body><pre>See <a href=\"https://x.test/\">reference</a> "
            "for details.</pre></body></html>"
        )
        assert extract_body_text(html) == "See reference for details."

    def test_strips_trailing_whitespace(self) -> None:
        html = "<html><body><pre>\n\n  Body.  \n\n  </pre></body></html>"
        assert extract_body_text(html) == "Body."


class TestIndexUrl:
    def test_builds_correct_url(self) -> None:
        url = _index_url(("RULE",), "2026-04-13", "2026-04-20", 1)
        assert "per_page=100" in url
        assert "page=1" in url
        assert "order=newest" in url
        assert "conditions[publication_date][gte]=2026-04-13" in url
        assert "conditions[publication_date][lte]=2026-04-20" in url
        assert "conditions[type][]=RULE" in url
        assert "fields[]=document_number" in url

    def test_multiple_types(self) -> None:
        url = _index_url(("RULE", "PRORULE"), "2026-01-01", "2026-01-02", 2)
        assert "conditions[type][]=RULE" in url
        assert "conditions[type][]=PRORULE" in url
        assert "page=2" in url


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = FederalRegisterRulemakingScraper(generation_date=date(2026, 4, 20))
        assert scraper.jurisdiction == "us-federal"
        assert scraper.doc_type == "rulemaking"
        assert scraper.authority_code == "FR"

    def test_parse_section_soft_fails_when_raw_text_missing(self) -> None:
        ref = FRDocRef(
            document_number="2026-1",
            title="T",
            citation="",
            publication_date="2026-01-01",
            raw_text_url="",
            agency_names=(),
        )
        scraper = FederalRegisterRulemakingScraper()
        assert scraper.parse_section(ref) is None

    def test_parse_section_builds_section_from_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ref = FRDocRef(
            document_number="2026-07681",
            title="Enrolled Agent Fee Update",
            citation="91 FR 20899",
            publication_date="2026-04-20",
            raw_text_url="https://example.test/doc.txt",
            agency_names=("IRS",),
        )
        install_fake_http(
            monkeypatch,
            scrape,
            {
                "https://example.test/doc.txt": (
                    "<html><body><pre>SUMMARY: This rule updates fees.</pre></body></html>"
                )
            },
        )
        scraper = FederalRegisterRulemakingScraper(generation_date=date(2026, 4, 20))
        sec = scraper.parse_section(ref)
        assert sec is not None
        assert sec.work_number == "2026-07681"
        assert sec.citation == "91 FR 20899"
        assert sec.heading == "Enrolled Agent Fee Update"
        assert "SUMMARY" in sec.body

    def test_parse_section_prefixes_proposed_rule_heading(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ref = FRDocRef(
            document_number="2026-07682",
            title="Enrolled Agent Fee Update",
            citation="91 FR 20910",
            publication_date="2026-04-20",
            raw_text_url="https://example.test/proposed.txt",
            agency_names=("IRS",),
            fr_type="Proposed Rule",
        )
        install_fake_http(
            monkeypatch,
            scrape,
            {
                "https://example.test/proposed.txt": (
                    "<html><body><pre>ACTION: Notice of proposed rulemaking.</pre></body></html>"
                )
            },
        )
        sec = FederalRegisterRulemakingScraper().parse_section(ref)
        assert sec is not None
        assert sec.heading == "[Proposed] Enrolled Agent Fee Update"

    def test_parse_section_no_prefix_for_final_rule(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ref = FRDocRef(
            document_number="2026-07681",
            title="Final rule title",
            citation="91 FR 20899",
            publication_date="2026-04-20",
            raw_text_url="https://example.test/final.txt",
            agency_names=(),
            fr_type="Rule",
        )
        install_fake_http(
            monkeypatch,
            scrape,
            {"https://example.test/final.txt": "<html><body><pre>Body.</pre></body></html>"},
        )
        sec = FederalRegisterRulemakingScraper().parse_section(ref)
        assert sec is not None
        assert sec.heading == "Final rule title"
        assert not sec.heading.startswith("[Proposed]")

    def test_parse_section_falls_back_to_doc_number_citation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ref = FRDocRef(
            document_number="1999-12345",
            title="Old rule",
            citation="",
            publication_date="1999-06-01",
            raw_text_url="https://example.test/old.txt",
            agency_names=(),
        )
        install_fake_http(
            monkeypatch,
            scrape,
            {"https://example.test/old.txt": "<html><body><pre>Body.</pre></body></html>"},
        )
        sec = FederalRegisterRulemakingScraper().parse_section(ref)
        assert sec is not None
        assert sec.citation == "FR Doc. 1999-12345"


class TestOutputPath:
    def _section(self, work_number: str) -> SourceSection:
        return SourceSection(
            jurisdiction="us-federal",
            doc_type="rulemaking",
            authority_code="FR",
            work_number=work_number,
            citation="91 FR 20899",
            heading="H",
            body="B",
            author_id="us-federal-register",
            author_name="FR",
            author_url="https://www.federalregister.gov",
            generation_date=date(2026, 4, 20),
        )

    def test_nests_by_year(self) -> None:
        scraper = FederalRegisterRulemakingScraper()
        rel = scraper.relative_output_path(self._section("2026-07681"))
        assert rel == Path("us-federal/rulemaking/2026/2026-07681.txt")

    def test_old_document(self) -> None:
        scraper = FederalRegisterRulemakingScraper()
        rel = scraper.relative_output_path(self._section("1999-12345"))
        assert rel == Path("us-federal/rulemaking/1999/1999-12345.txt")


class TestCrawlLayer:
    """End-to-end walk of the index endpoint via fake HTTP."""

    def test_list_sections_paginates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        page_1 = (
            '{"results":[{"document_number":"2026-1","title":"A",'
            '"raw_text_url":"https://x.test/a.txt","publication_date":"2026-04-14"}],'
            '"next_page_url":"https://x.test/page2"}'
        )
        page_2 = (
            '{"results":[{"document_number":"2026-2","title":"B",'
            '"raw_text_url":"https://x.test/b.txt","publication_date":"2026-04-15"}]}'
        )
        scraper = FederalRegisterRulemakingScraper(generation_date=date(2026, 4, 20))
        start = (date(2026, 4, 20) - __import__("datetime").timedelta(days=7)).isoformat()
        url1 = _index_url(scrape._DOC_TYPES, start, "2026-04-20", 1)
        url2 = _index_url(scrape._DOC_TYPES, start, "2026-04-20", 2)
        install_fake_http(monkeypatch, scrape, {url1: page_1, url2: page_2})
        refs = list(scraper.list_sections())
        assert [r.document_number for r in refs] == ["2026-1", "2026-2"]

    def test_list_sections_stops_on_empty_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scraper = FederalRegisterRulemakingScraper(generation_date=date(2026, 4, 20))
        start = (date(2026, 4, 20) - __import__("datetime").timedelta(days=7)).isoformat()
        url1 = _index_url(scrape._DOC_TYPES, start, "2026-04-20", 1)
        install_fake_http(monkeypatch, scrape, {url1: '{"results":[]}'})
        assert list(scraper.list_sections()) == []
