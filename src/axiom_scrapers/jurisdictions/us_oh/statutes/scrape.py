"""Ohio Revised Code (RC) scraper.

Source — ``codes.ohio.gov/ohio-revised-code``
---------------------------------------------
Ohio serves the Revised Code as static server-rendered HTML (not a
SPA). The tree is three-deep:

* **Root** ``/ohio-revised-code`` — links to 32 top-level ``title-N``
  pages plus one ``general-provisions`` bucket.
* **Title** ``/ohio-revised-code/title-{N}`` — links to chapter pages
  ``/ohio-revised-code/chapter-{N}``.
* **Chapter** ``/ohio-revised-code/chapter-{N}`` — links to individual
  section pages ``/ohio-revised-code/section-{N}``.

Each section page contains:

    <h1>Section {number} <span class='codes-separator'>|</span> {heading}.</h1>
    <section class="laws-body"><span><p>...</p><p>...</p></span></section>

with an optional trailing ``<p>Last updated ...</p>`` notice that we
strip before emitting AKN.

Repealed / missing sections still return HTTP 200 but omit
``<section class="laws-body">``; :func:`_parse_section_html` returns
``None`` for those, which the base runner counts as a skip.

Relative-href caveat
--------------------
Chapter pages emit ``href="section-5747.01"`` (relative), not the
absolute ``/ohio-revised-code/section-5747.01``. The discovery regexes
match both shapes so we can accept the root page's absolute hrefs and
the chapter page's relative ones.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from axiom_scrapers._common import FetchResult, Scraper, Section, clean_text, http_get

BASE = "https://codes.ohio.gov"
ROOT = f"{BASE}/ohio-revised-code"

# Discovery regexes — accept both absolute and relative hrefs because
# OH chapter pages emit ``href="section-5747.01"`` (sibling-relative)
# while title/root pages emit ``href="ohio-revised-code/title-1"``
# (repo-root-relative).
_TITLE_LINK = re.compile(
    r'href="/?(?:ohio-revised-code/)?(title-[0-9]+|general-provisions)"',
)
_CHAPTER_LINK = re.compile(
    r'href="/?(?:ohio-revised-code/)?chapter-(?P<chapter>[0-9A-Za-z.\-]+?)"',
)
_SECTION_LINK = re.compile(
    r'href="/?(?:ohio-revised-code/)?section-(?P<section>[0-9A-Za-z.\-]+?)"',
)

# Section-page extractors.
_H1_RE = re.compile(
    r"<h1>\s*Section\s+(?P<num>[0-9A-Za-z.\-]+)\s*"
    r"<span[^>]*class=['\"]codes-separator['\"][^>]*>\|</span>\s*"
    r"(?P<heading>.*?)</h1>",
    re.DOTALL,
)
_BODY_RE = re.compile(
    r'<section\s+class="laws-body">(?P<body>.*?)</section>',
    re.DOTALL,
)

# "Last updated March 30, 2026 at 9:13 AM" — LSC publishes this as
# the last paragraph of every section body. Strip it so the emitted
# AKN body is just statutory text.
_LAST_UPDATED_TAIL = re.compile(r"\s*Last updated[^\n]*$")


class RCStatutesScraper(Scraper[str]):
    """One scraper instance = one full RC scrape.

    ``SectionRef`` is the section-page URL (``str``). The base class's
    parallel runner hands each URL to :meth:`parse_section`.
    """

    jurisdiction = "us-oh"
    doc_type = "statute"
    authority_code = "RC"
    author_id = "oh-lsc"
    author_name = "Ohio Legislative Service Commission"
    author_url = "https://codes.ohio.gov"
    workers = 6

    def list_sections(self) -> Iterable[str]:
        """Walk root → titles → chapters → sections, yielding section URLs."""
        seen_chapters: set[str] = set()
        seen_sections: set[str] = set()
        for title_slug in _list_title_slugs():
            for chapter in _list_chapters_in_title(title_slug):
                if chapter in seen_chapters:
                    continue
                seen_chapters.add(chapter)
                for section in _list_sections_in_chapter(chapter):
                    if section in seen_sections:
                        continue
                    seen_sections.add(section)
                    yield f"{ROOT}/section-{section}"

    def parse_section(self, url: str) -> Section | None:
        res = http_get(url)
        if res is None:
            return None
        return _parse_section_html(
            html=res.text(),
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: Section) -> Path:
        """Nest by chapter (the bit before the first ``.``) so the tree stays browseable."""
        chapter = section.work_number.split(".", 1)[0]
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"ch-{chapter}",
            f"{section.work_number}.xml",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def _parse_section_html(html: str, generation_date: date) -> Section | None:
    """Turn one section-page HTML into a :class:`Section`, or ``None``.

    Parse strategy:

    * ``<section class="laws-body">`` is the authoritative body marker.
      If it's absent the section is repealed/missing and we skip.
    * ``<h1>Section N | Heading.</h1>`` encodes the section number and
      heading; trailing period on the heading is stripped.
    * The trailing ``Last updated ...`` notice (an LSC audit trail, not
      statute text) is removed from the body.

    Returns ``None`` when there's no ``laws-body`` or the body is empty
    after cleaning.
    """
    body_m = _BODY_RE.search(html)
    if not body_m:
        return None
    head_m = _H1_RE.search(html)
    if not head_m:
        return None
    section_num = head_m.group("num").strip()
    heading = clean_text(head_m.group("heading")).rstrip(".").strip()
    body = clean_text(body_m.group("body"))
    body = _LAST_UPDATED_TAIL.sub("", body).strip()
    if not body:
        return None

    citation = f"R.C. \u00a7 {section_num}"
    return Section(
        jurisdiction="us-oh",
        doc_type="statute",
        authority_code="RC",
        work_number=section_num,
        citation=citation,
        heading=heading,
        body=body,
        author_id="oh-lsc",
        author_name="Ohio Legislative Service Commission",
        author_url="https://codes.ohio.gov",
        generation_date=generation_date,
    )


def _list_title_slugs() -> list[str]:
    """Return the title slugs linked from the RC root (``title-1``, ``general-provisions``, ...)."""
    html = _fetch_text(ROOT)
    seen: list[str] = []
    for m in _TITLE_LINK.finditer(html):
        slug = m.group(1)
        if slug not in seen:
            seen.append(slug)
    return seen


def _list_chapters_in_title(title_slug: str) -> list[str]:
    """Return chapter tokens (``"1"``, ``"5747"``) linked from a title page."""
    html = _fetch_text(f"{ROOT}/{title_slug}")
    seen: list[str] = []
    for m in _CHAPTER_LINK.finditer(html):
        chapter = m.group("chapter")
        if chapter not in seen:
            seen.append(chapter)
    return seen


def _list_sections_in_chapter(chapter: str) -> list[str]:
    """Return section numbers (``"5747.01"``, ``"1.01"``) for a chapter page.

    Filters cross-chapter links by requiring the section's chapter
    prefix (everything before the first ``.``) to match the chapter we
    fetched; otherwise a section linking to another chapter's section
    would get double-scraped under the wrong chapter.
    """
    html = _fetch_text(f"{ROOT}/chapter-{chapter}")
    seen: list[str] = []
    for m in _SECTION_LINK.finditer(html):
        section = m.group("section")
        prefix = section.split(".", 1)[0]
        if prefix != chapter:
            continue
        if section not in seen:
            seen.append(section)
    return seen


def _fetch_text(url: str) -> str:
    """Fetch a URL and return text, returning ``""`` on failure."""
    res: FetchResult | None = http_get(url)
    return res.text() if res else ""
