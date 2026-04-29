"""Code of Virginia scraper.

Source — `law.lis.virginia.gov/vacode`
--------------------------------------
Three-level navigation on the LIS site:

* ``/vacode`` — title list (74 titles; numbers include decimals like
  ``2.2``, ``10.1``, ``18.2``).
* ``/vacode/title{T}/`` — chapter list.
* ``/vacode/title{T}/chapter{C}/`` — section list.
* ``/vacode/title{T}/chapter{C}/section{T}-{S}/`` — full section.

Each section page wraps the body in::

    <h2> <span id='v0'>§ 1-1</span>. Contents and designation of Code.</h2>
    <section class='body editable' id='edit1' data-table='CoV' data-field='body'>
      <p>Body paragraphs…</p>
      <p>Code 1919, § 1; R. P. 1948, § 1-1.</p>
    </section>

The final paragraph often carries acts-of-assembly provenance ("Code
1919, § 1; …") which we keep inline with the body, matching how
Axiom's other state ingests treat session-law citations.
"""

from __future__ import annotations

import re
import ssl
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from axiom_scrapers._common import Scraper, SourceSection, clean_paragraphs, clean_text, http_get

BASE = "https://law.lis.virginia.gov/vacode"

# VA LIS ships with outdated root CA bundle in some containers; system
# trust works on macOS but may need relaxing in CI. Scraper callers can
# set a permissive context via _common.http.opener if needed.
_DEFAULT_CTX = ssl.create_default_context()

# VA LIS emits single-quoted hrefs (``href='/vacode/...'``) inside
# dynamically-generated TOC blocks and double-quoted hrefs elsewhere.
# The link regexes accept either quoting style.
_TITLE_LINK_RE = re.compile(
    r"""href=['"]/vacode/title(?P<title>[0-9]+(?:\.[0-9]+)?)/?['"]""",
    re.IGNORECASE,
)
_CHAPTER_LINK_RE = re.compile(
    r"""href=['"]/vacode/title(?P<title>[0-9]+(?:\.[0-9]+)?)"""
    r"""/chapter(?P<chapter>[0-9]+(?:\.[0-9]+)?)/?['"]""",
    re.IGNORECASE,
)
_SECTION_LINK_RE = re.compile(
    r"""href=['"]/vacode/title(?P<title>[0-9]+(?:\.[0-9]+)?)"""
    r"""/chapter(?P<chapter>[0-9]+(?:\.[0-9]+)?)"""
    r"""/section(?P<section>[0-9A-Za-z\.\-:]+)/?['"]""",
    re.IGNORECASE,
)

# Heading is `<h2> <span id='v0'>§ {section}</span>. Heading.</h2>`
_HEADING_RE = re.compile(
    r"<h2>\s*<span[^>]*id=['\"]v0['\"][^>]*>\s*§\s*(?P<section>[0-9A-Za-z\.\-:]+)\s*</span>"
    r"\.\s*(?P<heading>[^<]*?)\s*\.?\s*</h2>",
    re.IGNORECASE | re.DOTALL,
)

# Body lives in `<section class='body editable' ...>...</section>`
_BODY_RE = re.compile(
    r"<section[^>]*class=['\"][^'\"]*\bbody\b[^'\"]*['\"][^>]*>(?P<body>.*?)</section>",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class VASectionRef:
    """Handle for one Code of Virginia section."""

    title: str  # e.g. "1", "2.2", "18.2"
    chapter: str  # e.g. "1", "4.1"
    section: str  # full section id: "1-1", "2.2-4007.01"


class VACodeStatutesScraper(Scraper[VASectionRef]):
    """Scrape the Code of Virginia.

    Per-section URL walker. LIS serves each section as its own HTML
    page with a consistent ``<h2> + <section class='body editable'>``
    shape — no caching needed.
    """

    jurisdiction = "us-va"
    doc_type = "statute"
    authority_code = "Va. Code Ann."
    author_id = "va-legislature"
    author_name = "Virginia General Assembly"
    author_url = "https://law.lis.virginia.gov"
    workers = 6

    def list_sections(self) -> Iterable[VASectionRef]:
        for title in _list_titles():
            for chapter in _list_chapters(title):
                for section in _list_sections(title, chapter):
                    yield VASectionRef(title, chapter, section)

    def parse_section(self, ref: VASectionRef) -> SourceSection | None:
        url = f"{BASE}/title{ref.title}/chapter{ref.chapter}/section{ref.section}/"
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
            work_number=ref.section,
            citation=f"Va. Code Ann. § {ref.section}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: SourceSection) -> Path:
        """``us-va/statutes/title-{T}/title-{T}-sec-{id}.txt``.

        VA section ids are ``{title}-{section}`` (e.g. ``1-1``,
        ``2.2-4007.01``), so the title prefix comes from splitting on
        the first ``-``.
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

    Heading comes from the ``<h2>`` with a ``<span id='v0'>`` child;
    body comes from the ``<section class='body editable'>`` block.
    """
    head_m = _HEADING_RE.search(html)
    if not head_m:
        return None
    heading = clean_text(head_m.group("heading")).rstrip(".").strip()

    body_m = _BODY_RE.search(html)
    if not body_m:
        return None
    # VA keeps the provenance ("Code 1919, § 1; …") as the last <p>
    # inside the body; clean_paragraphs preserves the paragraph break
    # so consumers can see it as a distinct segment.
    body = clean_paragraphs(body_m.group("body")).strip()
    return heading, body


def extract_title_tokens(html: str) -> list[str]:
    """Return VA title tokens (``1``, ``2.2``, ``10.1``) from the root TOC."""
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
    res = http_get(f"{BASE}/title{title}/")
    if res is None:
        return []
    return extract_chapter_tokens(res.text(), title)


def _list_sections(title: str, chapter: str) -> list[str]:
    res = http_get(f"{BASE}/title{title}/chapter{chapter}/")
    if res is None:
        return []
    return extract_section_tokens(res.text(), title, chapter)
