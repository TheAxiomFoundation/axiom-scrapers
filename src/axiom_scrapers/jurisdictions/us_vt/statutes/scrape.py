"""Vermont Statutes Annotated (VSA) scraper.

Source — `legislature.vermont.gov/statutes`
-------------------------------------------
Four-layer navigation on a SilverStripe site::

    /statutes/                         — title index
    /statutes/title/{T}                — chapter list
    /statutes/chapter/{T}/{C}          — section list
    /statutes/section/{T}/{C}/{S}      — full section

Title tokens vary in shape (``01``, ``32``, ``09A``, ``10APPENDIX``);
chapters are zero-padded numerics; section tokens are zero-padded
with optional trailing letter (``05811``, ``05825a``, ``05862c``).

Each section page has::

    <b>(Cite as: 32 V.S.A. § 5811)</b>
    <ul class="item-list statutes-detail">
      <li><p>§ 5811. Definitions.</p> <p>body paragraphs…</p></li>
    </ul>

Trailing history note ``(Added 1966, No. 61 …)`` is part of the last
paragraph and kept inline with the body (matching Axiom behavior).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from axiom_scrapers._common import Scraper, SourceSection, http_get

BASE = "https://legislature.vermont.gov"
ROOT = f"{BASE}/statutes/"

_TITLE_LINK_RE = re.compile(r'href="/?statutes/title/(?P<title>[0-9A-Za-z]+)"')

_CHAPTER_LINK_RE = re.compile(
    r'href="/?statutes/chapter/(?P<title>[0-9A-Za-z]+)/(?P<chapter>[0-9A-Za-z]+)"'
)

_SECTION_LINK_RE = re.compile(
    r'href="/?statutes/section/'
    r"(?P<title>[0-9A-Za-z]+)/(?P<chapter>[0-9A-Za-z]+)/(?P<section>[0-9A-Za-z]+)\""
)

_CITE_RE = re.compile(
    r"\(\s*Cite as\s*:\s*(?P<title>[0-9A-Za-z]+)\s+V\.?S\.?A\.?\s*§+\s*"
    r"(?P<section>[0-9A-Za-z\-]+)\s*\)",
    re.IGNORECASE,
)

_DETAIL_UL_RE = re.compile(
    r'<ul[^>]*class="[^"]*statutes-detail[^"]*"[^>]*>(?P<inner>.*?)</ul>',
    re.DOTALL | re.IGNORECASE,
)

_HEADING_RE = re.compile(
    r"<p[^>]*>\s*(?:<b>\s*)?§+\s*[0-9A-Za-z\-]+\s*\.\s*"
    r"(?P<heading>[^<]*)"
    r"(?:</b>\s*)?</p>",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VTSectionRef:
    """Handle for one VSA section — title + chapter + section token."""

    title: str
    chapter: str
    section: str


class VSAStatutesScraper(Scraper[VTSectionRef]):
    """Scrape the Vermont Statutes Annotated.

    Per-section URL walker using :class:`VTSectionRef`.
    """

    jurisdiction = "us-vt"
    doc_type = "statute"
    authority_code = "V.S.A."
    author_id = "vt-legislature"
    author_name = "Vermont General Assembly"
    author_url = "https://legislature.vermont.gov"
    workers = 6

    def list_sections(self) -> Iterable[VTSectionRef]:
        for title in _list_titles():
            for chapter in _list_chapters(title):
                for section in _list_section_tokens(title, chapter):
                    yield VTSectionRef(title, chapter, section)

    def parse_section(self, ref: VTSectionRef) -> SourceSection | None:
        url = f"{BASE}/statutes/section/{ref.title}/{ref.chapter}/{ref.section}"
        res = http_get(url)
        if res is None:
            return None
        parsed = parse_section_page(res.text())
        if parsed is None:
            return None
        title_from_cite, section_num, heading, body = parsed
        if not body:
            return None
        # Strip leading zeros from section for the canonical citation.
        canonical_section = section_num.lstrip("0") or section_num
        work_number = f"{title_from_cite}-{canonical_section}"
        return SourceSection(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=work_number,
            citation=f"{title_from_cite} V.S.A. \u00a7 {canonical_section}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: SourceSection) -> Path:
        """``us-vt/statute/ch-{title}/ch-{title}-sec-{section}.txt``."""
        title = section.work_number.split("-", 1)[0]
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"ch-{title}",
            f"ch-{title}-sec-{safe_section}.txt",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def parse_section_page(html: str) -> tuple[str, str, str, str] | None:
    """Return ``(title, section, heading, body)`` or ``None`` if not a section.

    Returns ``None`` when the page has no ``(Cite as: N V.S.A. § X)``
    marker (navigation/error pages) or no ``statutes-detail`` ``<ul>``
    block (repealed stubs served as title-only pages).
    """
    cite = _CITE_RE.search(html)
    if not cite:
        return None
    title = cite.group("title")
    section = cite.group("section")

    m = _DETAIL_UL_RE.search(html)
    if not m:
        return None
    inner = m.group("inner")

    heading = ""
    head_m = _HEADING_RE.search(inner)
    if head_m:
        heading = _clean_vt(head_m.group("heading")).rstrip(". ").strip()
        inner = inner[: head_m.start()] + inner[head_m.end() :]

    body = _clean_vt(inner).strip().strip("—–-").strip()
    return (title, section, heading, body)


def extract_title_tokens(html: str) -> list[str]:
    """Return title tokens from the statutes root page."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _TITLE_LINK_RE.finditer(html):
        tok = m.group("title")
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def extract_chapter_tokens(html: str, title: str) -> list[str]:
    """Return chapter tokens linked from a title page."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _CHAPTER_LINK_RE.finditer(html):
        if m.group("title") != title:
            continue
        tok = m.group("chapter")
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def extract_section_tokens(html: str, title: str, chapter: str) -> list[str]:
    """Return section tokens linked from a chapter page."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _SECTION_LINK_RE.finditer(html):
        if m.group("title") != title or m.group("chapter") != chapter:
            continue
        tok = m.group("section")
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _clean_vt(s: str) -> str:
    """VT-specific cleaning preserving paragraph breaks as blank lines.

    Unlike the shared :func:`clean_text`, this treats ``</p>``/``</li>``
    as paragraph separators (two newlines) so the body splits correctly
    into separate ``<p>`` entries downstream.
    """
    import html as _stdhtml

    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</(p|li|tr|div)>", "\n\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</(td|span)>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = _stdhtml.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _list_titles() -> list[str]:
    res = http_get(ROOT)
    if res is None:
        return []
    return extract_title_tokens(res.text())


def _list_chapters(title: str) -> list[str]:
    res = http_get(f"{BASE}/statutes/title/{title}")
    if res is None:
        return []
    return extract_chapter_tokens(res.text(), title)


def _list_section_tokens(title: str, chapter: str) -> list[str]:
    res = http_get(f"{BASE}/statutes/chapter/{title}/{chapter}")
    if res is None:
        return []
    return extract_section_tokens(res.text(), title, chapter)
