"""Washington Revised Code (RCW) scraper.

Source — `app.leg.wa.gov/RCW`
-----------------------------
ASP.NET site with a ``?cite=`` query-string key. Navigation layers:

* ``/RCW/`` — title index (titles like ``1``, ``9A``, ``28A``)
* ``?Cite={title}`` — chapter list, links to ``?cite={title}.{chapter}``
* ``?cite={chapter}`` — section list, links to ``?cite={chapter}.{sec}``
* ``?cite={section}`` — section page with heading + body

Each section page has::

    <h2><!-- field: CaptionsTitles -->Heading<!-- field: -->...</h2>
    <div id="contentWrapper" class="section-page">
      <p>Body paragraphs...</p>
      ...
      <div style="margin-top:15pt">[ 1951 c 5 § 2; ...]</div>
      <h3>Notes:</h3>
      <p>...editor notes — dropped...</p>
    </div>

Session-law bracketed citations and ``Notes:`` blocks are stripped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import quote

from axiom_scrapers._common import Scraper, Section, clean_text, http_get

BASE = "https://app.leg.wa.gov/RCW"

_CONTENT_WRAPPER_RE = re.compile(
    r"<div\s+id=['\"]contentWrapper['\"][^>]*>(?P<body>.*?)</div>\s*"
    r"<div\s+id=['\"]ContentPlaceHolder1_pnlExpanded['\"]",
    re.DOTALL | re.IGNORECASE,
)

_CITATION_NOT_FOUND_RE = re.compile(r"Citation\s+not\s+found", re.IGNORECASE)

_TITLE_LINK_RE = re.compile(r'default\.aspx\?Cite=([0-9]+[A-Z]?)"')

_CHAPTER_LINK_RE = re.compile(
    r"default\.aspx\?cite=([0-9]+[A-Z]?\.[0-9]+[A-Z]?)(?=['\"])",
    re.IGNORECASE,
)

_SECTION_LINK_RE = re.compile(
    r"default\.aspx\?cite=([0-9]+[A-Z]?\.[0-9]+[A-Z]?\.[0-9A-Za-z]+)(?=['\"&])",
    re.IGNORECASE,
)

_HEADING_RE = re.compile(
    r"<h2>\s*<!--\s*field:\s*CaptionsTitles\s*-->(.*?)<!--\s*field:",
    re.DOTALL | re.IGNORECASE,
)


class RCWStatutesScraper(Scraper[str]):
    """Scrape the Washington Revised Code.

    ``SectionRef`` is the full three-segment section token
    (``"1.04.010"``). Chapter is the first two segments.
    """

    jurisdiction = "us-wa"
    doc_type = "statute"
    authority_code = "RCW"
    author_id = "wa-legislature"
    author_name = "Washington State Legislature"
    author_url = "https://app.leg.wa.gov"
    workers = 6

    def list_sections(self) -> Iterable[str]:
        for title in _list_titles():
            for chapter in _list_chapters_for_title(title):
                yield from _list_sections_for_chapter(chapter)

    def parse_section(self, ref: str) -> Section | None:
        res = http_get(f"{BASE}/default.aspx?cite={quote(ref)}")
        if res is None:
            return None
        parsed = parse_section_page(res.text())
        if parsed is None:
            return None
        heading, body = parsed
        if not body:
            return None
        return Section(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=ref,
            citation=f"RCW {ref}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: Section) -> Path:
        """``us-wa/statute/ch-{title}.{chapter}/ch-{...}-sec-{section}.xml``."""
        parts = section.work_number.split(".")
        chapter = ".".join(parts[:2]) if len(parts) >= 3 else parts[0]
        safe_chapter = chapter.replace("/", "_")
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self.doc_type,
            f"ch-{safe_chapter}",
            f"ch-{safe_chapter}-sec-{safe_section}.xml",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def parse_section_page(html: str) -> tuple[str, str] | None:
    """Return ``(heading, body)`` or ``None`` if not a section page.

    Skips chapter-index or "Citation not found" pages (the same
    ``default.aspx`` endpoint serves all three views).
    """
    if _CITATION_NOT_FOUND_RE.search(html):
        return None
    # Guard against the server silently resolving to a chapter index.
    if "class='section-page'" not in html and 'class="section-page"' not in html:
        return None

    heading = ""
    head_m = _HEADING_RE.search(html)
    if head_m:
        heading = clean_text(head_m.group(1))
    if not heading:
        tm = re.search(r"<title[^>]*>\s*(.*?)\s*</title>", html, re.DOTALL)
        if tm:
            tt = clean_text(tm.group(1))
            if ":" in tt:
                heading = tt.split(":", 1)[1].strip()
    heading = heading.rstrip(".")

    body_html = _extract_content_wrapper(html)
    if body_html is None:
        return None
    body_html = re.split(r"<h3[^>]*>\s*Notes:\s*</h3>", body_html, maxsplit=1)[0]
    body_html = re.sub(
        r"<div[^>]*style=\"[^\"]*margin-top:\s*15pt[^\"]*\"[^>]*>\s*\[.*?\]\s*</div>",
        "",
        body_html,
        flags=re.DOTALL,
    )
    body_html = re.sub(r"\[\s*(?:19|20)\d{2}[^\[\]]*?\]", "", body_html)

    paragraphs: list[str] = []
    for chunk in re.split(r"</(?:div|p)>", body_html, flags=re.IGNORECASE):
        text = clean_text(chunk)
        if text:
            paragraphs.append(text)
    body = "\n\n".join(paragraphs).strip()
    return (heading, body)


def extract_title_tokens(html: str) -> list[str]:
    """Return RCW title tokens from the index page."""
    m = re.search(
        r'<table[^>]*id="ContentPlaceHolder1_dgSections"[^>]*>(.*?)</table>',
        html,
        re.DOTALL,
    )
    scope = m.group(1) if m else html
    tokens = _TITLE_LINK_RE.findall(scope)
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def extract_chapter_tokens(html: str, title: str) -> list[str]:
    """Return chapter tokens for ``title`` from a title page wrapper."""
    body = _extract_content_wrapper(html)
    if body is None:
        return []
    tokens = _CHAPTER_LINK_RE.findall(body)
    prefix = f"{title}."
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if not t.startswith(prefix):
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def extract_section_tokens(html: str, chapter: str) -> list[str]:
    """Return section tokens for ``chapter`` from a chapter page wrapper."""
    body = _extract_content_wrapper(html)
    if body is None:
        return []
    tokens = _SECTION_LINK_RE.findall(body)
    prefix = f"{chapter}."
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if not t.startswith(prefix):
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _extract_content_wrapper(html: str) -> str | None:
    m = _CONTENT_WRAPPER_RE.search(html)
    return m.group("body") if m else None


def _list_titles() -> list[str]:
    res = http_get(f"{BASE}/")
    if res is None:
        return []
    return extract_title_tokens(res.text())


def _list_chapters_for_title(title: str) -> list[str]:
    res = http_get(f"{BASE}/default.aspx?Cite={quote(title)}")
    if res is None:
        return []
    return extract_chapter_tokens(res.text(), title)


def _list_sections_for_chapter(chapter: str) -> list[str]:
    res = http_get(f"{BASE}/default.aspx?cite={quote(chapter)}")
    if res is None:
        return []
    return extract_section_tokens(res.text(), chapter)
