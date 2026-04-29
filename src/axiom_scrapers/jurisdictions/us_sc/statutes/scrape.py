"""South Carolina Code of Laws scraper.

Source — `scstatehouse.gov/code`
--------------------------------
The master index at ``/code/statmast.php`` lists 63 titles, each
linking to ``/code/title{N}.php``. Each title page links to chapter
HTML pages at ``/code/t{TT}c{CCC}.php`` (zero-padded).

Chapter pages render all sections inline. Each section is demarcated
by a ``SECTION {title}-{chapter}-{section}.`` marker followed by
heading and body text, terminated by a ``HISTORY:`` block that
carries session-law provenance and is stripped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from axiom_scrapers._common import Scraper, SourceSection, clean_paragraphs, clean_text, http_get

BASE = "https://www.scstatehouse.gov/code"
_DEFAULT_TITLES = tuple(range(1, 64))

_TITLE_LINK_RE = re.compile(
    r'href="/code/title(?P<title>\d+)\.php"', re.IGNORECASE
)

_CHAPTER_LINK_RE = re.compile(
    r'href="/code/t(?P<title>\d+)c(?P<chapter>\d+[A-Za-z]*)\.php"',
    re.IGNORECASE,
)

_SECTION_MARKER_RE = re.compile(
    r"SECTION\s+"
    r"(?P<full>(?P<t>\d+[A-Za-z]?)-(?P<c>\d+[A-Za-z]?)-(?P<s>\d+[A-Za-z]?))"
    r"\s*\."
)


class SCCodeStatutesScraper(Scraper[tuple[int, str, str]]):
    """Scrape the South Carolina Code.

    ``SectionRef`` is ``(title, chapter_token, section_num)`` where
    ``chapter_token`` is the zero-padded form used in URLs and
    ``section_num`` is the full dashed id (e.g. ``1-1-10``). Chapter
    HTML pages are fetched once in :meth:`list_sections` and parsed
    sections cached for thread-safe :meth:`parse_section` reads.
    """

    jurisdiction = "us-sc"
    doc_type = "statute"
    authority_code = "S.C. Code"
    author_id = "sc-legislature"
    author_name = "South Carolina General Assembly"
    author_url = "https://www.scstatehouse.gov"
    workers = 6

    _titles: tuple[int, ...] = _DEFAULT_TITLES

    def __init__(self, *, generation_date: date | None = None) -> None:
        super().__init__(generation_date=generation_date)
        # (title, chapter_token, section_num) -> (heading, body)
        self._cache: dict[tuple[int, str, str], tuple[str, str]] = {}

    def list_sections(self) -> Iterable[tuple[int, str, str]]:
        for title in self._titles:
            for chapter in _list_title_chapters(title):
                res = http_get(f"{BASE}/t{title:02d}c{chapter}.php")
                if res is None:
                    continue
                html = res.text()
                if not html:
                    continue
                for section_num, heading, body in parse_sections(html, title, chapter):
                    if not body:
                        continue
                    ref = (title, chapter, section_num)
                    self._cache[ref] = (heading, body)
                    yield ref

    def parse_section(self, ref: tuple[int, str, str]) -> SourceSection | None:
        cached = self._cache.get(ref)
        if cached is None:
            return None
        heading, body = cached
        _title, _chapter, section_num = ref
        return SourceSection(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=section_num,
            citation=f"S.C. Code \u00a7 {section_num}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: SourceSection) -> Path:
        """``us-sc/statute/ch-{title}/ch-{title}-sec-{section}.txt``."""
        title = section.work_number.split("-", 1)[0]
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"ch-{title}",
            f"ch-{title}-sec-{safe_section}.txt",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def parse_sections(
    html: str, title_num: int, chapter_token: str
) -> list[tuple[str, str, str]]:
    """Return ``(section_num, heading, body)`` for each section on a page.

    Filters out cross-references appearing in history/editor notes by
    checking that the section id's title+chapter match the page we're
    parsing.
    """
    matches = list(_SECTION_MARKER_RE.finditer(html))
    page_title = str(title_num)
    page_chapter = chapter_token.lstrip("0") or "0"
    out: list[tuple[str, str, str]] = []
    for i, m in enumerate(matches):
        section_num = m.group("full")
        t = m.group("t")
        c = m.group("c").lstrip("0") or "0"
        if t != page_title or c != page_chapter:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        slab = html[start:end]
        heading, body = extract_heading_and_body(slab)
        out.append((section_num, heading, body))
    return out


def extract_heading_and_body(slab: str) -> tuple[str, str]:
    """Split a section slab into ``(heading, body)``.

    Slab shape::

        {heading}<br/>
        <br/>
        {body paragraphs with <br/><br/> between}
        HISTORY: {session-law provenance — dropped}
    """
    history_m = re.search(r"HISTORY\s*:", slab, re.IGNORECASE)
    content = slab[: history_m.start()] if history_m else slab

    head_end = re.search(r"<br\s*/?>", content, re.IGNORECASE)
    if head_end is None:
        # Heading-only slab: preserve paragraph breaks since there's no heading/body split.
        return ("", clean_paragraphs(content))
    heading = clean_text(content[: head_end.start()]).rstrip(".")
    # Body separators are <br/><br/>; preserve them as paragraph breaks.
    body = clean_paragraphs(content[head_end.end() :])
    return (heading, body)


def extract_title_numbers(html: str) -> list[int]:
    """Return title numbers linked from the master index."""
    seen: set[int] = set()
    for m in _TITLE_LINK_RE.finditer(html):
        seen.add(int(m.group("title")))
    return sorted(seen)


def extract_chapter_tokens(html: str, title: int) -> list[str]:
    """Return zero-padded chapter tokens linked from a title page."""
    tokens: list[str] = []
    seen: set[str] = set()
    for m in _CHAPTER_LINK_RE.finditer(html):
        if int(m.group("title")) != title:
            continue
        tok = m.group("chapter")
        if tok not in seen:
            seen.add(tok)
            tokens.append(tok)
    return tokens


def _list_title_chapters(title: int) -> list[str]:
    res = http_get(f"{BASE}/title{title}.php")
    if res is None:
        return []
    return extract_chapter_tokens(res.text(), title)
