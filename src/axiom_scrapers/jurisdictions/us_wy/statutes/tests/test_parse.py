"""Offline parse tests for the Wyoming Statutes scraper.

Fixtures were captured from a live ``wyoleg.gov`` session in April 2026:

* ``toc_root.xml`` — root TOC listing (year buckets).
* ``toc_titles.xml`` — children of ``2025 Wyoming Statutes/2025 Titles``.
* ``toc_chapter_leaf.xml`` — sections in a ``text/xml`` chapter leaf
  (Title 1, Chapter 2 — Oaths).
* ``toc_chapter_folder.xml`` — articles in a folder-type chapter
  (Title 1, Chapter 6 — Process, Notice and Lis Pendens).
* ``chapter_2_oaths.html`` — leaf document for the chapter-leaf case
  (4 sections, mix of empty and multi-paragraph bodies).
* ``article_1_lis_pendens.html`` — leaf document for the article case
  (11 sections under Article 1 of Title 1 Chapter 6).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from axiom_scrapers._common import FetchResult, http
from axiom_scrapers._common.akn import Section
from axiom_scrapers.jurisdictions.us_wy.statutes.scrape import (
    GATEWAY,
    TITLES_ROOT_ID,
    VID,
    TocNode,
    WyoStatutesScraper,
    WySectionRef,
    build_document_url,
    build_xmlcontents_url,
    extract_section_id_and_heading,
    extract_title_from_work_number,
    extract_toc_nodes,
    iter_section_blocks,
    parse_section_body,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestExtractTocNodes:
    def test_root_lists_year_buckets(self) -> None:
        nodes = extract_toc_nodes(_fixture("toc_root.xml"))
        ids = [n.id for n in nodes]
        # Real fixture lists multiple year snapshots; current and recent
        # publications must both be present.
        assert "2025 Wyoming Statutes" in ids
        assert "2024 Wyoming Statutes" in ids
        assert "2023 Wyoming Statutes" in ids
        # All year buckets are folders (recurse to find titles).
        assert all(n.content_type == "application/folder" for n in nodes)

    def test_titles_listing(self) -> None:
        nodes = extract_toc_nodes(_fixture("toc_titles.xml"))
        # Title 1 (Code of Civil Procedure) must be present and be a
        # folder — chapters live underneath.
        names = [n.name for n in nodes]
        assert "1" in names
        title_1 = next(n for n in nodes if n.name == "1")
        assert title_1.content_type == "application/folder"
        assert "TITLE 1" in title_1.title.upper()
        # The fixture covers a substantial slice of Wyoming's titles
        # (the actual range depends on the snapshot); a healthy walk
        # should surface at least a few dozen.
        assert len(nodes) >= 10

    def test_chapter_leaf_sections(self) -> None:
        nodes = extract_toc_nodes(_fixture("toc_chapter_leaf.xml"))
        assert all(n.content_type == "application/subdocument" for n in nodes)
        # Chapter 2 (Oaths) in Title 1 surfaces 4 sections: 1-2-101..104.
        titles = [n.title for n in nodes]
        assert any(t.startswith("1-2-101") for t in titles)
        assert any(t.startswith("1-2-104") for t in titles)
        assert len(nodes) == 4

    def test_chapter_folder_articles(self) -> None:
        nodes = extract_toc_nodes(_fixture("toc_chapter_folder.xml"))
        # Chapter 6 (Process, Notice, Lis Pendens) decomposes into
        # articles. The first child must be ARTICLE 1.
        assert nodes[0].content_type == "text/xml"
        assert "ARTICLE 1" in nodes[0].title.upper()

    def test_returns_empty_when_no_nodes_envelope(self) -> None:
        assert extract_toc_nodes("<toc></toc>") == []

    def test_handles_self_closing_tags(self) -> None:
        xml = (
            '<toc><nodes>'
            '<n ct="text/xml" hc="y" id="A/B" n="B" t="Section B"/>'
            '</nodes></toc>'
        )
        nodes = extract_toc_nodes(xml)
        assert nodes == [
            TocNode(id="A/B", name="B", title="Section B", content_type="text/xml")
        ]

    def test_skips_grandchildren(self) -> None:
        # NXT inlines grandchildren when the caller pre-expanded them;
        # extract_toc_nodes must only return immediate children.
        xml = (
            '<toc><nodes>'
            '<n ct="application/folder" id="A" n="A" t="A">'
            '<n ct="text/xml" id="A/x" n="x" t="Grandchild"/>'
            '</n>'
            '</nodes></toc>'
        )
        nodes = extract_toc_nodes(xml)
        assert len(nodes) == 1
        assert nodes[0].id == "A"


class TestIterSectionBlocks:
    def test_chapter_with_four_sections(self) -> None:
        html = _fixture("chapter_2_oaths.html")
        blocks = list(iter_section_blocks(html))
        # Chapter 2 has 4 sections: 1-2-101..1-2-104.
        assert len(blocks) == 4
        # First heading carries the citation; first body carries the
        # rule text ("A person may be sworn by any form…").
        head_html, body_html = blocks[0]
        assert "1-2-101" in head_html
        assert "any form" in body_html

    def test_article_with_many_sections(self) -> None:
        html = _fixture("article_1_lis_pendens.html")
        blocks = list(iter_section_blocks(html))
        # Article 1 carries 11 sections per the saved fixture.
        assert len(blocks) == 11

    def test_no_sections_returns_empty(self) -> None:
        assert list(iter_section_blocks("<html><body>nothing</body></html>")) == []

    def test_caps_last_body_at_body_close(self) -> None:
        html = (
            '<body>'
            '<div class="Section"><span></span><div>1-1-101. Sample.</div></div>'
            '<div class="Normal-Level"><div class="Normal">First.</div></div>'
            '</body>'
            'TRAILER'
        )
        blocks = list(iter_section_blocks(html))
        assert len(blocks) == 1
        _, body = blocks[0]
        assert "First." in body
        assert "TRAILER" not in body


class TestExtractSectionIdAndHeading:
    def test_basic_form(self) -> None:
        head_html = (
            '<div class="Section"><span class="heading_text"></span>'
            '<div>1-2-101.&nbsp;&nbsp;Form.</div></div>'
        )
        result = extract_section_id_and_heading(head_html)
        assert result == ("1-2-101", "Form")

    def test_decimal_section_suffix(self) -> None:
        head_html = (
            '<div class="Section"><span></span>'
            '<div>1-39-104.5.&nbsp;Sub-section identifier.</div></div>'
        )
        result = extract_section_id_and_heading(head_html)
        assert result is not None
        work, heading = result
        assert work == "1-39-104.5"
        assert heading == "Sub-section identifier"

    def test_missing_match_returns_none(self) -> None:
        assert extract_section_id_and_heading("<div>nope</div>") is None

    def test_strips_trailing_period(self) -> None:
        head_html = (
            '<div class="Section"><span></span>'
            '<div>1-2-102.&nbsp;Officers authorized to administer.</div></div>'
        )
        result = extract_section_id_and_heading(head_html)
        assert result is not None
        _, heading = result
        # Trailing punctuation is dropped so it matches what other
        # state scrapers emit (heading only, no terminal period).
        assert heading == "Officers authorized to administer"


class TestParseSectionBody:
    def test_collapses_multi_div_paragraphs(self) -> None:
        body = (
            '<div class="Normal-Level"><div class="Normal">First sentence.</div></div>'
            '<div class="Normal-Level"><div class="Normal" style="margin-bottom=10pt"> </div></div>'
            '<div class="Normal-Level"><div class="L2">(a)&nbsp;Second clause.</div></div>'
            '<div class="Normal-Level"><div class="L3">(i)&nbsp;Sub-clause.</div></div>'
        )
        out = parse_section_body(body)
        # Each non-empty block becomes its own paragraph.
        assert "First sentence." in out
        assert "(a) Second clause." in out
        assert "(i) Sub-clause." in out
        # Blank-spacer divs collapse — the result must have at least
        # paragraph breaks between distinct clauses.
        assert "\n\n" in out

    def test_empty_body(self) -> None:
        # Body that's only the spacer div used by NXT for vertical gap.
        body = (
            '<div class="Normal-Level">'
            '<div class="Normal" style="margin-bottom=10pt"> </div>'
            '</div>'
        )
        assert parse_section_body(body) == ""


class TestExtractTitleFromWorkNumber:
    def test_simple(self) -> None:
        assert extract_title_from_work_number("1-2-101") == "1"

    def test_two_digit_title(self) -> None:
        assert extract_title_from_work_number("39-15-103") == "39"

    def test_decimal_section(self) -> None:
        assert extract_title_from_work_number("1-39-104.5") == "1"


class TestUrlBuilders:
    def test_xmlcontents_url(self) -> None:
        url = build_xmlcontents_url(TITLES_ROOT_ID)
        assert url.startswith(f"{GATEWAY}?")
        # Spaces percent-encoded; slashes encoded too (Folio expects %2F here).
        assert "basepathid=2025%20Wyoming%20Statutes%2F2025%20Titles" in url
        assert "f=xmlcontents" in url
        assert "command=getchildren" in url
        assert f"vid={VID.replace(':', '%3A').replace('/', '%2F')}" in url

    def test_document_url_preserves_slashes(self) -> None:
        url = build_document_url("2025 Wyoming Statutes/2025 Titles/1/3")
        # In the doc path, slashes are real path separators (not %2F).
        assert "/2025%20Wyoming%20Statutes/2025%20Titles/1/3" in url
        assert "fn=document-frameset.htm" in url

    def test_document_url_handles_article(self) -> None:
        url = build_document_url("2025 Wyoming Statutes/2025 Titles/1/7/8")
        assert url.endswith(
            "/2025%20Wyoming%20Statutes/2025%20Titles/1/7/8"
            f"?f=templates&fn=document-frameset.htm&vid={VID.replace(':', '%3A').replace('/', '%2F')}"
        )


class TestScraperClass:
    def test_config_validates(self) -> None:
        scraper = WyoStatutesScraper(generation_date=date(2026, 4, 21))
        assert scraper.jurisdiction == "us-wy"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "Wyo. Stat. Ann."

    def test_section_ref_frozen(self) -> None:
        ref = WySectionRef(
            work_number="1-2-101",
            title="1",
            heading="Form",
            body="A person may be sworn.",
            source_url="https://wyoleg.gov/x",
        )
        assert ref.work_number == "1-2-101"

    def test_parse_section_returns_section(self) -> None:
        scraper = WyoStatutesScraper(generation_date=date(2026, 4, 21))
        ref = WySectionRef(
            work_number="1-2-101",
            title="1",
            heading="Form",
            body="A person may be sworn by any form he deems binding on his conscience.",
            source_url="https://wyoleg.gov/x",
        )
        sec = scraper.parse_section(ref)
        assert sec is not None
        assert sec.work_number == "1-2-101"
        assert sec.heading == "Form"
        assert sec.citation == "Wyo. Stat. Ann. § 1-2-101"
        assert sec.author_id == "wy-legislature"

    def test_parse_section_skips_empty_body(self) -> None:
        scraper = WyoStatutesScraper()
        ref = WySectionRef(
            work_number="1-2-101", title="1", heading="Form", body="", source_url="x"
        )
        assert scraper.parse_section(ref) is None


class TestOutputPath:
    def _section(self, work_number: str) -> Section:
        return Section(
            jurisdiction="us-wy",
            doc_type="statute",
            authority_code="Wyo. Stat. Ann.",
            work_number=work_number,
            citation=f"Wyo. Stat. Ann. § {work_number}",
            heading="H",
            body="B",
            author_id="wy-legislature",
            author_name="Wyoming Legislature",
            author_url="https://wyoleg.gov",
            generation_date=date(2026, 4, 21),
        )

    def test_nests_by_title(self) -> None:
        scraper = WyoStatutesScraper()
        rel = scraper.relative_output_path(self._section("1-2-101"))
        assert rel == Path("us-wy/statutes/title-1/1-2-101.xml")

    def test_two_digit_title(self) -> None:
        scraper = WyoStatutesScraper()
        rel = scraper.relative_output_path(self._section("39-15-103"))
        assert rel == Path("us-wy/statutes/title-39/39-15-103.xml")

    def test_decimal_section_suffix(self) -> None:
        scraper = WyoStatutesScraper()
        rel = scraper.relative_output_path(self._section("1-39-104.5"))
        assert rel == Path("us-wy/statutes/title-1/1-39-104.5.xml")


class TestEndToEndWithStubbedNetwork:
    """Integration-shape test: stub ``http_get`` with the saved fixtures."""

    def test_yields_sections_from_one_chapter(self) -> None:
        scraper = WyoStatutesScraper(generation_date=date(2026, 4, 21))

        # Map every URL the scraper might fetch to a fixture body.
        # Discovery walks: titles → chapters → leaf docs.
        titles_root_url = build_xmlcontents_url(TITLES_ROOT_ID)
        # Synthesize a one-title TOC pointing at the real fixture chapter.
        single_title = (
            '<toc><nodes>'
            '<n ct="application/folder" hc="y" '
            f'id="{TITLES_ROOT_ID}/1" n="1" t="TITLE 1 - CIVIL PROCEDURE"/>'
            '</nodes></toc>'
        )
        title_1_chapters = (
            '<toc><nodes>'
            '<n ct="text/xml" hc="y" '
            f'id="{TITLES_ROOT_ID}/1/3" n="3" t="CHAPTER 2 - OATHS"/>'
            '</nodes></toc>'
        )
        chapter_2_doc = _fixture("chapter_2_oaths.html")

        responses = {
            titles_root_url: single_title,
            build_xmlcontents_url(f"{TITLES_ROOT_ID}/1"): title_1_chapters,
            build_document_url(f"{TITLES_ROOT_ID}/1/3"): chapter_2_doc,
        }

        def fake_http_get(url: str, **_: Any) -> FetchResult | None:
            body = responses.get(url)
            if body is None:
                return None
            return FetchResult(body=body.encode("utf-8"), url=url, charset="utf-8")

        with patch.object(http, "http_get", fake_http_get), patch(
            "axiom_scrapers.jurisdictions.us_wy.statutes.scrape.http_get",
            fake_http_get,
        ):
            refs = list(scraper.list_sections())

        # Chapter 2 (Oaths) holds 4 sections.
        assert len(refs) >= 1
        ids = [r.work_number for r in refs]
        assert "1-2-101" in ids
        # Verify body propagation (1-2-101 is a single-sentence section).
        sec_101 = next(r for r in refs if r.work_number == "1-2-101")
        assert "any form" in sec_101.body
        assert sec_101.title == "1"

    def test_skips_when_root_toc_unavailable(self) -> None:
        scraper = WyoStatutesScraper()
        with patch(
            "axiom_scrapers.jurisdictions.us_wy.statutes.scrape.http_get",
            lambda *_a, **_kw: None,
        ):
            assert list(scraper.list_sections()) == []


# Silence unused-import warning for the patched `http` module (we patch
# both the `http` namespace and the local `http_get` re-export so any
# accidental indirect call also routes through the fake).
cast(Any, http)
