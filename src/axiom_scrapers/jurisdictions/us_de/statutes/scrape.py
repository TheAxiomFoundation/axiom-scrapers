"""Delaware Code scraper.

Source — `delcode.delaware.gov`
------------------------------
The Delaware Code Online is a static HTML site. Top-level index lists
31 titles at ``/title{N}/index.html``. Each title page links to
chapters ``/title{N}/c{CCC}/index.html``. Chapter slugs are numeric
(``c001``) with occasional alpha suffixes (``c020a``).

Two chapter shapes appear:

1. **Leaf chapter.** The chapter page itself holds every section as a
   ``<div class="Section"><div class="SectionHead" id="{sec}">...</div>
   <p class="subsection">...</p>...</div>`` block.

2. **Split chapter.** The chapter page is a TOC of
   ``/title{N}/c{CCC}/sc{SC}/index.html`` subchapter links; each
   subchapter page follows the leaf shape.

Parse strategy
--------------
:meth:`list_sections` walks titles → chapters, detecting leaf vs split
pages, fetches each leaf page, parses out every section, caches
``(heading, body)`` on ``self._cache`` keyed by ``SectionRef``, and
yields the refs. :meth:`parse_section` is a cache read; no duplicate
network calls.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from axiom_scrapers._common import Scraper, Section, clean_text, http_get

BASE = "https://delcode.delaware.gov"

# Titles 1..31 — the full Delaware Code.
_DEFAULT_TITLES = tuple(range(1, 32))

_CHAPTER_LINK = re.compile(
    r'href="[^"]*/title(?P<title>\d+)/c(?P<chapter>[0-9a-z]+)/index\.html"',
    re.IGNORECASE,
)

_SUBCHAPTER_LINK = re.compile(
    r'href="[^"]*/title(?P<title>\d+)/c(?P<chapter>[0-9a-z]+)'
    r"/sc(?P<sub>[0-9a-z]+)/index\.html\"",
    re.IGNORECASE,
)

_SECTION_BLOCK = re.compile(
    r'<div\s+class="Section"\s*>(.*?)(?=<div\s+class="Section"\s*>|</div>\s*</div>\s*</div>)',
    re.DOTALL | re.IGNORECASE,
)

_SECTION_HEAD = re.compile(
    r'<div\s+class="SectionHead"\s+id="(?P<id>[^"]+)"\s*>(?P<inner>.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class DESectionRef:
    """Handle for one DE section — title/chapter/subchapter/section."""

    title: int
    chapter: str  # e.g. "c001", "c020a"
    subchapter: str  # e.g. "sc01" or "" if inline
    section_id: str


class DelCodeStatutesScraper(Scraper[DESectionRef]):
    """Scrape the Delaware Code.

    ``SectionRef`` is a :class:`DESectionRef`; pages are fetched once
    in :meth:`list_sections` and parsed sections cached on
    ``self._cache`` for thread-safe reads by :meth:`parse_section`.
    """

    jurisdiction = "us-de"
    doc_type = "statute"
    authority_code = "Del. C."
    author_id = "de-legislature"
    author_name = "Delaware General Assembly"
    author_url = "https://legis.delaware.gov"
    workers = 6

    _titles: tuple[int, ...] = _DEFAULT_TITLES

    def __init__(self, *, generation_date: date | None = None) -> None:
        super().__init__(generation_date=generation_date)
        self._cache: dict[DESectionRef, tuple[str, str]] = {}

    def list_sections(self) -> Iterable[DESectionRef]:
        for title in self._titles:
            for chapter in _list_chapter_slugs(title):
                chapter_html = _fetch(f"{BASE}/title{title}/{chapter}/index.html")
                if not chapter_html:
                    continue
                # Leaf chapter — sections are inline.
                if _has_sections_inline(chapter_html):
                    for section_id, heading, body in parse_sections(chapter_html):
                        ref = DESectionRef(title, chapter, "", section_id)
                        self._cache[ref] = (heading, body)
                        yield ref
                    continue
                # Split chapter — walk subchapters.
                for sub in _extract_subchapter_slugs(chapter_html, title, chapter):
                    sub_html = _fetch(f"{BASE}/title{title}/{chapter}/{sub}/index.html")
                    if not sub_html:
                        continue
                    for section_id, heading, body in parse_sections(sub_html):
                        ref = DESectionRef(title, chapter, sub, section_id)
                        self._cache[ref] = (heading, body)
                        yield ref

    def parse_section(self, ref: DESectionRef) -> Section | None:
        cached = self._cache.get(ref)
        if cached is None:
            return None
        heading, body = cached
        if not body or body.strip().lower() in {"repealed.", "[repealed]", "repealed"}:
            return None
        citation = f"{ref.title} Del. C. § {ref.section_id}"
        work_number = f"{ref.title}-{ref.section_id}"
        return Section(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=work_number,
            citation=citation,
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: Section) -> Path:
        """Nest by title and chapter.

        ``work_number`` is ``{title}-{section}``; we split once.
        """
        title, section_id = section.work_number.split("-", 1)
        safe_section = section_id.replace("/", "_")
        return Path(
            self.jurisdiction,
            self.doc_type,
            f"title-{title}",
            f"title-{title}-sec-{safe_section}.xml",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def parse_sections(html: str) -> list[tuple[str, str, str]]:
    """Extract every ``(section_id, heading, body)`` from a chapter page."""
    out: list[tuple[str, str, str]] = []
    for block_m in _SECTION_BLOCK.finditer(html):
        block = block_m.group(1)
        head_m = _SECTION_HEAD.search(block)
        if not head_m:
            continue
        section_id = head_m.group("id").strip()
        head_inner = clean_text(head_m.group("inner"))
        heading = (
            re.sub(
                r"^\s*§\s*" + re.escape(section_id) + r"\s*\.\s*",
                "",
                head_inner,
            )
            .strip()
            .rstrip(".")
        )
        body_html = block[head_m.end() :]
        # Drop trailing history anchors; keep everything up to last </p>.
        last_p = body_html.rfind("</p>")
        if last_p != -1:
            body_html = body_html[: last_p + len("</p>")]
        else:
            body_html = re.sub(
                r"<a\s[^>]*>.*?</a>", "", body_html, flags=re.DOTALL | re.IGNORECASE
            )
        body = clean_text(body_html)
        out.append((section_id, heading, body))
    return out


def _has_sections_inline(html: str) -> bool:
    """Distinguish a leaf chapter (sections inline) from a split chapter."""
    return bool(re.search(r'<div\s+class="SectionHead"', html, re.IGNORECASE))


def _extract_subchapter_slugs(html: str, title: int, chapter: str) -> list[str]:
    """Return ``sc{XX}`` slugs for a split chapter."""
    chapter_core = chapter[1:]  # strip leading "c"
    out: list[str] = []
    seen: set[str] = set()
    for m in _SUBCHAPTER_LINK.finditer(html):
        if int(m.group("title")) != title or m.group("chapter").lower() != chapter_core:
            continue
        slug = f"sc{m.group('sub').lower()}"
        if slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def _list_chapter_slugs(title: int) -> list[str]:
    html = _fetch(f"{BASE}/title{title}/index.html")
    if not html:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _CHAPTER_LINK.finditer(html):
        if int(m.group("title")) != title:
            continue
        slug = f"c{m.group('chapter').lower()}"
        if slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def _fetch(url: str) -> str:
    res = http_get(url)
    return res.text() if res else ""
