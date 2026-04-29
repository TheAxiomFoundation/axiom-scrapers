"""Offline tests for the NRS parser.

Fixtures are real chapter HTML saved under ``fixtures/``. No live HTTP
in these tests — the subclass's :meth:`parse_section` is exercised via
the pure helper :func:`_parse_sections`. End-to-end fetch behavior
(``http_get`` retries, ConnectionResetError handling, etc.) is covered
by the shared base-class tests in
``axiom_scrapers._common.tests.test_http``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from axiom_scrapers._common.source_section import SourceSection
from axiom_scrapers.jurisdictions.us_nv.statutes.scrape import (
    NRSStatutesScraper,
    _chapter_token,
    _list_chapter_files,
    _parse_sections,
)

FIXTURES = Path(__file__).parent / "fixtures"

# NRS-244.html is chapter 244 (Counties: Government). At scrape-time
# the chapter had 310 section anchors, all with bodies — a good
# representative for the "big chapter, all live" case. If NV adds /
# repeals sections the numbers below will nudge; update to match.
_NRS_244 = "nrs_244.html"


def _load(name: str) -> str:
    """Load a fixture as cp1252-decoded text (matching NV's encoding)."""
    return (FIXTURES / name).read_bytes().decode("cp1252", errors="replace")


class TestParseSections:
    def test_known_count_of_sections(self) -> None:
        """NRS-244 has 310 live sections; a regression here means the
        anchor regex or the empty-body guard shifted."""
        html = _load(_NRS_244)
        sections = _parse_sections(html, generation_date=date(2026, 4, 20))
        assert len(sections) == 310

    def test_known_section_244_010_citation_and_heading(self) -> None:
        """244.010 is the first section of the chapter and a canonical
        example: plain ``Leadline`` heading + a dense-paragraph body
        that references three other sections."""
        html = _load(_NRS_244)
        sections = _parse_sections(html, generation_date=date(2026, 4, 20))
        by_work = {s.work_number: s for s in sections}
        assert "244.010" in by_work
        sec = by_work["244.010"]
        assert sec.jurisdiction == "us-nv"
        assert sec.doc_type == "statute"
        assert sec.authority_code == "NRS"
        assert sec.citation == "NRS 244.010"
        assert sec.work_number == "244.010"
        assert sec.heading == "Minimum number of county commissioners"
        assert sec.author_id == "nv-legislature"
        assert sec.author_name == "Nevada Legislature"
        assert sec.author_url == "https://www.leg.state.nv.us"
        assert sec.generation_date == date(2026, 4, 20)

    def test_known_section_244_010_body_content(self) -> None:
        """Body should contain the operative language from the NRS and
        cross-reference the other three-commissioner sections."""
        html = _load(_NRS_244)
        sections = _parse_sections(html, generation_date=date(2026, 4, 20))
        by_work = {s.work_number: s for s in sections}
        body = by_work["244.010"].body
        # Operative language
        assert "three members" in body
        assert "board of county commissioners" in body
        # Cross-references are preserved as plain text
        assert "244.011" in body
        assert "244.014" in body
        assert "244.016" in body

    def test_source_note_paragraphs_stripped(self) -> None:
        """``<p class="SourceNote">`` paragraphs are cite-history,
        not body text — they must not leak into any section body."""
        html = _load(_NRS_244)
        sections = _parse_sections(html, generation_date=date(2026, 4, 20))
        # "(Added to NRS by" is the NV source-history prefix; it lives
        # inside SourceNote paragraphs and should never appear in body.
        for s in sections:
            assert "Added to NRS by" not in s.body, (
                f"SourceNote leaked into {s.work_number}: {s.body[:200]!r}"
            )
            # And the bracketed pre-codification history format
            assert not s.body.lstrip().startswith("["), (
                f"SourceNote bracket leaked into {s.work_number}"
            )

    def test_section_numbers_preserve_decimal_format(self) -> None:
        """NV section numbers are ``244.010`` (leading-zero minor). The
        parser must not strip decimal points or leading zeros from the
        canonical citation — downstream keys on the exact string."""
        html = _load(_NRS_244)
        sections = _parse_sections(html, generation_date=date(2026, 4, 20))
        work_numbers = {s.work_number for s in sections}
        # A handful of known-good exact numbers from chapter 244.
        for expected in ("244.010", "244.011", "244.014", "244.016", "244.1505"):
            assert expected in work_numbers, f"missing {expected}"
        # Every work_number should be ``{chapter}.{minor}`` shape,
        # never the raw anchor form ``Sec010``.
        for wn in work_numbers:
            assert "." in wn, f"malformed work_number: {wn!r}"
            chapter, minor = wn.split(".", 1)
            assert chapter == "244", f"chapter mismatch in {wn!r}"
            assert minor, f"empty minor in {wn!r}"

    def test_no_anchors_returns_empty(self) -> None:
        """Pages without section anchors (e.g. a 404 stub someone
        saved by mistake) parse to an empty list, not a crash."""
        assert (
            _parse_sections(
                "<html><body>nothing here</body></html>",
                generation_date=date.today(),
            )
            == []
        )

    def test_empty_or_repealed_body_sections_skipped(self) -> None:
        """Hand-rolled minimal page exercising the empty-body guard.

        Three anchors:
        * ``Sec001`` — has a full body. Kept.
        * ``Sec002`` — only a ``Repealed.`` placeholder. Dropped.
        * ``Sec003`` — has just a heading, no body paragraph. Dropped.
        """
        html = """
        <html><body>
        <p class="SectBody"><span class="Empty"><a name=NRS999Sec001></a>NRS </span>
        <span class="Section">999.001</span><span class="Empty">  </span>
        <span class="Leadline">Definitions.</span><span class="Empty">  </span>
        As used in this chapter, unless the context otherwise requires, words
        have the meanings ascribed to them in the sections that follow.</p>
        <p class="SourceNote">(Added to NRS by 2001, 100)</p>

        <p class="SectBody"><span class="Empty"><a name=NRS999Sec002></a>NRS </span>
        <span class="Section">999.002</span><span class="Empty">  </span>
        <span class="Leadline">Repealed.</span></p>
        <p class="SourceNote">(Repealed.)</p>

        <p class="SectBody"><span class="Empty"><a name=NRS999Sec003></a>NRS </span>
        <span class="Section">999.003</span><span class="Empty">  </span>
        <span class="Leadline">Empty.</span></p>
        </body></html>
        """
        sections = _parse_sections(html, generation_date=date.today())
        assert len(sections) == 1
        assert sections[0].work_number == "999.001"
        assert "meanings ascribed" in sections[0].body

    def test_coleadline_class_also_parsed_as_heading(self) -> None:
        """Some sections use ``COLeadline`` (cross-office lead) instead
        of ``Leadline``. The regex handles both."""
        html = """
        <p class="SectBody"><a name=NRS100Sec010></a>
        <span class="Section">100.010</span>
        <span class="COLeadline">Short title.</span>
        This chapter may be cited as the Example Act.</p>
        """
        sections = _parse_sections(html, generation_date=date.today())
        assert len(sections) == 1
        assert sections[0].heading == "Short title"
        assert "Example Act" in sections[0].body

    def test_crlf_line_endings_normalized(self) -> None:
        """NV's Word export ships with CRLF. Bodies must not carry
        stray ``\\r`` chars through to source text output."""
        html = _load(_NRS_244)
        sections = _parse_sections(html, generation_date=date.today())
        for s in sections:
            assert "\r" not in s.body, f"\\r leaked into {s.work_number}: {s.body[:100]!r}"
            assert "\r" not in s.heading


class TestListChapterFiles:
    def test_extracts_nrs_html_filenames(self) -> None:
        html = """
        <a href="NRS-001.html">1</a>
        <a href="NRS-244.html">244</a>
        <a href="NRS-244A.html">244A</a>
        <a href="NRS-360B.html">360B</a>
        <a href="other.html">other</a>
        """
        files = _list_chapter_files(html)
        assert files == [
            "NRS-001.html",
            "NRS-244.html",
            "NRS-244A.html",
            "NRS-360B.html",
        ]

    def test_deduplicates_repeated_hrefs(self) -> None:
        html = '<a href="NRS-244.html">a</a><a href="NRS-244.html">b</a>'
        assert _list_chapter_files(html) == ["NRS-244.html"]

    def test_empty_listing(self) -> None:
        assert _list_chapter_files("") == []


class TestChapterToken:
    def test_numeric_chapter(self) -> None:
        assert _chapter_token("NRS-244.html") == "244"

    def test_chapter_with_letter_suffix(self) -> None:
        # Chapters like 244A (Public Health) carry their letter token
        # through to the output filesystem layout.
        assert _chapter_token("NRS-244A.html") == "244A"
        assert _chapter_token("NRS-360B.html") == "360B"

    def test_unexpected_filename_returned_as_is(self) -> None:
        # Robustness: garbage in → garbage out, no crash.
        assert _chapter_token("weird.html") == "weird.html"


class TestNRSScraperConfig:
    def test_subclass_config_complete(self) -> None:
        scraper = NRSStatutesScraper()
        assert scraper.jurisdiction == "us-nv"
        assert scraper.doc_type == "statute"
        assert scraper.authority_code == "NRS"
        assert scraper.author_id == "nv-legislature"
        assert scraper.author_name == "Nevada Legislature"
        assert scraper.author_url == "https://www.leg.state.nv.us"
        assert scraper.workers == 6

    def test_output_path_nests_by_chapter(self) -> None:
        scraper = NRSStatutesScraper()
        sec = SourceSection(
            jurisdiction="us-nv",
            doc_type="statute",
            authority_code="NRS",
            work_number="244.010",
            citation="NRS 244.010",
            heading="Minimum number of county commissioners",
            body="body",
            author_id="nv-legislature",
            author_name="Nevada Legislature",
            author_url="https://www.leg.state.nv.us",
            generation_date=date.today(),
        )
        rel = scraper.relative_output_path(sec)
        assert rel == Path("us-nv/statutes/ch-244/244.010.txt")

    def test_output_path_handles_letter_chapter_token(self) -> None:
        """Chapters like 244A must land under ``ch-244A``, not
        accidentally collapse to ``ch-244``."""
        scraper = NRSStatutesScraper()
        sec = SourceSection(
            jurisdiction="us-nv",
            doc_type="statute",
            authority_code="NRS",
            work_number="244A.010",
            citation="NRS 244A.010",
            heading="Short title",
            body="body",
            author_id="nv-legislature",
            author_name="Nevada Legislature",
            author_url="https://www.leg.state.nv.us",
            generation_date=date.today(),
        )
        rel = scraper.relative_output_path(sec)
        assert rel == Path("us-nv/statutes/ch-244A/244A.010.txt")


class TestConnectionResetHandledByHttpGet:
    """ConnectionResetError is handled by the shared ``http_get`` —
    this test nails down the contract at the NV level so a future
    refactor that replaces ``http_get`` in this module still preserves
    the soft-fail behavior.
    """

    def test_connection_reset_surfaces_as_none_not_crash(self) -> None:
        from axiom_scrapers._common.http import http_get

        calls: list[str] = []

        def opener(req: object, timeout: float) -> object:  # noqa: ARG001
            calls.append("tried")
            raise ConnectionResetError("connection reset by peer")

        # With the default 5 retries + a noop sleeper, we expect
        # ``None`` (soft-fail) rather than a propagated exception.
        result = http_get(
            "https://www.leg.state.nv.us/NRS/NRS-244.html",
            opener=opener,
            sleeper=lambda _s: None,
        )
        assert result is None
        assert len(calls) == 5

    def test_connection_reset_then_success_returns_body(self) -> None:
        """Transient reset → retry → success path."""
        from axiom_scrapers._common.http import http_get

        class _FakeResp:
            def __init__(self, body: bytes) -> None:
                self._body = body
                self.headers = {"Content-Type": "text/html; charset=windows-1252"}
                self.url = "https://www.leg.state.nv.us/NRS/NRS-244.html"

            def __enter__(self) -> _FakeResp:
                return self

            def __exit__(self, *a: object) -> None:
                return None

            def read(self) -> bytes:
                return self._body

        responses: list[object] = [
            ConnectionResetError("reset"),
            _FakeResp(b"hello-nv"),
        ]

        def opener(req: object, timeout: float) -> object:  # noqa: ARG001
            nxt = responses.pop(0)
            if isinstance(nxt, BaseException):
                raise nxt
            return nxt

        got = http_get(
            "https://www.leg.state.nv.us/NRS/NRS-244.html",
            opener=opener,
            sleeper=lambda _s: None,
        )
        assert got is not None
        assert got.body == b"hello-nv"
        # Response advertised windows-1252; decode that way to exercise
        # the NV-specific path.
        assert got.text("cp1252") == "hello-nv"
