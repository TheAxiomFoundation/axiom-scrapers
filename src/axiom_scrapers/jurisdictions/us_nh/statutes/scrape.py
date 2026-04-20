"""New Hampshire Revised Statutes Annotated (RSA) scraper.

Source — `gencourt.state.nh.us/rsa/html`
---------------------------------------
The RSA is a static HTML tree with three discovery layers:

1. Top TOC ``nhtoc.htm`` lists titles by roman numeral (``I``, ``II``,
   ``LXIV``…) linking to ``NHTOC/NHTOC-{title}.htm``.
2. Each title TOC lists chapters linking to
   ``NHTOC/NHTOC-{title}-{chapter}.htm``. Chapters may carry letter
   suffixes (``1-A``, ``21-V``).
3. Each chapter TOC lists sections linking to
   ``../{title}/{chapter}/{chapter}-{section}.htm``.

Each section page looks like::

    <center><h3>Section 1:1</h3></center>
    &nbsp;<b> 1:1 Perambulation of lines &#150;</b>
    <codesect>
    The boundary lines between the state of New Hampshire…
    </codesect>
    <sourcenote><p><b>Source.</b> 2000, 35:1</p></sourcenote>

The heading is the ``<b>`` span with an en-dash separator; the body is
everything inside ``<codesect>``. ``<sourcenote>`` holds citation
history and is dropped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from axiom_scrapers._common import Scraper, Section, clean_text, http_get

BASE = "https://www.gencourt.state.nh.us/rsa/html"

_TITLE_LINK_RE = re.compile(r'href="NHTOC/NHTOC-([A-Z\-]+)\.htm"')

_CHAPTER_LINK_RE = re.compile(
    r'href="NHTOC-([A-Z\-]+?)-([0-9][0-9A-Z\-]*)\.htm"'
)

_CODESECT_RE = re.compile(r"<codesect[^>]*>(.*?)</codesect>", re.DOTALL | re.IGNORECASE)
_HEADING_RE = re.compile(
    r"<b>\s*[0-9A-Za-z:\-]+\s+(.*?)\s*(?:&#150;|-|\u2013|\u2014)\s*</b>",
    re.DOTALL,
)


@dataclass(frozen=True)
class NHSectionRef:
    """Handle for one RSA section: title + chapter + section token."""

    title: str  # e.g. "I", "LXIV"
    chapter: str  # e.g. "1", "1-A"
    section: str  # e.g. "1", "14-a"


class RSAStatutesScraper(Scraper[NHSectionRef]):
    """Scrape the New Hampshire RSA.

    Per-section URL walker; the base runner fetches each page in
    parallel. NH's server is occasionally slow — shared ``http_get``
    retries handle transient errors.
    """

    jurisdiction = "us-nh"
    doc_type = "statute"
    authority_code = "RSA"
    author_id = "nh-legislature"
    author_name = "New Hampshire General Court"
    author_url = "https://www.gencourt.state.nh.us"
    workers = 4

    def list_sections(self) -> Iterable[NHSectionRef]:
        for title in _list_titles():
            for _, chapter in _list_chapters_in_title(title):
                for section in _list_sections_in_chapter(title, chapter):
                    yield NHSectionRef(title, chapter, section)

    def parse_section(self, ref: NHSectionRef) -> Section | None:
        url = f"{BASE}/{ref.title}/{ref.chapter}/{ref.chapter}-{ref.section}.htm"
        res = http_get(url)
        if res is None:
            return None
        heading, body = parse_section_page(res.text())
        if not body:
            return None
        full_cite = f"{ref.chapter}:{ref.section}"
        work_number = f"{ref.chapter}-{ref.section}"
        return Section(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=work_number,
            citation=f"RSA {full_cite}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: Section) -> Path:
        """``us-nh/statute/ch-{chapter}/ch-{chapter}-sec-{section}.xml``.

        ``work_number`` is ``{chapter}-{section}``; split once at the
        *last* dash so alpha-suffixed chapters (``1-A-5``) and dashed
        section tokens (``14-a``) both resolve cleanly. We preserve
        the exact atlas layout here.
        """
        # Chapter is everything up to the last "-{section}" chunk; but
        # chapter may itself contain a dash (1-A). We detect chapter via
        # the known-valid pattern: chapter token ends where the first
        # purely-numeric-or-letter section token begins.
        work = section.work_number
        # Try the two-part split by matching a chapter that contains an
        # optional single letter suffix (1 or 1-A), then the remaining
        # tail is the section.
        m = re.match(r"^(\d+(?:-[A-Z])?)-(.+)$", work)
        chapter = m.group(1) if m else work.split("-", 1)[0]
        safe_section = work.replace("/", "_")
        return Path(
            self.jurisdiction,
            self.doc_type,
            f"ch-{chapter}",
            f"ch-{chapter}-sec-{safe_section}.xml",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def parse_section_page(html: str) -> tuple[str, str]:
    """Return ``(heading, body)`` for a section page.

    The heading is the ``<b>`` span terminated by an en-dash. The body
    is the full contents of ``<codesect>`` with HTML stripped.
    """
    head_m = _HEADING_RE.search(html)
    heading = clean_text(head_m.group(1)) if head_m else ""
    heading = heading.rstrip(".")

    code_m = _CODESECT_RE.search(html)
    body = clean_text(code_m.group(1)) if code_m else ""
    return heading, body


def extract_title_names(html: str) -> list[str]:
    """Return roman-numeral title tokens from the top TOC."""
    return sorted(set(_TITLE_LINK_RE.findall(html)))


def extract_chapter_pairs(html: str, title: str) -> list[tuple[str, str]]:
    """Return ``(title, chapter)`` pairs from a title TOC."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in _CHAPTER_LINK_RE.finditer(html):
        t, ch = m.group(1), m.group(2)
        if t != title:
            continue
        if (t, ch) in seen:
            continue
        seen.add((t, ch))
        out.append((t, ch))
    return out


def extract_section_tokens(html: str, title: str, chapter: str) -> list[str]:
    """Return section tokens linked from a chapter TOC (preserving order)."""
    pattern = (
        rf'href="\.\./{re.escape(title)}/{re.escape(chapter)}'
        rf'/{re.escape(chapter)}-([0-9][0-9A-Za-z\-]*)\.htm"'
    )
    seen: set[str] = set()
    out: list[str] = []
    for match in re.findall(pattern, html):
        if match == "mrg":  # NH's legend/marginal-section placeholder
            continue
        if match not in seen:
            seen.add(match)
            out.append(match)
    return out


def _list_titles() -> list[str]:
    res = http_get(f"{BASE}/nhtoc.htm")
    if res is None:
        return []
    return extract_title_names(res.text())


def _list_chapters_in_title(title: str) -> list[tuple[str, str]]:
    res = http_get(f"{BASE}/NHTOC/NHTOC-{title}.htm")
    if res is None:
        return []
    return extract_chapter_pairs(res.text(), title)


def _list_sections_in_chapter(title: str, chapter: str) -> list[str]:
    res = http_get(f"{BASE}/NHTOC/NHTOC-{title}-{chapter}.htm")
    if res is None:
        return []
    return extract_section_tokens(res.text(), title, chapter)
