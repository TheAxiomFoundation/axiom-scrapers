"""Tennessee Code Annotated (TCA) scraper.

Source — ``law.justia.com/codes/tennessee``
-------------------------------------------
TCA is a commercially published work (LexisNexis). Tennessee does not
offer an unauthenticated official HTML mirror; ``advance.lexis.com``
requires JS + cookie fingerprinting, and ``capitol.tn.gov`` hosts only
bills, not compiled statutes. Justia's Tennessee mirror is the
structured free source — same TCA text, per-section URL walker, stable
HTML shape.

Site shape
~~~~~~~~~~
Four-level navigation, year-scoped (Justia archives one snapshot per
year; we pin ``YEAR`` to the last complete publication year):

* ``/codes/tennessee/{year}/`` — title list (68 titles, 1..71 with
  gaps where titles were repealed or never assigned).
* ``/codes/tennessee/{year}/title-{T}/`` — chapter list.
* ``/codes/tennessee/{year}/title-{T}/chapter-{C}/`` — section list.
* ``/codes/tennessee/{year}/title-{T}/chapter-{C}/section-{T}-{C}-{S}/`` —
  full section.

Section pages wrap the body in ``<div id="codes-content">...</div>``
and render the heading as an ``<h1 class="heading-1">`` that concatenates
year / title / chapter / section with ``<br/>`` separators:

    <h1 class="heading-1">
      2021 Tennessee Code<br/>
      Title 1 - Code and Statutes<br/>
      Chapter 1 - Code Commission<br/>
      &sect; 1-1-103. Staff Services for Commission
    </h1>

We pull the short section heading from the segment after the last
``<br/>``, strip the ``§ {id}.`` prefix and trailing period to match
what other state scrapers emit (``"Staff Services for Commission"``).

Coverage caveat
~~~~~~~~~~~~~~~
Justia sits behind Cloudflare and returns ``403`` to non-residential
IPs — including CI runners and most cloud egress. Live scrapes from
such networks will silently produce zero sections (the shared
:func:`axiom_scrapers._common.http.http_get` soft-fails on 4xx). The
parser itself is exercised against real Justia fixtures so offline
tests always pass; a run that writes 0 sections is the blocked-source
signal rather than a parser bug.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from axiom_scrapers._common import Scraper, SourceSection, clean_paragraphs, clean_text, http_get

#: Last complete publication year Justia archives for TCA. Bump when a
#: new year's snapshot lands.
YEAR = "2021"

BASE = f"https://law.justia.com/codes/tennessee/{YEAR}"

# Title links on the year page: `/codes/tennessee/2021/title-1/` …
# Titles are numeric; some are absent (repealed) but the TOC only
# surfaces live titles, so no pre-filtering needed.
_TITLE_LINK_RE = re.compile(
    rf'href="/codes/tennessee/{YEAR}/title-(?P<title>[0-9]+[A-Za-z]*)/?"',
    re.IGNORECASE,
)

# Chapter links on a title page: `/codes/tennessee/2021/title-1/chapter-1/`.
_CHAPTER_LINK_RE = re.compile(
    rf'href="/codes/tennessee/{YEAR}/title-(?P<title>[0-9]+[A-Za-z]*)'
    rf'/chapter-(?P<chapter>[0-9]+[A-Za-z]*)/?"',
    re.IGNORECASE,
)

# Section links on a chapter page. Section ids are dashed triples
# ``{title}-{chapter}-{section}`` where the trailing component may
# include letters (``1-1-101``, ``67-8-303A``) and dots for later-
# added subsections (``1-2-112.5``).
_SECTION_LINK_RE = re.compile(
    rf'href="/codes/tennessee/{YEAR}/title-(?P<title>[0-9]+[A-Za-z]*)'
    rf'/chapter-(?P<chapter>[0-9]+[A-Za-z]*)'
    rf'/section-(?P<section>[0-9A-Za-z.\-]+)/?"',
    re.IGNORECASE,
)

# Heading: ``<h1 class="heading-1">… <br/> &sect; {id}. {heading}</h1>``.
# Justia emits ``&sect;`` as the literal HTML entity before the HTML
# is decoded — we match on the entity form. The ``.*?<br/>`` is greedy-
# lazy so the last ``<br/>`` before ``&sect;`` is the segment we slice on.
_HEADING_RE = re.compile(
    r'<h1[^>]*class="heading-1"[^>]*>(?P<full>.*?)</h1>',
    re.DOTALL | re.IGNORECASE,
)
_HEADING_LAST_LINE_RE = re.compile(
    r"&sect;\s*(?P<section>[0-9A-Za-z.\-]+)\s*\.\s*(?P<heading>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)

# Body: ``<div id="codes-content">...</div>``. Allow attributes after
# the id and nested tags inside.
_BODY_RE = re.compile(
    r'<div[^>]*id="codes-content"[^>]*>(?P<body>.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)


class TCAStatutesScraper(Scraper[tuple[str, str, str]]):
    """Scrape Tennessee Code Annotated via Justia's mirror.

    ``SectionRef`` is ``(title, chapter, section_id)`` — the pieces
    needed to rebuild a section URL. The fetcher is per-section; no
    caching is required because each section page is self-contained
    and Justia's response is ~10 KB each.
    """

    jurisdiction = "us-tn"
    doc_type = "statute"
    authority_code = "Tenn. Code Ann."
    author_id = "tn-legislature"
    author_name = "Tennessee General Assembly"
    author_url = "https://www.capitol.tn.gov"
    # Justia is behind Cloudflare; keep workers conservative to avoid
    # tripping per-IP rate limits when the source is reachable.
    workers = 4

    def list_sections(self) -> Iterable[tuple[str, str, str]]:
        for title in _list_titles():
            for chapter in _list_chapters(title):
                for section in _list_sections(title, chapter):
                    yield (title, chapter, section)

    def parse_section(self, ref: tuple[str, str, str]) -> SourceSection | None:
        title, chapter, section_id = ref
        url = f"{BASE}/title-{title}/chapter-{chapter}/section-{section_id}/"
        res = http_get(url)
        if res is None:
            return None
        parsed = parse_section_page(res.text())
        if parsed is None:
            return None
        heading, body = parsed
        if not body:
            return None
        return SourceSection(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=section_id,
            citation=f"Tenn. Code Ann. § {section_id}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: SourceSection) -> Path:
        """``us-tn/statutes/title-{T}/title-{T}-sec-{id}.txt``.

        Section ids are ``{title}-{chapter}-{section}``; the title
        segment for the directory comes from splitting on the first
        ``-``. Mirrors the VA layout so ingest paths across states are
        consistent.
        """
        title = section.work_number.split("-", 1)[0]
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"title-{title}",
            f"title-{title}-sec-{safe_section}.txt",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def parse_section_page(html: str) -> tuple[str, str] | None:
    """Return ``(heading, body)`` or ``None`` if the page isn't a section.

    Heading comes from the last ``<br/>`` segment inside the
    ``heading-1`` ``<h1>``; body comes from ``<div id="codes-content">``.
    """
    head_m = _HEADING_RE.search(html)
    if not head_m:
        return None
    # The heading is built up as year / title / chapter / section on
    # consecutive <br/> lines. Only the last line carries the short
    # section heading. Split on <br/> (with optional space/slash) and
    # take the tail.
    segments = re.split(r"<br\s*/?>", head_m.group("full"), flags=re.IGNORECASE)
    last = segments[-1] if segments else ""
    last_clean = last.strip()
    last_m = _HEADING_LAST_LINE_RE.search(last_clean)
    if not last_m:
        return None
    heading = clean_text(last_m.group("heading")).rstrip(".").strip()

    body_m = _BODY_RE.search(html)
    if not body_m:
        return None
    body = clean_paragraphs(body_m.group("body")).strip()
    return heading, body


def extract_title_tokens(html: str) -> list[str]:
    """Return TCA title tokens (``1``, ``2``, ``67`` …) from the code TOC."""
    seen: dict[str, None] = {}
    for m in _TITLE_LINK_RE.finditer(html):
        seen[m.group("title")] = None
    return list(seen)


def extract_chapter_tokens(html: str, title: str) -> list[str]:
    """Return chapter tokens under ``title`` from a title TOC."""
    seen: dict[str, None] = {}
    for m in _CHAPTER_LINK_RE.finditer(html):
        if m.group("title") != title:
            continue
        seen[m.group("chapter")] = None
    return list(seen)


def extract_section_tokens(html: str, title: str, chapter: str) -> list[str]:
    """Return full section ids under ``{title}-{chapter}`` from a chapter TOC."""
    seen: dict[str, None] = {}
    for m in _SECTION_LINK_RE.finditer(html):
        if m.group("title") != title or m.group("chapter") != chapter:
            continue
        seen[m.group("section")] = None
    return list(seen)


def _list_titles() -> list[str]:
    res = http_get(f"{BASE}/")
    if res is None:
        return []
    return extract_title_tokens(res.text())


def _list_chapters(title: str) -> list[str]:
    res = http_get(f"{BASE}/title-{title}/")
    if res is None:
        return []
    return extract_chapter_tokens(res.text(), title)


def _list_sections(title: str, chapter: str) -> list[str]:
    res = http_get(f"{BASE}/title-{title}/chapter-{chapter}/")
    if res is None:
        return []
    return extract_section_tokens(res.text(), title, chapter)
